"""Deterministic, page-aware text chunking for DocMind-AI Phase 2.4.

Operates on ExtractedSegment streams without database or vector writes.
- PDF page boundaries are never crossed.
- DOCX paragraphs/table rows are packed in order; TXT blocks are concatenated.
- Chunk IDs are stable UUID5 strings scoped to the document version.
- Source segment ranges and 1-based PDF page numbers are retained.

Windows use character counts, NOT embedding-model token counts. Revisit chunk
sizes after choosing the embedding model. OCR candidates are skipped until OCR
is available; never mark a partly scanned document READY merely from this step.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Iterator
from uuid import NAMESPACE_URL, uuid5
import unicodedata

from .document_extraction import ExtractedSegment


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_version_id: str
    chunk_index: int
    text: str
    page_number: int | None
    source_start_segment: int  # zero-based extracted segment index
    source_end_segment: int    # inclusive
    source_kind: str          # pdf_page / docx / txt
    group_char_start: int     # offsets in the normalized, joined source group
    group_char_end: int


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    index: int


def _normalize(text: str) -> str:
    """Preserve paragraphs, normalize Unicode and Windows line endings."""
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def _boundary(text: str, start: int, limit: int, max_chars: int) -> int:
    if limit == len(text):
        return limit
    lower = start + max_chars // 2
    # Break near a paragraph, line, sentence, then word when possible.
    for delim in ("\n\n", "\n", ". ", "! ", "? ", "; ", " "):
        pos = text.rfind(delim, lower, limit)
        if pos >= lower:
            end = pos + len(delim)
            if end > start:
                return end
    return limit


def iter_document_chunks(
    segments: Iterable[ExtractedSegment],
    document_version_id: str,
    *,
    max_chars: int = 1000,
    overlap_chars: int = 150,
) -> Iterator[DocumentChunk]:
    """Create bounded, source-aware chunks without retaining the whole document.

    Each group is at most roughly 8 * max_chars of source text, except an
    individual extracted segment, which may itself be larger (e.g. one PDF page).
    For PDFs, groups never span page boundaries. Blank or OCR-needed segments
    produce no chunks, so OCR must be handled separately before completion.
    """
    if not isinstance(document_version_id, str) or not document_version_id.strip():
        raise ValueError("document_version_id is required")
    if type(max_chars) is not int or not 200 <= max_chars <= 8000:
        raise ValueError("max_chars must be an integer between 200 and 8000")
    if type(overlap_chars) is not int or not 0 <= overlap_chars <= max_chars // 3:
        raise ValueError("overlap_chars must be 0 to max_chars // 3")

    current_key: tuple[str, int | None] | None = None
    pieces: list[tuple[int, str]] = []
    next_index = 0
    size = 0

    def flush() -> Iterator[DocumentChunk]:
        nonlocal next_index, pieces, size
        if not pieces:
            return
        assert current_key is not None
        source_kind, page = current_key
        separator = "\n\n" if source_kind == "docx" else ""
        spans: list[_Span] = []
        parts: list[str] = []
        position = 0
        for i, text in pieces:
            if parts:
                parts.append(separator)
                position += len(separator)
            spans.append(_Span(position, position + len(text), i))
            parts.append(text)
            position += len(text)
        merged = "".join(parts)
        n = len(merged)
        start = 0
        while start < n:
            end = _boundary(merged, start, min(start + max_chars, n), max_chars)
            raw = merged[start:end]
            value = raw.strip()
            actual_start = start + (len(raw) - len(raw.lstrip()))
            actual_end = end - (len(raw) - len(raw.rstrip()))
            matching = [s.index for s in spans if s.start < actual_end and s.end > actual_start]
            if value and matching:
                digest = sha256(value.encode("utf-8")).hexdigest()
                point_id = str(uuid5(
                    NAMESPACE_URL,
                    f"docmind:chunk:v1:{document_version_id}:{next_index}:{digest}",
                ))
                yield DocumentChunk(
                    chunk_id=point_id,
                    document_version_id=document_version_id,
                    chunk_index=next_index,
                    text=value,
                    page_number=page,
                    source_start_segment=min(matching),
                    source_end_segment=max(matching),
                    source_kind=source_kind,
                    group_char_start=actual_start,
                    group_char_end=actual_end,
                )
                next_index += 1
            if end == n:
                break
            start = max(start + 1, end - overlap_chars)
        pieces = []
        size = 0

    for segment_index, segment in enumerate(segments):
        if not isinstance(segment, ExtractedSegment):
            raise TypeError("Expected ExtractedSegment objects")
        if segment.needs_ocr:
            # This page must be OCR'd later; don't index incomplete text.
            continue
        text = _normalize(segment.text)
        if not text.strip():
            continue
        if segment.kind == "pdf_page":
            if segment.page_number is None or segment.page_number < 1:
                raise ValueError("PDF page segment requires a 1-based page number")
            key = ("pdf_page", segment.page_number)
        elif segment.kind.startswith("docx_"):
            key = ("docx", None)
        elif segment.kind == "txt_text":
            key = ("txt", None)
        else:
            raise ValueError(f"Unsupported extracted segment kind: {segment.kind}")

        if current_key is not None and (key != current_key or size + len(text) > max_chars * 8):
            yield from flush()
        current_key = key
        pieces.append((segment_index, text))
        size += len(text) + (2 if key[0] == "docx" and len(pieces) > 1 else 0)

    yield from flush()
