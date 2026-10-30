"""Offline unit tests for page-aware, deterministic chunking (no services)."""

import unittest
from uuid import UUID

from .document_chunking import iter_document_chunks
from .document_extraction import ExtractedSegment


class ChunkingTests(unittest.TestCase):
    def generate(self, segments, version="v1", max_chars=300, overlap_chars=40):
        return list(iter_document_chunks(
            segments, version, max_chars=max_chars, overlap_chars=overlap_chars
        ))

    def test_pdf_page_isolation(self):
        pages = [
            ExtractedSegment("A " * 400, "pdf_page", page_number=1),
            ExtractedSegment("B " * 400, "pdf_page", page_number=2),
        ]
        chunks = self.generate(pages)
        self.assertGreater(len(chunks), 2)
        self.assertEqual({c.page_number for c in chunks}, {1, 2})
        self.assertTrue(all(set(c.text.replace(" ", "")) in ({"A"}, {"B"}) for c in chunks))

    def test_chunks_are_bounded_and_ordered(self):
        chunks = self.generate([ExtractedSegment("word " * 400, "txt_text")])
        self.assertTrue(all(len(c.text) <= 300 for c in chunks))
        self.assertEqual([c.chunk_index for c in chunks], list(range(len(chunks))))
        self.assertEqual([c.group_char_start for c in chunks], sorted(c.group_char_start for c in chunks))

    def test_overlap_contains_shared_source_text(self):
        text = "word " * 200
        chunks = self.generate([ExtractedSegment(text, "txt_text")], overlap_chars=40)
        self.assertGreater(len(chunks), 1)
        self.assertLess(chunks[1].group_char_start, chunks[0].group_char_end)

    def test_identifiers_stable_and_scoped_to_version(self):
        segs = [ExtractedSegment("hello " * 100, "txt_text")]
        first = self.generate(segs, version="v1")
        second = self.generate(segs, version="v1")
        third = self.generate(segs, version="v2")
        self.assertEqual([c.chunk_id for c in first], [c.chunk_id for c in second])
        self.assertNotEqual(first[0].chunk_id, third[0].chunk_id)
        UUID(first[0].chunk_id)  # Qdrant accepts UUID point IDs.

    def test_docx_paragraphs_packed_in_order(self):
        chunks = self.generate([
            ExtractedSegment("Intro", "docx_paragraph"),
            ExtractedSegment("Alpha | Beta", "docx_table_row"),
            ExtractedSegment("Summary", "docx_paragraph"),
        ])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text, "Intro\n\nAlpha | Beta\n\nSummary")
        self.assertEqual((chunks[0].source_start_segment, chunks[0].source_end_segment), (0, 2))

    def test_txt_segments_continuous_without_extra_separators(self):
        chunks = self.generate([
            ExtractedSegment("Hello", "txt_text"),
            ExtractedSegment("World", "txt_text"),
        ])
        self.assertEqual(chunks[0].text, "HelloWorld")

    def test_candidate_ocr_page_not_indexed(self):
        chunks = self.generate([
            ExtractedSegment(" ", "pdf_page", page_number=1, needs_ocr=True),
            ExtractedSegment("Readable passage", "pdf_page", page_number=2),
        ])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].page_number, 2)

    def test_invalid_window_rejected(self):
        with self.assertRaises(ValueError):
            self.generate([], max_chars=100)
        with self.assertRaises(ValueError):
            self.generate([], max_chars=300, overlap_chars=150)

    def test_whitespace_skipped(self):
        self.assertEqual(self.generate([ExtractedSegment("  \n\n ", "txt_text")]), [])

    def test_unbroken_text_still_progresses(self):
        chunks = self.generate([ExtractedSegment("x" * 1000, "txt_text")])
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(0 < len(c.text) <= 300 for c in chunks))

    def test_normalizes_crlf(self):
        chunks = self.generate([ExtractedSegment("one\r\ntwo", "txt_text")])
        self.assertEqual(chunks[0].text, "one\ntwo")


if __name__ == "__main__":
    unittest.main()
