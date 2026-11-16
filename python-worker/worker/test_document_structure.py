"""Offline PDF-block and DOCX-section tests, never touching services."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import UUID

import pymupdf
from docx import Document

from .document_extraction import DocumentExtractionError
from .document_structure import (
    iter_pdf_page_structures, iter_docx_structure, iter_layout_segments,
)


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_pdf(self, pages=3):
        path = self.root / "book.pdf"
        with pymupdf.open() as doc:
            for n in range(1, pages + 1):
                page = doc.new_page()
                page.insert_text((72, 72), f"Page {n} of 5,000 book test, stable reference.")
            doc.save(str(path))
        return path

    def test_pdf_preserves_pages_geometry_and_stable_ids(self):
        path = self.make_pdf()
        first = list(iter_pdf_page_structures(path, "version_a"))
        self.assertEqual([p.page_number for p in first], [1,2,3])
        self.assertTrue(all(p.blocks for p in first))
        self.assertEqual(first[1].blocks[0].page_number,2)
        self.assertTrue(first[1].blocks[0].bbox[0] >= 0)
        self.assertTrue(first[1].blocks[0].locator.startswith("pdf:p00002:"))
        self.assertEqual(first, list(iter_pdf_page_structures(path,"version_a")))
        self.assertNotEqual(first[0].blocks[0].block_id,
                            list(iter_pdf_page_structures(path,"version_b"))[0].blocks[0].block_id)
        UUID(first[0].blocks[0].block_id)

    def test_pdf_page_range_preserves_original_numbers(self):
        path = self.make_pdf(12)
        pages = list(iter_pdf_page_structures(path,"ver",first_page=9,last_page=11))
        self.assertEqual([p.page_number for p in pages], [9,10,11])
        self.assertIn("Page 11",pages[-1].blocks[0].text)

    def test_blank_pdf_page_has_no_fake_text(self):
        path = self.root / "blank.pdf"
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(str(path))
        pages = list(iter_pdf_page_structures(path,"ver"))
        self.assertFalse(pages[0].blocks)
        self.assertFalse(pages[0].needs_ocr)
        self.assertEqual(list(iter_layout_segments(iter(pages)))[0].text, "")

    def test_pdf_invalid_page_scope_fails_closed(self):
        path=self.make_pdf()
        for kw in ({"first_page":0}, {"first_page":2,"last_page":1},
                   {"first_page":5}, {"last_page":5}):
            with self.assertRaises((ValueError, DocumentExtractionError)):
                list(iter_pdf_page_structures(path,"ver",**kw))

    def test_docx_heading_paths_and_table_columns(self):
        path=self.root/"structured.docx"
        doc=Document()
        doc.add_heading("Chapter One",level=1)
        doc.add_heading("Procedures",level=2)
        doc.add_paragraph("Use these rules.")
        table=doc.add_table(rows=0, cols=2)
        for values in [("Part Number","Quantity"),("SKU-42","17")]:
            cells=table.add_row().cells
            for index,text in enumerate(values):
                cells[index].text=text
        doc.add_heading("Chapter Two",level=1)
        doc.add_paragraph("Different rules.")
        doc.save(path)
        rows=list(iter_docx_structure(path,"ver"))
        self.assertEqual([r.kind for r in rows[:3]],
                         ["docx_heading","docx_heading","docx_paragraph"])
        self.assertEqual(rows[2].section_path,("Chapter One","Procedures"))
        self.assertEqual(rows[4].table_id,rows[3].table_id)
        self.assertIn("Part Number: SKU-42",rows[4].text)
        self.assertIn("Quantity: 17",rows[4].text)
        self.assertEqual(rows[-1].section_path,("Chapter Two",))
        self.assertTrue(all(x.page_number is None for x in rows))
        self.assertTrue(all(x.block_id for x in rows))

    def test_structured_docx_adapter_includes_parent_section(self):
        from .document_structure import iter_structured_docx_segments
        path=self.root/"adapter.docx"
        doc=Document()
        doc.add_heading("Policy A",level=1)
        doc.add_paragraph("Refund within 30 days")
        doc.add_heading("Policy B",level=1)
        doc.add_paragraph("Refund within 60 days")
        doc.save(path)
        items=list(iter_structured_docx_segments(path,"version"))
        self.assertEqual(items[1].text,"Section: Policy A\nRefund within 30 days")
        self.assertEqual(items[3].text,"Section: Policy B\nRefund within 60 days")
        self.assertTrue(all(x.page_number is None for x in items))
        self.assertTrue(all(x.kind.startswith("docx_") for x in items))

    def test_docx_new_version_changes_block_id(self):
        path=self.root/"ver.docx"
        doc=Document(); doc.add_heading("A chapter",level=1); doc.save(path)
        first=list(iter_docx_structure(path,"v1"))[0]
        second=list(iter_docx_structure(path,"v2"))[0]
        self.assertNotEqual(first.block_id,second.block_id)

    def test_bad_docx_rejected_before_parse(self):
        path=self.root/"bad.docx"
        path.write_bytes(b"not zip")
        with self.assertRaises(DocumentExtractionError):
            list(iter_docx_structure(path,"v"))

    def test_docx_no_fake_page_number(self):
        path=self.root/"simple.docx"
        doc=Document();doc.add_paragraph("Simple text");doc.save(path)
        rows=list(iter_docx_structure(path,"ver"))
        self.assertEqual(rows[0].locator,"docx:paragraph:000001")
        self.assertIsNone(rows[0].page_number)


if __name__ == "__main__":
    unittest.main()
