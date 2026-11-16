"""Phase 4.3A offline hierarchical indexing tests; no services or model download."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf
from docx import Document

from .document_structure import (
    SourceBlock, PdfPageStructure, iter_docx_structure, iter_pdf_page_structures,
)
from .hierarchical_index import (
    HierarchyIntegrityError, HierarchyManifestBuilder,
    iter_hierarchical_passages, iter_manifested_docx_passages,
    iter_manifested_pdf_passages, MAX_PASSAGES,
)


class Encoding:
    def __init__(self, size):
        self.ids = list(range(size))


class FakeTokenizer:
    def encode(self, text):
        return Encoding(len(text) + 2)


DIGEST = "a" * 64


class HierarchyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tokenizer = FakeTokenizer()

    def pdf(self, pages):
        path=self.root/"book.pdf"
        with pymupdf.open() as doc:
            for i in range(1,pages+1):
                page=doc.new_page()
                page.insert_text((72,72),f"Independent evidence is on page {i}; number {i+7000}.")
            doc.save(str(path))
        return path

    def test_pdf_page_parents_and_version_scopes(self):
        path=self.pdf(3)
        pages=list(iter_pdf_page_structures(path, "version1"))
        builder=HierarchyManifestBuilder("version1", DIGEST, expected_pages=3)
        ps=list(iter_manifested_pdf_passages(pages,builder,self.tokenizer))
        manifest=builder.finalize()
        self.assertEqual([p.page_number for p in ps],[1,2,3])
        self.assertEqual(len({p.parent_id for p in ps}),3)
        self.assertEqual(manifest.accounted_pages,3)
        self.assertEqual(manifest.passage_count,3)
        self.assertEqual(manifest.point_ids,tuple(p.passage_id for p in ps))
        self.assertFalse(manifest.public_metadata()["index_ready"])
        other=list(iter_hierarchical_passages(
            (b for pg in iter_pdf_page_structures(path, "version2") for b in pg.blocks),
            self.tokenizer))
        self.assertNotEqual(ps[0].passage_id,other[0].passage_id)

    def test_5000_page_stream_manifest_includes_last_page(self):
        version="ver5000"
        builder=HierarchyManifestBuilder(version,DIGEST,expected_pages=5000)
        def pages():
            for n in range(1,5001):
                yield PdfPageStructure(n,(
                    SourceBlock(f"source-{n}",version,0,
                      f"Unique page {n} searchable fact number {n+1000}",
                      "pdf_text",n,(0.0,0.0,80.0,20.0),(),
                      f"pdf:p{n:05d}:b00000"),),
                    False,False)
        last=None
        for item in iter_manifested_pdf_passages(pages(),builder,self.tokenizer):
            last=item
        manifest=builder.finalize()
        self.assertEqual(manifest.accounted_pages,5000)
        self.assertEqual(manifest.passage_count,5000)
        self.assertEqual(last.page_number,5000)
        self.assertEqual(len(manifest.point_ids),5000)
        self.assertEqual(manifest.parent_count,5000)

    def test_missing_page_and_ocr_unresolved_stop_finalize(self):
        version="v1"
        for num,needs_ocr in ((2,False),(1,True)):
            b=HierarchyManifestBuilder(version,DIGEST,expected_pages=2)
            with self.assertRaisesRegex(HierarchyIntegrityError,"coverage|OCR"):
                list(iter_manifested_pdf_passages(
                    [PdfPageStructure(num,(),needs_ocr,needs_ocr)],b,self.tokenizer))
        b=HierarchyManifestBuilder(version,DIGEST,expected_pages=3)
        b.add_pdf_page(PdfPageStructure(1,(),False,False))
        with self.assertRaisesRegex(HierarchyIntegrityError,"Incomplete"):
            b.finalize()

    def test_docx_chapter_and_table_ancestry(self):
        file=self.root/"policy.docx"
        d=Document()
        d.add_heading("Chapter A",level=1)
        d.add_paragraph("Return period is 14 days.")
        d.add_heading("Rules",level=2)
        tbl=d.add_table(rows=0,cols=2)
        for row in (("Item ID","Due Date"),("SKU-500","2026-08-17")):
            cells=tbl.add_row().cells
            for i,val in enumerate(row): cells[i].text=val
        d.add_heading("Chapter B",level=1)
        d.add_paragraph("Return period is 60 days.")
        d.save(file)
        builder=HierarchyManifestBuilder("version3",DIGEST)
        ps=list(iter_manifested_docx_passages(
            iter_docx_structure(file,"version3"),builder,self.tokenizer))
        manifest=builder.finalize()
        a=next(x for x in ps if "14 days" in x.text)
        b=next(x for x in ps if "60 days" in x.text)
        row=next(x for x in ps if "SKU-500" in x.text)
        self.assertNotEqual(a.parent_id,b.parent_id)
        self.assertEqual(a.section_path,("Chapter A",))
        self.assertEqual(row.section_path,("Chapter A","Rules"))
        self.assertIn("Section: Chapter A > Rules",row.embedding_text)
        self.assertIn("Item ID: SKU-500",row.text)
        self.assertEqual(manifest.expected_pages,None)
        self.assertEqual(manifest.passage_count,len(ps))
        self.assertTrue(all(x.page_number is None for x in ps))

    def test_long_block_token_budget_and_full_nonwhitespace_coverage(self):
        text="বাংলাদেশ তথ্য " * 750
        block=SourceBlock("sourceid","ver",0,text,"pdf_text",1,
                          (0,0,100,50),(),"pdf:p00001:b00000")
        passages=list(iter_hierarchical_passages([block],self.tokenizer,
                     max_tokens=128,max_chars=250,overlap_chars=25))
        covered=set()
        for p in passages:
            self.assertLessEqual(len(p.embedding_text),250)
            self.assertLessEqual(p.token_count,128)
            self.assertEqual(p.text,text[p.char_start:p.char_end])
            covered.update(range(p.char_start,p.char_end))
        self.assertTrue(all(i in covered for i,c in enumerate(text) if not c.isspace()))
        self.assertEqual([p.passage_index for p in passages],list(range(len(passages))))
        self.assertEqual(passages[1].previous_in_parent,passages[0].passage_id)

    def test_very_large_block_has_bounded_tokenizer_input(self):
        class BoundedTokenizer(FakeTokenizer):
            def encode(self,text):
                if len(text)>959:
                    raise AssertionError("Too much text tokenized at once")
                return super().encode(text)
        block=SourceBlock("abc","ver",0,"x"*200000,
                          "pdf_text",1,None,(),"pdf:p00001:b00000")
        results=list(iter_hierarchical_passages(
            [block],BoundedTokenizer(),max_chars=950))
        self.assertGreater(len(results),200)

    def test_duplicate_or_mixed_version_blocks_fail(self):
        block=SourceBlock("a","ver",0,"text","pdf_text",1,None,(),"pdf:p00001:b00000")
        with self.assertRaisesRegex(HierarchyIntegrityError,"Duplicate"):
            list(iter_hierarchical_passages([block,block],self.tokenizer))
        wrong=SourceBlock("b","other",0,"text","pdf_text",2,None,(),"pdf:p00002:b00000")
        with self.assertRaisesRegex(HierarchyIntegrityError,"versions"):
            list(iter_hierarchical_passages([block,wrong],self.tokenizer))

    def test_manifest_refuses_mismatched_version_and_double_finalize(self):
        p=next(iter_hierarchical_passages(
            [SourceBlock("b","v",0,"text","pdf_text",1,None,(),"pdf:p00001:b00000")],
            self.tokenizer))
        b=HierarchyManifestBuilder("other",DIGEST,expected_pages=1)
        b.add_pdf_page(PdfPageStructure(1,(),False,False))
        with self.assertRaisesRegex(HierarchyIntegrityError,"scope"):
            b.add_passage(p)
        b=HierarchyManifestBuilder("v",DIGEST,expected_pages=1)
        b.add_pdf_page(PdfPageStructure(1,(),False,False))
        b.add_passage(p)
        b.finalize()
        with self.assertRaisesRegex(HierarchyIntegrityError,"twice"):
            b.finalize()


if __name__=="__main__":
    unittest.main()
