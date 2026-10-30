"""Token-budgeted, source-aware DocMind-AI chunks (Phase 2.7A).

Wrap the existing character-based document chunks with the EXACT E5 tokenizer.
No network calls or database/vector writes are performed here. The caller
supplies a tokenizer. Chunk IDs use their own v2 namespace; never mix them
with v1 diagnostic points. OCR candidates remain excluded upstream.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Iterable, Iterator
from uuid import NAMESPACE_URL, uuid5

from .document_chunking import DocumentChunk

SAFE_TOKEN_BUDGET = 480  # Includes "passage: " and special tokens; E5 limit 512.
DEFAULT_SUBCHUNK_OVERLAP = 80  # Unicode code points, bounded to 1/4 of window.


def _count_tokens(tokenizer, text: str) -> int:
    # Hugging Face tokenizers.Tokenizer.encode adds model special tokens by default.
    encoded = tokenizer.encode("passage: " + text)
    if not hasattr(encoded, "ids"):
        raise TypeError("Tokenizer.encode must return an Encoding with .ids")
    return len(encoded.ids)


def _largest_safe_end(tokenizer, text: str, start: int, budget: int) -> int:
    """Locate a bounded piece without assuming a fixed tokens/characters ratio."""
    total = len(text)
    if _count_tokens(tokenizer, text[start:]) <= budget:
        return total

    lo, hi = start + 1, total
    best = start
    while lo <= hi:
        mid = (lo + hi) // 2
        if _count_tokens(tokenizer, text[start:mid]) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best == start:
        raise ValueError("One source character exceeds the token budget")

    # Prefer a natural boundary near the end, but don't discard punctuation
    # or whitespace: subsequent windows must cover every source character.
    lower = start + max(1, int((best - start) * 0.70))
    if lower < best:
        for boundary in ("\n\n", "\n", ". ", "! ", "? ", "; ", " "):
            pos = text.rfind(boundary, lower, best)
            if pos >= lower:
                candidate = pos + len(boundary)
                if candidate > start and _count_tokens(tokenizer, text[start:candidate]) <= budget:
                    return candidate
    return best


def iter_token_safe_chunks(
    chunks: Iterable[DocumentChunk],
    tokenizer,
    *,
    max_tokens: int = SAFE_TOKEN_BUDGET,
    overlap_chars: int = DEFAULT_SUBCHUNK_OVERLAP,
) -> Iterator[DocumentChunk]:
    """Stream token-safe chunks with stable UUIDs and preserved page metadata.

    Emitted intervals cover every non-whitespace source character; whitespace-only
    windows are skipped. Adjacent pieces may overlap. IDs and indices are
    deterministically, scoped to the document version and originating chunk.
    """
    if type(max_tokens) is not int or not 16 <= max_tokens <= SAFE_TOKEN_BUDGET:
        raise ValueError("max_tokens must be an integer between 16 and 480")
    if type(overlap_chars) is not int or not 0 <= overlap_chars <= 256:
        raise ValueError("overlap_chars must be an integer between 0 and 256")
    if not callable(getattr(tokenizer, "encode", None)):
        raise TypeError("tokenizer must provide .encode(text)")

    next_index = 0
    for original in chunks:
        if not isinstance(original, DocumentChunk):
            raise TypeError("Expected DocumentChunk objects")
        text = original.text
        if not text or not text.strip():
            raise ValueError("Cannot split an empty source chunk")
        start = 0
        part = 0
        while start < len(text):
            end = _largest_safe_end(tokenizer, text, start, max_tokens)
            piece = text[start:end]
            if not piece.strip():
                # Long runs of whitespace need no embedding. Skip only the
                # whitespace run; every non-whitespace source character is
                # still included in some emitted subchunk.
                start = end
                continue
            if _count_tokens(tokenizer, piece) > max_tokens:
                raise ValueError("Token count exceeds the allowed budget")
            digest = sha256(piece.encode("utf-8")).hexdigest()
            chunk_id = str(uuid5(
                NAMESPACE_URL,
                f"docmind:tokenchunk:v2:{original.document_version_id}:"
                f"{original.chunk_id}:{part}:{digest}",
            ))
            yield DocumentChunk(
                chunk_id=chunk_id,
                document_version_id=original.document_version_id,
                chunk_index=next_index,
                text=piece,
                page_number=original.page_number,
                source_start_segment=original.source_start_segment,
                source_end_segment=original.source_end_segment,
                source_kind=original.source_kind,
                group_char_start=original.group_char_start + start,
                group_char_end=original.group_char_start + end,
            )
            next_index += 1
            part += 1
            if end >= len(text):
                break
            # Limit overlap to <=25% of the last window to ensure progress,
            # even for unusually high tokens/character ratios.
            effective_overlap = min(overlap_chars, (end - start) // 4)
            start = end - effective_overlap
