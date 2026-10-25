"""No-network performance QA tests for local synthetic corpus."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .phase2_qa_benchmark import benchmark_local_file, main, MAX_QA_CHUNKS
from .phase2_qa_fixtures import generate_fixture_set


class FakeTokenizer:
    def encode(self, text):
        # Deliberately char-heavy tokenization to exercise safe splitting.
        return SimpleNamespace(ids=list(range(len(text) // 3 + 2)))


class FakeModel:
    def embed(self, documents, *, batch_size=8):
        for _ in documents:
            yield [1.0] * 384


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name) / "qa"
        generate_fixture_set(cls.folder, native_pdf_pages=3, scanned_pdf_pages=2,
                             docx_paragraphs=4, txt_lines=5)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def run_benchmark(self, file, **opts):
        return benchmark_local_file(self.folder / file, **opts)

    def test_extract_native_pdf(self):
        report = self.run_benchmark("qa_native.pdf", expected_pages=3)
        self.assertTrue(report["qa_passed"])
        self.assertEqual(report["pdf_pages"], 3)
        self.assertEqual(report["native_ocr_candidate_pages"], 0)
        self.assertIsNone(report["chunks"])

    def test_extract_bengali_txt(self):
        report = self.run_benchmark("qa_bengali.txt")
        self.assertTrue(report["characters"] > 0)
        self.assertIsNone(report["pdf_pages"])

    def test_extract_docx(self):
        report = self.run_benchmark("qa_structured.docx")
        self.assertGreater(report["segments"], 4)

    def test_scanned_pdf_reports_ocr_candidates(self):
        report = self.run_benchmark("qa_scanned.pdf", expected_pages=2)
        self.assertEqual(report["native_ocr_candidate_pages"], 2)

    def test_chunk_mode_with_real_extraction(self):
        report = self.run_benchmark("qa_native.pdf", mode="chunks", expected_pages=3,
                                    tokenizer=FakeTokenizer())
        self.assertGreaterEqual(report["chunks"], 3)
        self.assertLessEqual(report["max_e5_tokens"], 480)
        self.assertEqual(report["pdf_pages_indexed"], 3)

    def test_missing_pdf_page_in_chunk_output_refused(self):
        from .document_chunking import DocumentChunk
        def fake_chunks(segments, version_id):
            list(segments)  # drain the real extraction stream
            yield DocumentChunk("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                                version_id, 0, "Example", 1, 0, 0, "pdf_page", 0, 7)
        with patch("worker.phase2_qa_benchmark.iter_document_chunks", side_effect=fake_chunks):
            with self.assertRaisesRegex(RuntimeError, "Nonblank PDF pages"):
                self.run_benchmark("qa_native.pdf", mode="chunks", tokenizer=FakeTokenizer())

    def test_chunk_mode_bengali_text(self):
        report = self.run_benchmark("qa_bengali.txt", mode="chunks",
                                    tokenizer=FakeTokenizer())
        self.assertGreater(report["chunks"], 0)

    def test_embed_mode_mock_has_correct_vectors(self):
        report = self.run_benchmark("qa_structured.docx", mode="embed",
                    tokenizer=FakeTokenizer(), model=FakeModel())
        self.assertEqual(report["vectors"], report["chunks"])
        self.assertEqual(report["embedding_model"], "intfloat/multilingual-e5-small")

    def test_readonly_metadata_fields(self):
        report = self.run_benchmark("qa_native.pdf")
        for key in ("mysql_modified", "qdrant_modified", "redis_modified", "storage_modified"):
            self.assertFalse(report[key])
        self.assertIn("peak_python_allocations_mb", report)
        self.assertIsNone(report["peak_process_rss_mb"])

    def test_chunk_rejects_missing_ocr(self):
        with self.assertRaisesRegex(RuntimeError, "OCR"):
            self.run_benchmark("qa_scanned.pdf", mode="chunks", tokenizer=FakeTokenizer())

    def test_expected_pdf_pages_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, "page count"):
            self.run_benchmark("qa_native.pdf", expected_pages=4)

    def test_expected_pages_restricted_to_pdf(self):
        with self.assertRaises(ValueError):
            self.run_benchmark("qa_structured.docx", expected_pages=2)

    def test_reject_missing_files(self):
        with self.assertRaises(ValueError):
            self.run_benchmark("nonexistent.txt")

    def test_reject_non_supported_extension(self):
        with self.assertRaises(ValueError):
            self.run_benchmark("qa_manifest.json")

    def test_reject_empty_source(self):
        empty = self.folder / "empty.txt"
        empty.write_text("")
        self.addCleanup(empty.unlink)
        with self.assertRaises(ValueError):
            benchmark_local_file(empty)

    def test_reject_invalid_mode(self):
        with self.assertRaises(ValueError):
            self.run_benchmark("qa_native.pdf", mode="publish")

    def test_reject_invalid_ocr_flag(self):
        with self.assertRaises(TypeError):
            self.run_benchmark("qa_native.pdf", ocr="yes")

    def test_ocr_only_for_pdf(self):
        with self.assertRaises(ValueError):
            self.run_benchmark("qa_bengali.txt", ocr=True)

    def test_bad_ocr_budget_refused(self):
        for n in (0, 5001, True):
            with self.subTest(n=n), self.assertRaises(ValueError):
                self.run_benchmark("qa_native.pdf", max_ocr_pages=n)

    def test_bad_chunk_budget_refused(self):
        for n in (0, 50001, False):
            with self.subTest(n=n), self.assertRaises(ValueError):
                self.run_benchmark("qa_native.pdf", max_chunks=n)

    def test_chunk_budget_cap_works(self):
        with self.assertRaisesRegex(RuntimeError, "budget"):
            self.run_benchmark("qa_native.pdf", mode="chunks", tokenizer=FakeTokenizer(), max_chunks=1)

    def test_real_ocr_delegation_simulated(self):
        from .document_extraction import ExtractedSegment
        pages = [ExtractedSegment("OCR page one text", "pdf_page", 1),
                 ExtractedSegment("OCR page two text", "pdf_page", 2)]
        with patch("worker.phase2_qa_benchmark.iter_pdf_segments_with_ocr", return_value=iter(pages)) as run:
            report = self.run_benchmark("qa_scanned.pdf", mode="chunks", ocr=True,
                                        expected_pages=2, max_ocr_pages=2,
                                        tokenizer=FakeTokenizer())
        self.assertEqual(report["chunks"], 2)
        self.assertEqual(run.call_args.kwargs["max_ocr_pages"], 2)

    def test_cli_extract_does_not_require_tokenizer(self):
        with patch("worker.storage_qdrant_probe._tokenizer", side_effect=AssertionError("must not load model")):
            with redirect_stdout(io.StringIO()) as output:
                code = main(["--file", str(self.folder / "qa_native.pdf"),
                             "--mode", "extract", "--expected-pages", "3"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["qa_passed"])

    def test_optional_json_write_without_overwrite(self):
        target = Path(self.temp.name) / "report.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--file", str(self.folder / "qa_native.pdf"),
                                    "--json-out", str(target)]), 0)
        self.assertTrue(json.loads(target.read_text())["qa_passed"])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--file", str(self.folder / "qa_native.pdf"),
                                    "--json-out", str(target)]), 1)
        self.assertIn("FileExistsError", output.getvalue())

    def test_cli_safe_exception_does_not_show_file_path(self):
        with redirect_stdout(io.StringIO()) as out:
            code = main(["--file", "C:/private/secrets/sample.pdf"])
        self.assertEqual(code, 1)
        self.assertNotIn("private", out.getvalue())

    def test_never_makes_db_or_qdrant_calls(self):
        with patch("socket.create_connection", side_effect=AssertionError("network")):
            self.run_benchmark("qa_native.pdf", mode="chunks", tokenizer=FakeTokenizer())


if __name__ == '__main__': unittest.main()
