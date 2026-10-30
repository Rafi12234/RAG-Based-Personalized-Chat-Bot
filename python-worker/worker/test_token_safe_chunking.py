"""Offline tests: no S3/MySQL/Redis/Qdrant and no model download."""
import unittest
from uuid import UUID

from .document_chunking import DocumentChunk
from .token_safe_chunking import _count_tokens, iter_token_safe_chunks


class FakeEncoding:
    def __init__(self, ids):
        self.ids = ids


class FakeTokenizer:
    def encode(self, text):
        # Deliberately tokenize by code point for a conservative test budget.
        return FakeEncoding(list(range(len(text) + 2)))


def make_chunk(text, *, page=1, version="v1", index=0):
    return DocumentChunk(
        chunk_id=f"source-{index}", document_version_id=version,
        chunk_index=index, text=text, page_number=page,
        source_start_segment=index, source_end_segment=index,
        source_kind="pdf_page", group_char_start=0, group_char_end=len(text),
    )


class TokenChunkingTests(unittest.TestCase):
    def setUp(self):
        self.tokenizer = FakeTokenizer()

    def split(self, chunks, **kwargs):
        return list(iter_token_safe_chunks(chunks, self.tokenizer, **kwargs))

    def test_small_chunk_kept_whole(self):
        result = self.split([make_chunk("hello world")])
        self.assertEqual([c.text for c in result], ["hello world"])

    def test_large_chunk_split_under_token_limit(self):
        chunks = self.split([make_chunk("word " * 180)], max_tokens=120)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(_count_tokens(self.tokenizer, c.text) <= 120 for c in chunks))

    def test_all_source_positions_covered(self):
        source = "word " * 160
        chunks = self.split([make_chunk(source)], max_tokens=100)
        touched = set()
        for c in chunks:
            self.assertEqual(c.text, source[c.group_char_start:c.group_char_end])
            touched.update(range(c.group_char_start, c.group_char_end))
        self.assertEqual(touched, set(range(len(source))))

    def test_overlap_exists_and_progresses(self):
        source = "text " * 160
        chunks = self.split([make_chunk(source)], max_tokens=100, overlap_chars=32)
        self.assertLess(chunks[1].group_char_start, chunks[0].group_char_end)
        self.assertTrue(all(b.group_char_start > a.group_char_start for a, b in zip(chunks, chunks[1:])))

    def test_zero_overlap_preserves_exact_text(self):
        source = "x" * 550
        chunks = self.split([make_chunk(source)], max_tokens=100, overlap_chars=0)
        self.assertEqual("".join(c.text for c in chunks), source)

    def test_page_boundaries_never_crossed(self):
        result = self.split([make_chunk("a" * 600, page=1), make_chunk("b" * 600, page=2, index=1)], max_tokens=100)
        self.assertEqual({c.page_number for c in result}, {1, 2})
        self.assertTrue(all(set(c.text) in ({"a"}, {"b"}) for c in result))

    def test_ids_stable_unique_and_version_scoped(self):
        source = [make_chunk("x" * 600)]
        a = self.split(source, max_tokens=100)
        b = self.split(source, max_tokens=100)
        other = self.split([make_chunk("x" * 600, version="v2")], max_tokens=100)
        self.assertEqual([x.chunk_id for x in a], [x.chunk_id for x in b])
        self.assertEqual(len({x.chunk_id for x in a}), len(a))
        self.assertNotEqual(a[0].chunk_id, other[0].chunk_id)
        UUID(a[0].chunk_id)

    def test_indices_in_order_across_source_chunks(self):
        result = self.split([make_chunk("a" * 600), make_chunk("b" * 500, page=2, index=1)], max_tokens=100)
        self.assertEqual([x.chunk_index for x in result], list(range(len(result))))

    def test_invalid_limits(self):
        for kwargs in ({"max_tokens": 5}, {"max_tokens": 481}, {"overlap_chars": -1}, {"overlap_chars": 257}):
            with self.assertRaises(ValueError):
                self.split([], **kwargs)

    def test_no_empty_chunks_from_normal_source(self):
        chunks = self.split([make_chunk("the quick brown fox " * 45)], max_tokens=80)
        self.assertTrue(chunks)
        self.assertTrue(all(c.text.strip() for c in chunks))

    def test_bengali_characters_are_covered(self):
        source = "বাংলাদেশের ইতিহাস ও সংস্কৃতি " * 30
        chunks = self.split([make_chunk(source)], max_tokens=75)
        coverage = set()
        for ch in chunks:
            coverage.update(range(ch.group_char_start, ch.group_char_end))
        self.assertEqual(coverage, set(range(len(source))))

    def test_wrong_input_type_rejected(self):
        with self.assertRaises(TypeError):
            self.split(["not a chunk"])


if __name__ == "__main__":
    unittest.main()
