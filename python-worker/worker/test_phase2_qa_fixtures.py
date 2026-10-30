"""Offline tests for synthetic QA fixture generation (no services touched)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .phase2_qa_fixtures import generate_fixture_set, main
from .document_extraction import iter_extracted_segments


class FixtureGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "fixtures"

    def make(self):
        return generate_fixture_set(self.directory, native_pdf_pages=3,
            scanned_pdf_pages=2, docx_paragraphs=4, txt_lines=5)

    def test_generates_four_test_files_and_manifest(self):
        report = self.make()
        self.assertTrue(report["synthetic_data_only"])
        self.assertFalse(report["mysql_modified"])
        self.assertFalse(report["qdrant_modified"])
        self.assertEqual(len(report["files"]), 4)
        for row in report["files"]:
            self.assertGreater(row["bytes"], 0)
            self.assertTrue((self.directory / row["name"]).is_file())
        manifest = json.loads((self.directory / "qa_manifest.json").read_text(encoding="utf8"))
        self.assertEqual(manifest["native_pdf_pages"], 3)

    def test_native_pdf_exact_pages_with_1_based_metadata(self):
        self.make()
        pages = list(iter_extracted_segments(self.directory / "qa_native.pdf", "qa_native.pdf"))
        self.assertEqual([p.page_number for p in pages], [1, 2, 3])
        self.assertTrue(all("DOCMIND QA" in p.text for p in pages))
        self.assertTrue(all(not p.needs_ocr for p in pages))

    def test_scanned_pdf_is_image_only(self):
        self.make()
        pages = list(iter_extracted_segments(self.directory / "qa_scanned.pdf", "qa_scanned.pdf"))
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(p.needs_ocr for p in pages))
        self.assertTrue(all(not p.text.strip() for p in pages))

    def test_docx_extracts_paragraphs_and_tables(self):
        self.make()
        parts = list(iter_extracted_segments(self.directory / "qa_structured.docx", "qa_structured.docx"))
        self.assertTrue(any(p.kind == "docx_paragraph" for p in parts))
        self.assertTrue(any(p.kind == "docx_table_row" for p in parts))

    def test_bengali_utf8_preserved(self):
        self.make()
        text = "".join(s.text for s in iter_extracted_segments(self.directory / "qa_bengali.txt", "qa_bengali.txt"))
        self.assertEqual(sum(line.startswith("পরীক্ষা ") for line in text.splitlines()), 5)
        self.assertIn("ডকমাইন্ড", text)

    def test_does_not_overwrite_existing_fixture(self):
        self.make()
        before = (self.directory / "qa_native.pdf").read_bytes()
        with self.assertRaises(FileExistsError):
            self.make()
        self.assertEqual((self.directory / "qa_native.pdf").read_bytes(), before)

    def test_bounds_reject_before_file_creation(self):
        for kwargs in (dict(native_pdf_pages=0), dict(native_pdf_pages=1001),
                       dict(scanned_pdf_pages=11), dict(docx_paragraphs=0),
                       dict(txt_lines=100001), dict(txt_lines=True)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                generate_fixture_set(self.directory, **kwargs)
        self.assertFalse(self.directory.exists())

    def test_error_when_output_path_is_file(self):
        existing = Path(self.temp.name) / "existing.txt"
        existing.write_text("don't overwrite")
        with self.assertRaises(ValueError):
            generate_fixture_set(existing)

    def test_cli_fixture_generation(self):
        from contextlib import redirect_stdout
        from io import StringIO
        target = self.directory
        with redirect_stdout(StringIO()) as out:
            code = main(["--output-dir", str(target), "--native-pdf-pages", "1",
                         "--scanned-pdf-pages", "1", "--docx-paragraphs", "1",
                         "--txt-lines", "1"])
        self.assertEqual(code, 0)
        self.assertIn('"synthetic_data_only": true', out.getvalue())

    def test_no_external_services(self):
        with patch("socket.create_connection", side_effect=AssertionError("network")):
            self.make()


if __name__ == "__main__": unittest.main()
