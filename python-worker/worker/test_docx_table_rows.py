"""Pure header-aware DOCX table tests; no document dependencies or IO."""
from __future__ import annotations

import unittest

from .docx_table_rows import label_docx_table_rows


class DocxRowsTests(unittest.TestCase):
    def test_column_context_survives_row_level_indexing(self):
        rows=[["Invoice ID","Due Date"],["A-176","2026-01-31"],["A-177","2026-02-15"]]
        self.assertEqual(list(label_docx_table_rows(rows)),[
            "Invoice ID | Due Date",
            "Invoice ID: A-176 | Due Date: 2026-01-31",
            "Invoice ID: A-177 | Due Date: 2026-02-15"
        ])

    def test_no_header_is_invented_for_number_row(self):
        rows=[["1001","5000"],["1002","6000"]]
        self.assertEqual(list(label_docx_table_rows(rows)),["1001 | 5000","1002 | 6000"])

    def test_single_row_unchanged(self):
        self.assertEqual(list(label_docx_table_rows([["Column A","Column B"]])),
                         ["Column A | Column B"])

    def test_ragged_table_falls_back_to_plain_row(self):
        output=list(label_docx_table_rows([
            ["Product","Price"],["Book","100","USD"],["Pencil","5"]]))
        self.assertEqual(output[1], "Book | 100 | USD")
        self.assertEqual(output[2], "Product: Pencil | Price: 5")

    def test_empty_cell_has_visible_label(self):
        output=list(label_docx_table_rows([["Code","Value"],["ABC",""]]))
        self.assertIn("Value: [empty]",output[1])

    def test_oversized_cells_are_rejected(self):
        with self.assertRaises(ValueError):
            list(label_docx_table_rows([["Code","Value"],["A"*20001,"B"]]))

    def test_generator_is_lazily_consumed(self):
        events=[]
        def gen():
            for i in range(5000):
                events.append(i)
                yield ["A","B"] if i == 0 else [str(i),"value"]
        rows=label_docx_table_rows(gen())
        self.assertEqual(next(rows),"A | B")
        self.assertEqual(len(events),1)
        self.assertEqual(next(rows),"A: 1 | B: value")
        self.assertEqual(len(events),2)


if __name__ == "__main__":
    unittest.main()
