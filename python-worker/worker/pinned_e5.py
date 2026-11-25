"""Phase 4.3D: checksum-pinned, offline CPU E5 reference inference.

Only trusted, operator-approved local ONNX/tokenizer artifacts are accepted.
No downloads, fallback model, datastore access, or activation. The exact
verified bytes (not their paths) are passed to the runtimes. This is a
reference implementation for the isolated v2 path, not a replacement for v1.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Sequence

from .document_embeddings import MODEL_NAME, VECTOR_SIZE, MODEL_TOKEN_LIMIT, _normalize_vector

MAX_MODEL_BYTES = 512 * 1024 * 1024
MAX_TOKENIZER_BYTES = 32 * 1024 * 1024
MAX_BATCH = 32
MAX_TEXT_CHARS = 4096
PROFILE = "e5-cpu-int64-masked-mean-l2-v1"
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")


class ModelPinError(RuntimeError):
    """Model provenance, runtime contract or inference could not be verified."""


@dataclass(frozen=True)
class ModelArtifactPin:
    revision: str
    onnx_sha256: str
    tokenizer_sha256: str
    output_name: str = "last_hidden_state"

    def __post_init__(self) -> None:
        if not isinstance(self.revision, str) or not HEX40.fullmatch(self.revision):
            raise ModelPinError("An immutable model revision is required")
        for value in (self.onnx_sha256, self.tokenizer_sha256):
            if not isinstance(value, str) or not HEX64.fullmatch(value):
                raise ModelPinError("Expected lowercase SHA-256 artifact digests")
        if (not isinstance(self.output_name, str) or
                not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", self.output_name)):
            raise ModelPinError("Invalid pinned ONNX output name")

    @property
    def digest(self) -> str:
        canonical = json.dumps({
            "model": MODEL_NAME, "revision": self.revision,
            "onnx_sha256": self.onnx_sha256,
            "tokenizer_sha256": self.tokenizer_sha256,
            "output_name": self.output_name, "profile": PROFILE,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(canonical).hexdigest()


def _checked_bytes(path: Path, expected: str, maximum: int) -> bytes:
    try:
        if not path.is_file():
            raise ModelPinError("Pinned artifact must be a local regular file")
        with path.open("rb") as stream:
            content = stream.read(maximum + 1)
    except OSError:
        raise ModelPinError("Pinned artifact is unavailable") from None
    if not content or len(content) > maximum:
        raise ModelPinError("Pinned artifact outside size bound")
    if sha256(content).hexdigest() != expected:
        raise ModelPinError("Pinned artifact checksum mismatch")
    return content


class PinnedE5Model:
    """Local E5 adapter for trusted ONNX token-level exports.

    Supports the two standard int64 inputs and optional token_type_ids.
    Rejects truncation instead of silently dropping source evidence.
    Pin equality identifies the artifacts and preprocessing profile, not the
    runtime build, operator's trustworthiness, or numerical parity with v1.
    """

    def __init__(self, pin: ModelArtifactPin, onnx_path: Path, tokenizer_path: Path):
        if type(pin) is not ModelArtifactPin:
            raise TypeError("Trusted model artifact pin required")
        # Verify BOTH blobs before constructing any parser or inference runtime.
        model_bytes = _checked_bytes(Path(onnx_path), pin.onnx_sha256, MAX_MODEL_BYTES)
        tokenizer_bytes = _checked_bytes(
            Path(tokenizer_path), pin.tokenizer_sha256, MAX_TOKENIZER_BYTES)
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            self._tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
            self._tokenizer.no_truncation()
            self._tokenizer.no_padding()
            self._pad_id = self._tokenizer.token_to_id("<pad>")
            if type(self._pad_id) is not int or self._pad_id < 0:
                raise ModelPinError("E5 tokenizer pad token is missing")
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.log_severity_level = 3
            self._session = ort.InferenceSession(
                model_bytes, sess_options=options, providers=["CPUExecutionProvider"])
            if self._session.get_providers() != ["CPUExecutionProvider"]:
                raise ModelPinError("Unexpected inference provider")
            inputs = self._session.get_inputs()
            self._inputs = {item.name for item in inputs}
            required = {"input_ids", "attention_mask"}
            if (len(self._inputs) != len(inputs) or
                    self._inputs not in (required, required | {"token_type_ids"}) or
                    any(item.type != "tensor(int64)" or len(item.shape) != 2
                        for item in inputs)):
                raise ModelPinError("Unsupported E5 ONNX input contract")
            output = [item for item in self._session.get_outputs()
                      if item.name == pin.output_name]
            if (len(output) != 1 or output[0].type != "tensor(float)" or
                    len(output[0].shape) != 3 or output[0].shape[2] != VECTOR_SIZE):
                raise ModelPinError("Expected pinned token-level E5 output")
        except ModelPinError:
            raise
        except Exception:
            raise ModelPinError("Unable to load verified local E5 artifacts") from None
        self._pin = pin

    @property
    def pin(self) -> ModelArtifactPin:
        return self._pin

    def encode(self, text: str):
        """Tokenizer-compatible interface for hierarchical chunk construction."""
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ModelPinError("Embedding text outside safety bound")
        try:
            return self._tokenizer.encode(text, add_special_tokens=True)
        except Exception:
            raise ModelPinError("Pinned tokenization failed") from None

    def embed(self, documents: Sequence[str], *, batch_size: int = 8):
        import numpy as np

        if (type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH or
                not isinstance(documents, (list, tuple)) or
                not 1 <= len(documents) <= batch_size):
            raise ModelPinError("Invalid bounded embedding batch")
        encodings = []
        for document in documents:
            if not isinstance(document, str) or not (
                    document.startswith("passage: ") or document.startswith("query: ")):
                raise ModelPinError("Explicit E5 query or passage prefix required")
            if not document.split(": ", 1)[1].strip():
                raise ModelPinError("Cannot embed empty evidence")
            enc = self.encode(document)
            if not 1 <= len(enc.ids) <= MODEL_TOKEN_LIMIT:
                raise ModelPinError("Embedding would exceed E5 token budget")
            if (len(enc.type_ids) != len(enc.ids) or
                    len(enc.attention_mask) != len(enc.ids) or
                    any(v != 1 for v in enc.attention_mask)):
                raise ModelPinError("Unexpected unpadded tokenizer encoding")
            encodings.append(enc)
        width = max(len(enc.ids) for enc in encodings)
        shape = (len(encodings), width)
        ids = np.full(shape, self._pad_id, dtype=np.int64)
        mask = np.zeros(shape, dtype=np.int64)
        types = np.zeros(shape, dtype=np.int64)
        for row, enc in enumerate(encodings):
            size = len(enc.ids)
            ids[row, :size] = enc.ids
            mask[row, :size] = 1
            types[row, :size] = enc.type_ids
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = types
        try:
            values = self._session.run([self.pin.output_name], feeds)
            if len(values) != 1:
                raise ModelPinError("Unexpected model output count")
            hidden = values[0]
            if (not isinstance(hidden, np.ndarray) or hidden.dtype != np.float32 or
                    hidden.shape != (*shape, VECTOR_SIZE) or
                    not np.isfinite(hidden).all()):
                raise ModelPinError("Invalid E5 token embeddings")
            pooled = (hidden.astype(np.float64) * mask[..., None]).sum(axis=1)
            pooled /= mask.sum(axis=1)[:, None]
            return [_normalize_vector(row) for row in pooled]
        except ModelPinError:
            raise
        except Exception:
            raise ModelPinError("Pinned inference failed") from None
