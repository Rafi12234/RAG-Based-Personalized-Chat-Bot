"""Format DOCX table rows for retrieval without losing column-header meaning.

A normal row such as "17 | 2026-04-01" is ambiguous in a vector chunk.
When the first row is a plausible header, following rows retain column names:
"Invoice ID: 17 | Due Date: 2026-04-01". No model or IO required.
These heuristics are conservative; a full grid/merged-cell parser is planned.
"""
from __future__ import annotations

from typing import Iterable, Iterator

MAX_COLUMNS = 40
MAX_HEADER_CHARS = 100
MAX_CELL_CHARS = 20_000


def _plausible_header(cells: list[str]) -> bool:
    if not 2 <= len(cells) <= MAX_COLUMNS:
        return False
    items = [c.strip() for c in cells]
    if any(not item or len(item) > MAX_HEADER_CHARS for item in items):
        return False
    if len({item.casefold() for item in items}) != len(items):
        return False
    # Data-like first rows should not become false headings.
    if any(not any(c.isalpha() for c in item) or
           item.replace(".", "").replace(",", "").isdigit() for item in items):
        return False
    return True


def label_docx_table_rows(rows: Iterable[Iterable[str]]) -> Iterator[str]:
    """Yield bounded, header-aware rows without buffering the whole table.

    Header cells are never assumed when table properties are unavailable:
    only a plausible textual header row is promoted. Caller should preserve
    real table IDs and merged-cell geometry in the later layout milestone.
    """
    header: list[str] | None = None
    for row_number, row in enumerate(rows):
        cells = [str(value).strip().replace("\n", " ") for value in row]
        if not cells or not any(cells):
            continue
        if len(cells) > MAX_COLUMNS or any(len(c) > MAX_CELL_CHARS for c in cells):
            raise ValueError("DOCX table row exceeds resource limit")
        if row_number == 0 and _plausible_header(cells):
            header = cells
            yield " | ".join(cells)
            continue
        if header is not None and len(header) == len(cells):
            yield " | ".join(
                f"{col}: {val}" if val else f"{col}: [empty]"
                for col, val in zip(header, cells)
            )
        else:
            yield " | ".join(cells)
