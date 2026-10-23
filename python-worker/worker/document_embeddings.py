"""DocMind-AI Phase 2.5: local dense embeddings for page-aware chunks.

Model: multilingual E5-small (ONNX, quantized). The model is lazily loaded.
No network calls or model downloads occur until load_embedding_model() is used.
No MySQL, RustFS, Redis, or Qdrant writes occur in this module.

The chunking stage currently uses CHARACTERS rather than model tokens. E5 has
up to 512 input tokens; production ingestion MUST add tokenizer-aware limits
before considering arbitrarily long/multilingual documents safe to index.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import islice
from math import fsum, isfinite, sqrt
from typing import Iterable, Iterator, Protocol, Sequence

from .document_chunking import DocumentChunk

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_FILE = "onnx/model_O4.onnx"  # Smaller ONNX export in the official repository.
VECTOR_SIZE = 384
MODEL_TOKEN_LIMIT = 512
DEFAULT_BATCH_SIZE = 8
MAX_DIAGNOSTIC_CHARS = 1000


class EmbeddingValidationError(ValueError):
    """A document chunk or embedding result failed a safety check."""


class EmbeddingModel(Protocol):
    def embed(self, documents: Sequence[str], *, batch_size: int = ...): ...


@dataclass(frozen=True)
class ChunkEmbedding:
    chunk: DocumentChunk
    vector: tuple[float, ...]


def _normalize_vector(values) -> tuple[float, ...]:
    """Reject malformed vectors and normalize them for cosine similarity."""
    try:
        vector = tuple(float(v) for v in values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise EmbeddingValidationError("Embedding values must be numeric") from exc
    if len(vector) != VECTOR_SIZE:
        raise EmbeddingValidationError(
            f"Expected {VECTOR_SIZE} embedding dimensions; got {len(vector)}"
        )
    if not all(isfinite(v) for v in vector):
        raise EmbeddingValidationError("Embedding has non-finite values")
    try:
        length = sqrt(fsum(v * v for v in vector))
    except (OverflowError, ValueError) as exc:
        raise EmbeddingValidationError("Embedding vector magnitude overflowed") from exc
    if not isfinite(length) or length <= 0.0:
        raise EmbeddingValidationError("Embedding vector has invalid magnitude")
    return tuple(v / length for v in vector)


def _validate_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise EmbeddingValidationError("Cannot embed empty text")
    if len(text) > MAX_DIAGNOSTIC_CHARS:
        raise EmbeddingValidationError(
            f"Diagnostic text exceeds {MAX_DIAGNOSTIC_CHARS} characters; "
            "re-chunk it before embedding"
        )
    return text


@lru_cache(maxsize=1)
def load_embedding_model():
    """Load/download the CPU embedding model once per Python process.

    Uses the official quantized ONNX export. Downloads model files on the
    FIRST invocation only and keeps them in the standard FastEmbed cache.
    """
    from fastembed import TextEmbedding

    known_models = {entry["model"] for entry in TextEmbedding.list_supported_models()}
    if MODEL_NAME not in known_models:
        from fastembed.common.model_description import ModelSource, PoolingType
        TextEmbedding.add_custom_model(
            model=MODEL_NAME,
            pooling=PoolingType.MEAN,
            normalization=True,
            sources=ModelSource(hf=MODEL_NAME),
            dim=VECTOR_SIZE,
            model_file=MODEL_FILE,
        )
    return TextEmbedding(model_name=MODEL_NAME)


def embed_query(model: EmbeddingModel, query: str) -> tuple[float, ...]:
    """Prepare query embeddings for a later retrieval milestone; no DB writes."""
    cleaned = _validate_text(query).strip()
    rows = list(model.embed(["query: " + cleaned], batch_size=1))
    if len(rows) != 1:
        raise EmbeddingValidationError("Embedding model returned an unexpected vector count")
    return _normalize_vector(rows[0])


def iter_chunk_embeddings(
    chunks: Iterable[DocumentChunk],
    model: EmbeddingModel,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Iterator[ChunkEmbedding]:
    """Embed a chunk stream in bounded batches, preserving ID/order/page metadata.

    The function never buffers more than one batch of vectors in memory.
    It does not log or return document text separately from the input chunks.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 64:
        raise ValueError("batch_size must be an integer between 1 and 64")

    source = iter(chunks)
    while batch := list(islice(source, batch_size)):
        for chunk in batch:
            if not isinstance(chunk, DocumentChunk):
                raise EmbeddingValidationError("Expected DocumentChunk inputs")
            _validate_text(chunk.text)
        documents = ["passage: " + chunk.text for chunk in batch]
        vectors = list(model.embed(documents, batch_size=len(batch)))
        if len(vectors) != len(batch):
            raise EmbeddingValidationError(
                f"Embedding model returned {len(vectors)} vectors for {len(batch)} chunks"
            )
        for chunk, vector in zip(batch, vectors):
            yield ChunkEmbedding(chunk=chunk, vector=_normalize_vector(vector))
