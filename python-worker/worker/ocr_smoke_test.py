"""Make a temporary image-only PDF and OCR it locally (no MySQL/RustFS/Qdrant).

Run: python -m worker.ocr_smoke_test [--language eng] [--tessdata PATH]
No document is retained. Does not print any extracted source text.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
import pymupdf

from .document_ocr import iter_pdf_segments_with_ocr, resolve_tessdata


def smoke(language="eng", tessdata=None) -> dict:
    data_dir = resolve_tessdata(language, tessdata)
    with tempfile.TemporaryDirectory(prefix="docmind-ocr-smoke-") as folder:
        image_pdf = Path(folder) / "scanned.pdf"
        # First render readable PDF content to PNG; then store only the image
        # in the final PDF, so regular PDF text extraction returns zero chars.
        with pymupdf.open() as template:
            page = template.new_page(width=595, height=842)
            page.insert_text((72, 125), "SCANNED DOCUMENT TEST", fontsize=30)
            page.insert_text((72, 185), "DocMind optical character recognition", fontsize=18)
            png = page.get_pixmap(dpi=180, alpha=False).tobytes("png")
        with pymupdf.open() as scanned:
            scanned.new_page(width=595, height=842).insert_image(
                pymupdf.Rect(0, 0, 595, 842), stream=png
            )
            scanned.save(str(image_pdf))
        with pymupdf.open(str(image_pdf)) as doc:
            if doc[0].get_text("text").strip():
                raise AssertionError("Test fixture unexpectedly has a text layer")
        used = []
        segments = list(iter_pdf_segments_with_ocr(
            image_pdf, language=language, tessdata=data_dir, on_ocr=used.append,
        ))
        recognized = segments[0].text.lower()
        if used != [1] or "scanned" not in recognized or "document" not in recognized:
            raise AssertionError("Synthetic scanned-page OCR did not recover the target phrase")
        return {"result": "PASS", "scanned_pdf_pages": 1,
                "ocr_pages_recognized": len(used), "characters": len(segments[0].text),
                "no_persistent_files": True, "mysql_modified": False,
                "qdrant_written": False}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--language", default="eng")
    p.add_argument("--tessdata", default=None)
    a = p.parse_args()
    try:
        import json
        print(json.dumps(smoke(a.language, a.tessdata), indent=2))
        return 0
    except Exception as exc:
        print(f"OCR smoke test failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
