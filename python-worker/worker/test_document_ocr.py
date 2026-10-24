"""Offline OCR decision tests; mock PDF pages and Tesseract (no services)."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .document_extraction import DocumentExtractionError
from .document_ocr import (
    OCRExtractionError, iter_pdf_segments_with_ocr, resolve_tessdata,
)


class FakePage:
    def __init__(self, native="", *, images=False, drawings=False, recognized="OCR recognized text"):
        self.native = native
        self.images = images
        self.drawings = drawings
        self.recognized = recognized
        self.ocr_calls = []

    def get_text(self, mode, *, textpage=None, sort=False):
        return self.native if textpage is None else self.recognized

    def get_image_info(self):
        return [{"x": 1}] if self.images else []

    def get_drawings(self):
        return [{"shape": "vector"}] if self.drawings else []

    def get_textpage_ocr(self, **kwargs):
        self.ocr_calls.append(kwargs)
        return object()


class FakePDF:
    is_pdf = True
    needs_pass = False

    def __init__(self, pages):
        self.pages = pages
        self.page_count = len(pages)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def __iter__(self):
        return iter(self.pages)


class OCRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        (self.data / "eng.traineddata").write_bytes(b"not used by mocked OCR")

    def run_pages(self, pages, **kwargs):
        pdf = FakePDF(pages)
        with patch("worker.document_ocr.pymupdf.open", return_value=pdf):
            return list(iter_pdf_segments_with_ocr(
                "demo.pdf", tessdata=self.data, **kwargs
            ))

    def test_native_text_skips_ocr(self):
        p = FakePage("Readable page with ordinary native text", images=True)
        result = self.run_pages([p])
        self.assertEqual(result[0].text, p.native)
        self.assertFalse(p.ocr_calls)

    def test_image_only_page_uses_full_ocr(self):
        p = FakePage(images=True, recognized="OCR page one")
        events = []
        result = self.run_pages([p], on_ocr=events.append)
        self.assertEqual(result[0].text, "OCR page one")
        self.assertEqual(events, [1])
        self.assertEqual(p.ocr_calls[0]["full"], True)
        self.assertEqual(p.ocr_calls[0]["dpi"], 180)

    def test_blank_page_skips_ocr(self):
        p = FakePage()
        blanks = []
        result = self.run_pages([p], on_blank=blanks.append)
        self.assertEqual(result[0].text, "")
        self.assertEqual(blanks, [1])
        self.assertFalse(p.ocr_calls)

    def test_short_title_preserved_without_visuals(self):
        p = FakePage("Title")
        self.assertEqual(self.run_pages([p])[0].text, "Title")
        self.assertFalse(p.ocr_calls)

    def test_vector_content_detected(self):
        p = FakePage(drawings=True)
        self.assertEqual(self.run_pages([p])[0].text, "OCR recognized text")

    def test_ocr_empty_fails_closed(self):
        with self.assertRaises(OCRExtractionError):
            self.run_pages([FakePage(images=True, recognized=" ")])

    def test_ocr_page_limit_rejected(self):
        pages = [FakePage(images=True), FakePage(images=True)]
        with self.assertRaises(OCRExtractionError):
            self.run_pages(pages, max_ocr_pages=1)

    def test_missing_language_pack(self):
        with self.assertRaisesRegex(OCRExtractionError, "ben"):
            resolve_tessdata("eng+ben", self.data)

    def test_language_pack_validation(self):
        (self.data / "ben.traineddata").write_bytes(b"mock")
        self.assertEqual(resolve_tessdata("eng+ben", self.data), self.data)

    def test_invalid_language(self):
        with self.assertRaises(ValueError):
            self.run_pages([], language="eng;rm")

    def test_invalid_dpi_and_budget(self):
        for args in ({"dpi": 0}, {"dpi": 900}, {"max_ocr_pages": 0}):
            with self.assertRaises(ValueError):
                self.run_pages([], **args)

    def test_non_pdf_rejected(self):
        with self.assertRaises(DocumentExtractionError):
            list(iter_pdf_segments_with_ocr("evil.docx"))

    def test_page_numbering_and_blank_tracking(self):
        ocr, blanks = [], []
        pages = [FakePage("Normal readable page text here"), FakePage(images=True), FakePage()]
        result = self.run_pages(pages, on_ocr=ocr.append, on_blank=blanks.append)
        self.assertEqual([x.page_number for x in result], [1, 2, 3])
        self.assertEqual([x.kind for x in result], ["pdf_page"] * 3)
        self.assertEqual(ocr, [2])
        self.assertEqual(blanks, [3])

    def test_password_protected_rejected(self):
        pdf = FakePDF([FakePage()])
        pdf.needs_pass = True
        with patch("worker.document_ocr.pymupdf.open", return_value=pdf):
            with self.assertRaises(DocumentExtractionError):
                list(iter_pdf_segments_with_ocr("demo.pdf"))


if __name__ == "__main__":
    unittest.main()
