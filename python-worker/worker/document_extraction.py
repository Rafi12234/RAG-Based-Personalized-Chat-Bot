"""Read-only text extraction for PDF, DOCX and UTF-8 TXT documents.

This module never modifies a database or index. Consumers can iterate over
segments without holding an entire 1000-page document's text in memory.
OCR, embeddings and vector writes are intentionally NOT implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
import zipfile

import pymupdf
from docx import Document as OpenDocx
from docx.table import Table

MAX_PDF_PAGES = 5000
MAX_ZIP_ENTRIES = 10000
MAX_DOCX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
TXT_READ_SIZE = 16 * 1024


class DocumentExtractionError(Exception):
    """An uploaded document cannot be safely extracted."""


@dataclass(frozen=True)
class ExtractedSegment:
    text: str
    kind: str
    page_number: int | None = None  # PDF numbers are 1-based.
    needs_ocr: bool = False


@dataclass(frozen=True)
class ExtractionSummary:
    filename: str
    format: str
    segments: int
    characters: int
    pages: int | None
    potential_ocr_pages: tuple[int, ...]


def _pdf_segments(path: Path) -> Iterator[ExtractedSegment]:
    try:
        with pymupdf.open(str(path)) as pdf:
            if not pdf.is_pdf:
                raise DocumentExtractionError("File content is not a PDF")
            if pdf.needs_pass:
                raise DocumentExtractionError("Password-protected PDF is not supported")
            if pdf.page_count > MAX_PDF_PAGES:
                raise DocumentExtractionError("PDF page limit exceeded")
            for index, page in enumerate(pdf):
                text = page.get_text("text", sort=True)
                # A short/empty page is only a candidate for OCR, not proof.
                candidate = len(text.strip()) < 20
                yield ExtractedSegment(
                    text=text,
                    kind="pdf_page",
                    page_number=index + 1,
                    needs_ocr=candidate,
                )
    except DocumentExtractionError:
        raise
    except (RuntimeError, ValueError, OSError) as exc:
        raise DocumentExtractionError("Could not read PDF") from exc


def _check_docx_archive(path: Path) -> None:
    """Bound ZIP metadata before the DOCX library expands the package."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise DocumentExtractionError("DOCX contains too many archive entries")
            total = sum(entry.file_size for entry in infos)
            if total > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise DocumentExtractionError("DOCX expanded size limit exceeded")
            if "word/document.xml" not in archive.namelist():
                raise DocumentExtractionError("DOCX is missing its document content")
    except DocumentExtractionError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise DocumentExtractionError("Invalid DOCX archive") from exc


def _docx_segments(path: Path) -> Iterator[ExtractedSegment]:
    _check_docx_archive(path)
    try:
        document = OpenDocx(str(path))
        for item in document.iter_inner_content():
            if isinstance(item, Table):
                for row in item.rows:
                    text = " | ".join(cell.text.strip() for cell in row.cells).strip()
                    if text:
                        yield ExtractedSegment(text=text, kind="docx_table_row")
            else:
                text = item.text.strip()
                if text:
                    yield ExtractedSegment(text=text, kind="docx_paragraph")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise DocumentExtractionError("Could not read DOCX") from exc


def _txt_segments(path: Path) -> Iterator[ExtractedSegment]:
    try:
        with path.open("r", encoding="utf-8-sig", errors="strict") as handle:
            while chunk := handle.read(TXT_READ_SIZE):
                if "\x00" in chunk:
                    raise DocumentExtractionError("TXT contains binary NUL characters")
                yield ExtractedSegment(text=chunk, kind="txt_text")
    except UnicodeError as exc:
        raise DocumentExtractionError("TXT is not valid UTF-8") from exc
    except OSError as exc:
        raise DocumentExtractionError("Could not read TXT") from exc


def iter_extracted_segments(path: Path | str, filename: str) -> Iterator[ExtractedSegment]:
    path = Path(path)
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        yield from _pdf_segments(path)
    elif ext == ".docx":
        yield from _docx_segments(path)
    elif ext == ".txt":
        yield from _txt_segments(path)
    else:
        raise DocumentExtractionError("Only PDF, DOCX and TXT are supported")


def summarize_extraction(path: Path | str, filename: str) -> ExtractionSummary:
    segments = 0
    characters = 0
    pages = None
    ocr_pages = []
    for segment in iter_extracted_segments(path, filename):
        segments += 1
        characters += len(segment.text)
        if segment.page_number is not None:
            pages = segment.page_number
        if segment.needs_ocr and segment.page_number is not None:
            ocr_pages.append(segment.page_number)
    return ExtractionSummary(
        filename=filename,
        format=Path(filename).suffix.lower().removeprefix("."),
        segments=segments,
        characters=characters,
        pages=pages,
        potential_ocr_pages=tuple(ocr_pages),
    )
