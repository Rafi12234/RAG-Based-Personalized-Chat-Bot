"""DocMind-AI Phase 2.7B: bounded, OCR-aware PDF segment streaming.

Separate from the original PDF extractor. Preserve existing page-aware chunking
by always returning ExtractedSegment(kind='pdf_page'). Never write to MySQL,
Redis, Qdrant, or persistent storage. OCR is performed only on pages with
little native text AND raster/vector visual content. This is a conservative
heuristic, not a guarantee that every unreadable page is identified.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Callable, Iterator

import pymupdf

from .document_extraction import (
    DocumentExtractionError,
    ExtractedSegment,
    MAX_PDF_PAGES,
)


DEFAULT_OCR_DPI = 180
DEFAULT_OCR_MAX_PAGES = 10  # CLI diagnostic budget, NOT a production quota.
NATIVE_TEXT_THRESHOLD = 20
LANGUAGE_PATTERN = re.compile(r"^[a-z]{3}(?:\+[a-z]{3})*$")


class OCRExtractionError(DocumentExtractionError):
    """An OCR candidate could not be safely recognized."""


def resolve_tessdata(language: str, tessdata: str | Path | None = None) -> Path:
    """Find Tesseract language-data files and validate every requested language.

    Explicit `tessdata` > `TESSDATA_PREFIX` > standard Windows/Linux locations.
    This does not install a language pack or change environment variables.
    """
    if not isinstance(language, str) or not LANGUAGE_PATTERN.fullmatch(language):
        raise ValueError("OCR language must resemble eng or eng+ben")
    if tessdata is not None:
        paths = [Path(tessdata)]
    elif os.environ.get("TESSDATA_PREFIX"):
        paths = [Path(os.environ["TESSDATA_PREFIX"])]
    else:
        paths = [
            Path("C:/Program Files/Tesseract-OCR/tessdata"),
            Path("C:/Program Files (x86)/Tesseract-OCR/tessdata"),
            Path("/usr/share/tesseract-ocr/5/tessdata"),
            Path("/usr/share/tesseract-ocr/4.00/tessdata"),
            Path("/usr/share/tessdata"),
        ]
    path = next((p for p in paths if p.is_dir()), None)
    if path is None:
        raise OCRExtractionError(
            "Tesseract tessdata directory not found. Set TESSDATA_PREFIX to its tessdata folder."
        )
    missing = [code for code in language.split("+") if not (path / f"{code}.traineddata").is_file()]
    if missing:
        raise OCRExtractionError("Missing Tesseract language data: " + ", ".join(missing))
    return path


def _page_has_visual_content(page) -> bool:
    """Recognize raster/inline image pages and vector-only scanned facsimiles."""
    info = getattr(page, "get_image_info", None)
    if callable(info) and info():
        return True
    drawings = getattr(page, "get_drawings", None)
    if callable(drawings) and drawings():
        return True
    return False


def iter_pdf_segments_with_ocr(
    path: Path | str,
    *,
    language: str = "eng",
    dpi: int = DEFAULT_OCR_DPI,
    tessdata: str | Path | None = None,
    max_ocr_pages: int = DEFAULT_OCR_MAX_PAGES,
    on_ocr: Callable[[int], None] | None = None,
    on_blank: Callable[[int], None] | None = None,
) -> Iterator[ExtractedSegment]:
    """Yield page-ordered segments with OCR for suspected image-only pages.

    A page with >=20 native text characters is kept verbatim. A short native
    text page without visual content is also kept (fixes false OCR on titles).
    Blank pages yield an empty non-OCR segment; unrecognizable visual pages
    fail closed rather than permitting silent partial indexing.

    This is an *operator diagnostic*. The production task will need execution
    leases, better scan detection, OCR resource quotas and retry policy.
    """
    if not isinstance(language, str) or not LANGUAGE_PATTERN.fullmatch(language):
        raise ValueError("OCR language must resemble eng or eng+ben")
    if type(dpi) is not int or not 96 <= dpi <= 300:
        raise ValueError("OCR DPI must be an integer between 96 and 300")
    if type(max_ocr_pages) is not int or not 1 <= max_ocr_pages <= MAX_PDF_PAGES:
        raise ValueError("max_ocr_pages must be between 1 and MAX_PDF_PAGES")
    if on_ocr is not None and not callable(on_ocr):
        raise TypeError("on_ocr must be a callback")
    if on_blank is not None and not callable(on_blank):
        raise TypeError("on_blank must be a callback")
    candidate = Path(path)
    if candidate.suffix.lower() != ".pdf":
        raise DocumentExtractionError("OCR is only supported for PDF files")

    ocr_count = 0
    try:
        with pymupdf.open(str(candidate)) as pdf:
            if not pdf.is_pdf:
                raise DocumentExtractionError("Document is not a PDF")
            if pdf.needs_pass:
                raise DocumentExtractionError("Password-protected PDF is not supported")
            if pdf.page_count > MAX_PDF_PAGES:
                raise DocumentExtractionError("PDF page limit exceeded")

            for number, page in enumerate(pdf, start=1):
                native = page.get_text("text", sort=True)
                if len(native.strip()) >= NATIVE_TEXT_THRESHOLD:
                    yield ExtractedSegment(native, "pdf_page", page_number=number, needs_ocr=False)
                    continue

                if not _page_has_visual_content(page):
                    if not native.strip() and on_blank is not None:
                        on_blank(number)
                    yield ExtractedSegment(native, "pdf_page", page_number=number, needs_ocr=False)
                    continue

                ocr_count += 1
                if ocr_count > max_ocr_pages:
                    raise OCRExtractionError("OCR page budget exceeded; retry with an explicit larger budget")
                data_dir = resolve_tessdata(language, tessdata)
                try:
                    tp = page.get_textpage_ocr(
                        language=language, dpi=dpi, full=True, tessdata=str(data_dir)
                    )
                    recognized = page.get_text("text", textpage=tp, sort=True)
                except (RuntimeError, ValueError, OSError) as exc:
                    raise OCRExtractionError(f"OCR engine failed for page {number}") from exc
                if not recognized.strip():
                    raise OCRExtractionError(f"Visual page {number} produced no OCR text")
                if on_ocr is not None:
                    on_ocr(number)
                yield ExtractedSegment(recognized, "pdf_page", page_number=number, needs_ocr=False)
    except (DocumentExtractionError, OCRExtractionError):
        raise
    except (RuntimeError, ValueError, OSError) as exc:
        raise DocumentExtractionError("Could not read PDF with OCR") from exc
