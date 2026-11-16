"""Phase 4.2C: read-only, page-aware PDF and structural DOCX extraction.

Design for future hierarchical indexing. Never changes MySQL, RustFS or Qdrant.
PDF block coordinates are source page coordinates in points; DOCX has NO
verified rendered page numbers, so uses stable paragraph/table row locators.

No OCR is implied. Empty/short PDF native text with visual content is flagged;
callers must complete OCR before declaring a document fully indexed. DOCX uses
python-docx (a whole-document parser), so very large DOCX memory cost remains
an explicit later benchmark/optimization task.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterator
from uuid import NAMESPACE_URL, uuid5
import re

import pymupdf
from docx import Document as OpenDocx
from docx.table import Table
from docx.text.paragraph import Paragraph

from .document_extraction import (
    DocumentExtractionError, ExtractedSegment, MAX_PDF_PAGES, _check_docx_archive,
)
from .docx_table_rows import label_docx_table_rows

MAX_BLOCKS_PER_PAGE = 4000
MAX_BLOCK_CHARS = 200000
MAX_DOCX_BLOCKS = 250000
MAX_HEADING_DEPTH = 9
MIN_PDF_NATIVE_TEXT = 20
_HEADING = re.compile(r"^heading[ -]?([1-9])$", re.IGNORECASE)


@dataclass(frozen=True)
class SourceBlock:
    """Deterministic, version-scoped source locator and structure."""
    block_id: str
    document_version_id: str
    ordinal: int
    text: str
    kind: str
    page_number: int | None
    bbox: tuple[float, float, float, float] | None
    section_path: tuple[str, ...]
    locator: str  # pdf:p0012:b0003 or docx:paragraph:000012, etc.
    table_id: str | None = None
    table_row: int | None = None


@dataclass(frozen=True)
class PdfPageStructure:
    page_number: int
    blocks: tuple[SourceBlock, ...]
    needs_ocr: bool
    has_visual_content: bool


def _valid_version(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("A document version ID is required (<=128 characters)")


def _block(version: str, ordinal: int, text: str, kind: str, page: int | None,
           bbox: tuple[float, float, float, float] | None,
           section: tuple[str, ...], locator: str, *,
           table_id: str | None = None, table_row: int | None = None) -> SourceBlock:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_BLOCK_CHARS:
        raise DocumentExtractionError("Source block is empty or exceeds size limit")
    digest = sha256(text.encode("utf-8")).hexdigest()
    identifier = str(uuid5(NAMESPACE_URL,
        f"docmind:sourceblock:v1:{version}:{locator}:{kind}:{digest}"))
    return SourceBlock(identifier, version, ordinal, text, kind, page,
                       bbox, section, locator, table_id, table_row)


def iter_pdf_page_structures(path: Path | str, document_version_id: str, *,
                             first_page: int = 1,
                             last_page: int | None = None) -> Iterator[PdfPageStructure]:
    """Extract bounded page/block geometry; 1-based PDF coordinates preserved.

    Uses native text blocks only. Visual/scan detection is a conservative
    heuristic; a page with substantial corrupt OCR text may still look native.
    Does not silently flatten image-only pages into empty searchable text.
    """
    _valid_version(document_version_id)
    if type(first_page) is not int or first_page < 1:
        raise ValueError("Invalid start page")
    if last_page is not None and (type(last_page) is not int or last_page < first_page):
        raise ValueError("Invalid end page")
    try:
        with pymupdf.open(str(Path(path))) as pdf:
            if not pdf.is_pdf or pdf.needs_pass:
                raise DocumentExtractionError("Invalid or password-protected PDF")
            count = pdf.page_count
            if not 1 <= count <= MAX_PDF_PAGES:
                raise DocumentExtractionError("PDF page count is outside supported limits")
            last = count if last_page is None else last_page
            if last > count or first_page > count:
                raise DocumentExtractionError("PDF page range exceeds document")
            for number in range(first_page, last + 1):
                page = pdf.load_page(number - 1)
                raw_blocks = page.get_text("blocks", sort=True)
                if len(raw_blocks) > MAX_BLOCKS_PER_PAGE:
                    raise DocumentExtractionError("PDF page has too many layout blocks")
                blocks = []
                for index, item in enumerate(raw_blocks):
                    # PyMuPDF blocks: x0,y0,x1,y1,text,block_no,block_type
                    if len(item) < 7 or item[6] != 0:
                        continue
                    text = str(item[4]).strip()
                    if not text:
                        continue
                    box = tuple(round(float(v), 3) for v in item[:4])
                    if not (box[0] <= box[2] and box[1] <= box[3]):
                        raise DocumentExtractionError("Malformed PDF block coordinates")
                    blocks.append(_block(
                        document_version_id, index, text, "pdf_text", number,
                        box, (), f"pdf:p{number:05d}:b{index:05d}"))
                chars = sum(len(b.text) for b in blocks)
                images = bool(page.get_image_info())
                # Vector paths can be diagrams, not necessarily scans.
                vectors = bool(page.get_drawings()) if chars < MIN_PDF_NATIVE_TEXT else False
                visual = images or vectors
                yield PdfPageStructure(number, tuple(blocks),
                    needs_ocr=chars < MIN_PDF_NATIVE_TEXT and visual,
                    has_visual_content=visual)
    except DocumentExtractionError:
        raise
    except (RuntimeError, ValueError, OSError) as exc:
        raise DocumentExtractionError("Could not extract PDF layout") from exc


def iter_docx_structure(path: Path | str, document_version_id: str) -> Iterator[SourceBlock]:
    """Preserve DOCX headings, list/paragraph kind and table row context.

    DOCX page-number claims would be false without a renderer. Section paths
    remain stable and nearest ancestor headings are attached to each block.
    """
    _valid_version(document_version_id)
    path = Path(path)
    _check_docx_archive(path)
    try:
        doc = OpenDocx(str(path))
        headings: list[str] = []
        ordinal = 0
        table_number = 0
        paragraph_number = 0
        for item in doc.iter_inner_content():
            if isinstance(item, Table):
                table_number += 1
                table_id = f"docx:table:{table_number:06d}"
                rows = ([cell.text for cell in row.cells] for row in item.rows)
                for row_number, value in enumerate(label_docx_table_rows(rows), 1):
                    if ordinal >= MAX_DOCX_BLOCKS:
                        raise DocumentExtractionError("DOCX contains too many structural blocks")
                    locator = f"{table_id}:row:{row_number:06d}"
                    yield _block(document_version_id, ordinal, value,
                                 "docx_table_row", None, None, tuple(headings),
                                 locator, table_id=table_id, table_row=row_number)
                    ordinal += 1
                continue
            if not isinstance(item, Paragraph):
                raise DocumentExtractionError("Unknown DOCX block type")
            paragraph_number += 1
            value = item.text.strip()
            if not value:
                continue
            if ordinal >= MAX_DOCX_BLOCKS:
                raise DocumentExtractionError("DOCX contains too many structural blocks")
            style = (item.style.name or "") if item.style is not None else ""
            heading_match = _HEADING.fullmatch(style.strip())
            if heading_match:
                depth = int(heading_match.group(1))
                if depth > MAX_HEADING_DEPTH:
                    raise DocumentExtractionError("DOCX heading depth too large")
                headings = headings[:depth - 1]
                # Missing intermediate styles are allowed; don't invent their labels.
                headings.append(value)
                kind = "docx_heading"
            elif style.lower().startswith("list"):
                kind = "docx_list"
            else:
                kind = "docx_paragraph"
            yield _block(document_version_id, ordinal, value, kind, None, None,
                         tuple(headings), f"docx:paragraph:{paragraph_number:06d}")
            ordinal += 1
    except DocumentExtractionError:
        raise
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        raise DocumentExtractionError("Could not extract DOCX structure") from exc


def iter_layout_segments(pages: Iterator[PdfPageStructure]) -> Iterator[ExtractedSegment]:
    """Adapter for the legacy chunker; never claims OCR was completed.

    Retains PDF page citation and conservatively flags pages needing OCR.
    Fine-grained geometry must be persisted separately by future index v2.
    """
    for page in pages:
        if not isinstance(page, PdfPageStructure):
            raise TypeError("PdfPageStructure required")
        yield ExtractedSegment(
            text="\n\n".join(b.text for b in page.blocks),
            kind="pdf_page", page_number=page.page_number, needs_ocr=page.needs_ocr)


def iter_structured_docx_segments(path: Path | str,
                                  document_version_id: str) -> Iterator[ExtractedSegment]:
    """Opt-in searchable DOCX content with semantic parent-heading context.

    This is still the legacy chunker adapter: detailed paragraph/table locators
    remain in SourceBlock and are NOT yet persisted in Qdrant v1. Never invent
    DOCX page numbers. Truncate neither the passage nor its source heading.
    """
    for block in iter_docx_structure(path, document_version_id):
        if block.kind == "docx_heading":
            text = block.text
        else:
            prefix = " > ".join(block.section_path)
            if len(prefix) > 4096:
                raise DocumentExtractionError("DOCX section path too large")
            text = f"Section: {prefix}\n{block.text}" if prefix else block.text
        kind = ("docx_table_row" if block.kind == "docx_table_row"
                else "docx_paragraph")
        yield ExtractedSegment(text, kind, page_number=None, needs_ocr=False)
