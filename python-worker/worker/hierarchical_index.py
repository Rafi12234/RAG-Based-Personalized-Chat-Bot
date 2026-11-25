"""Phase 4.3A: streaming hierarchical passage builder and immutable manifest.

Independent of the existing r6 dense retrieval and v1 Qdrant collection.
Converts PDF page blocks or DOCX section/table blocks into E5-token-safe child
passages with stable page/section/table parents and original source locators.

This module does not load an embedding model, communicate with services,
change MySQL or Qdrant, mark READY, or claim full-book search capability.
A caller must supply the exact model tokenizer, authorized document version
and an immutable original file SHA-256.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable, Iterator
from uuid import NAMESPACE_URL, uuid5
import json
import re

from .document_structure import PdfPageStructure, SourceBlock, MAX_BLOCK_CHARS
from .document_embeddings import MODEL_NAME
from .token_safe_chunking import SAFE_TOKEN_BUDGET

MAX_PASSAGES = 50_000
MAX_EMBEDDING_CHARS = 950
MAX_CONTEXT_CHARS = 320
MAX_SECTION_DEPTH = 9
MAX_PDF_PAGES = 5000
MANIFEST_SCHEMA = "docmind-hierarchy:v2"
HEX_SHA = re.compile(r"^[0-9a-f]{64}$")


class HierarchyIntegrityError(ValueError):
    """Incomplete, inconsistent, truncated or out-of-scope hierarchy."""


@dataclass(frozen=True)
class HierarchicalPassage:
    passage_id: str
    document_version_id: str
    passage_index: int
    parent_id: str
    section_id: str | None
    previous_in_parent: str | None
    source_block_id: str
    source_locator: str
    section_path: tuple[str, ...]
    source_kind: str
    page_number: int | None
    bbox: tuple[float, float, float, float] | None
    char_start: int
    char_end: int
    text: str
    embedding_text: str
    token_count: int


@dataclass(frozen=True)
class HierarchyManifest:
    schema: str
    document_version_id: str
    source_sha256: str
    embedding_model: str
    expected_pages: int | None
    accounted_pages: int
    block_count: int
    passage_count: int
    parent_count: int
    point_ids: tuple[str, ...]
    digest: str

    def public_metadata(self) -> dict:
        """Does not reveal customer text or raw storage paths."""
        return {
            "schema": self.schema,
            "document_version_id": self.document_version_id,
            "source_sha256": self.source_sha256,
            "embedding_model": self.embedding_model,
            "expected_pages": self.expected_pages,
            "accounted_pages": self.accounted_pages,
            "block_count": self.block_count,
            "passage_count": self.passage_count,
            "parent_count": self.parent_count,
            "manifest_digest": self.digest,
            "index_ready": False,
            "retrieval_cutover_authorized": False,
        }


def _canonical(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True).encode("utf-8")


def _id(*parts: object) -> str:
    # Separators are unambiguous; source locators and section identifiers
    # are trusted only after SourceBlock/version validation.
    return str(uuid5(NAMESPACE_URL, json.dumps(
        ("docmind:hierarchical:v2", *parts), ensure_ascii=False,
        separators=(",", ":"))))


def _tokens(tokenizer, text: str) -> int:
    result = tokenizer.encode("passage: " + text)
    ids = getattr(result, "ids", None)
    if ids is None:
        raise TypeError("Tokenizer.encode must return an Encoding with ids")
    return len(ids)


def _safe_end(tokenizer, text: str, start: int, prefix: str,
              max_tokens: int, max_chars: int) -> int:
    """Bound EACH tokenization to a <=950-char window, not a 200k-char block."""
    limit = min(len(text), start + max_chars - len(prefix))
    if limit <= start:
        raise HierarchyIntegrityError("Section context consumes passage budget")
    if _tokens(tokenizer, prefix + text[start:limit]) <= max_tokens:
        return limit
    lo, hi = start + 1, limit
    best = start
    while lo <= hi:
        middle = (lo + hi) // 2
        if _tokens(tokenizer, prefix + text[start:middle]) <= max_tokens:
            best = middle
            lo = middle + 1
        else:
            hi = middle - 1
    if best <= start:
        raise HierarchyIntegrityError("Single source character exceeds E5 token budget")
    # Prefer a nearby natural boundary but never discard intervening content.
    lower = start + max(1, int((best - start) * .7))
    for separator in ("\n\n", "\n", ". ", "! ", "? ", "; ", " "):
        at = text.rfind(separator, lower, best)
        if at >= lower and at + len(separator) > start:
            return at + len(separator)
    return best


def iter_hierarchical_passages(
    blocks: Iterable[SourceBlock], tokenizer, *,
    max_tokens: int = 448,
    max_chars: int = MAX_EMBEDDING_CHARS,
    overlap_chars: int = 48,
) -> Iterator[HierarchicalPassage]:
    """Stream child passages with deterministic parents and source offsets.

    No full-chapter buffering. Docx heading anchors form a section stack;
    table rows use a table-specific parent but retain the section ID.
    PDF native/OCR blocks use page parents; OCR text without block geometry
    must first be converted to an explicit SourceBlock on the correct page.
    """
    if not callable(getattr(tokenizer, "encode", None)):
        raise TypeError("Exact E5 tokenizer required")
    if type(max_tokens) is not int or not 32 <= max_tokens <= SAFE_TOKEN_BUDGET:
        raise ValueError("Invalid E5 passage token budget")
    if type(max_chars) is not int or not 128 <= max_chars <= MAX_EMBEDDING_CHARS:
        raise ValueError("Invalid E5 passage character budget")
    if type(overlap_chars) is not int or not 0 <= overlap_chars <= 128:
        raise ValueError("Invalid passage overlap")
    seen_locators: set[str] = set()
    previous_by_parent: dict[str, str] = {}
    section_stack: list[tuple[str, str]] = []
    passage_index = 0
    version: str | None = None
    last_pdf_page = 0
    last_docx_ordinal = -1

    for block in blocks:
        if not isinstance(block, SourceBlock):
            raise TypeError("Expected SourceBlock stream")
        if version is None:
            version = block.document_version_id
        if block.document_version_id != version or not version or len(version) > 128:
            raise HierarchyIntegrityError("Mixed/invalid document versions")
        if (not block.locator or block.locator in seen_locators or
                not isinstance(block.text, str) or not block.text.strip() or
                len(block.text) > MAX_BLOCK_CHARS):
            raise HierarchyIntegrityError("Duplicate or invalid source block")
        seen_locators.add(block.locator)
        if len(block.section_path) > MAX_SECTION_DEPTH:
            raise HierarchyIntegrityError("Section nesting exceeds limit")

        if block.kind.startswith("pdf_"):
            if (type(block.page_number) is not int or
                    not 1 <= block.page_number <= MAX_PDF_PAGES or
                    block.page_number < last_pdf_page or block.section_path):
                raise HierarchyIntegrityError("Invalid PDF page/section position")
            last_pdf_page = block.page_number
            section_id = None
            parent_id = _id(version, "pdf-page", block.page_number)
            prefix = ""
        elif block.kind.startswith("docx_"):
            if block.page_number is not None or block.ordinal <= last_docx_ordinal:
                raise HierarchyIntegrityError("DOCX order/page position invalid")
            last_docx_ordinal = block.ordinal
            if block.kind == "docx_heading":
                depth = len(block.section_path)
                if not 1 <= depth <= MAX_SECTION_DEPTH:
                    raise HierarchyIntegrityError("DOCX heading missing section depth")
                section_stack = section_stack[:depth - 1]
                section_stack.append((block.text.strip(), _id(version,"section",block.block_id)))
            if tuple(title for title, _ in section_stack) != block.section_path:
                raise HierarchyIntegrityError("DOCX section ancestry mismatch")
            section_id = section_stack[-1][1] if section_stack else None
            if block.kind == "docx_table_row":
                if not block.table_id or type(block.table_row) is not int or block.table_row < 1:
                    raise HierarchyIntegrityError("DOCX table row missing source structure")
                parent_id = _id(version, "table", block.table_id)
            else:
                parent_id = section_id or _id(version, "document-root")
            context = " > ".join(block.section_path)
            prefix = f"Section: {context}\n" if context and block.kind != "docx_heading" else ""
        elif block.kind == "txt_text":
            if block.page_number is not None or block.section_path or block.ordinal <= last_docx_ordinal:
                raise HierarchyIntegrityError("TXT order or source position invalid")
            last_docx_ordinal = block.ordinal
            section_id = None
            parent_id = _id(version, "txt-block", block.block_id)
            prefix = ""
        else:
            raise HierarchyIntegrityError("Unsupported block format")
        if len(prefix) > MAX_CONTEXT_CHARS:
            raise HierarchyIntegrityError("Heading context exceeds budget")
        if _tokens(tokenizer, prefix) + 2 >= max_tokens:
            raise HierarchyIntegrityError("Heading context exceeds token budget")

        text = block.text
        start = 0
        while start < len(text):
            end = _safe_end(tokenizer, text, start, prefix, max_tokens, max_chars)
            excerpt = text[start:end]
            if excerpt.strip():
                embedding_text = prefix + excerpt
                token_count = _tokens(tokenizer, embedding_text)
                if len(embedding_text) > max_chars or token_count > max_tokens:
                    raise HierarchyIntegrityError("Passage unexpectedly exceeded model limits")
                digest = sha256(excerpt.encode("utf-8")).hexdigest()
                passage_id = _id(
                    version, block.block_id, start, end, digest, "e5-small")
                if passage_index >= MAX_PASSAGES:
                    raise HierarchyIntegrityError("Document exceeds indexed passage limit")
                yield HierarchicalPassage(
                    passage_id=passage_id,
                    document_version_id=version,
                    passage_index=passage_index,
                    parent_id=parent_id,
                    section_id=section_id,
                    previous_in_parent=previous_by_parent.get(parent_id),
                    source_block_id=block.block_id,
                    source_locator=block.locator,
                    section_path=block.section_path,
                    source_kind=block.kind,
                    page_number=block.page_number,
                    bbox=block.bbox,
                    char_start=start,
                    char_end=end,
                    text=excerpt,
                    embedding_text=embedding_text,
                    token_count=token_count,
                )
                previous_by_parent[parent_id] = passage_id
                passage_index += 1
            if end == len(text):
                break
            overlap = min(overlap_chars, (end - start) // 4)
            start = end - overlap


class HierarchyManifestBuilder:
    """Incremental provenance proof; never marks an actual version READY.

    add_pdf_page accounts for blank pages as well, preventing silent coverage
    gaps. Source text does not enter the manifest, but IDs and locators do.
    Complete manifest remains an in-memory object until a future encrypted
    persistence layer is explicitly enabled.
    """
    def __init__(self, document_version_id: str, source_sha256: str,
                 *, expected_pages: int | None = None):
        if (not isinstance(document_version_id, str) or
                not document_version_id.strip() or len(document_version_id) > 128):
            raise ValueError("Invalid document version")
        if not isinstance(source_sha256, str) or not HEX_SHA.fullmatch(source_sha256):
            raise ValueError("Immutable source SHA-256 required")
        if expected_pages is not None and (type(expected_pages) is not int or
                                            not 1 <= expected_pages <= MAX_PDF_PAGES):
            raise ValueError("Invalid expected PDF page count")
        self.version = document_version_id
        self.source_digest = source_sha256
        self.expected_pages = expected_pages
        self.pages = 0
        self.blocks: set[str] = set()
        self.parents: set[str] = set()
        self.point_ids: list[str] = []
        self.seen_ids: set[str] = set()
        self._digest = sha256()
        self._sealed = False

    def add_pdf_page(self, page: PdfPageStructure) -> None:
        if self._sealed or self.expected_pages is None:
            raise HierarchyIntegrityError("PDF page accounting is unavailable")
        if not isinstance(page, PdfPageStructure) or page.page_number != self.pages + 1:
            raise HierarchyIntegrityError("PDF page coverage is incomplete or unordered")
        if page.needs_ocr:
            raise HierarchyIntegrityError("PDF has an unresolved OCR page")
        if page.page_number > self.expected_pages:
            raise HierarchyIntegrityError("Unexpected extra PDF page")
        if any(b.page_number != page.page_number or
               b.document_version_id != self.version for b in page.blocks):
            raise HierarchyIntegrityError("PDF page includes an out-of-scope block")
        self.pages += 1

    def add_passage(self, passage: HierarchicalPassage) -> None:
        if self._sealed:
            raise HierarchyIntegrityError("Manifest is already sealed")
        if (not isinstance(passage, HierarchicalPassage) or
                passage.document_version_id != self.version or
                passage.passage_index != len(self.point_ids)):
            raise HierarchyIntegrityError("Index passage scope or sequence changed")
        if passage.passage_id in self.seen_ids:
            raise HierarchyIntegrityError("Duplicate indexed passage ID")
        if (passage.page_number is not None and
                (self.expected_pages is None or passage.page_number > self.pages)):
            raise HierarchyIntegrityError("Passage cites an unaccounted PDF page")
        if len(self.point_ids) >= MAX_PASSAGES:
            raise HierarchyIntegrityError("Passage manifest exceeds 50,000 elements")
        self.point_ids.append(passage.passage_id)
        self.seen_ids.add(passage.passage_id)
        self.blocks.add(passage.source_block_id)
        self.parents.add(passage.parent_id)
        self._digest.update(_canonical({
            "id": passage.passage_id,
            "parent": passage.parent_id,
            "section": passage.section_id,
            "source": passage.source_locator,
            "page": passage.page_number,
            "start": passage.char_start,
            "end": passage.char_end,
        }) + b"\n")

    def finalize(self) -> HierarchyManifest:
        if self._sealed:
            raise HierarchyIntegrityError("Manifest finalized twice")
        if self.expected_pages is not None and self.pages != self.expected_pages:
            raise HierarchyIntegrityError("Incomplete 5,000-page coverage")
        if not self.point_ids:
            raise HierarchyIntegrityError("Cannot index a document with no passages")
        self._sealed = True
        base = {"schema": MANIFEST_SCHEMA, "version": self.version,
                "source_sha256": self.source_digest, "model": MODEL_NAME,
                "expected_pages": self.expected_pages, "accounted_pages": self.pages,
                "passages": len(self.point_ids),
                "blocks": len(self.blocks), "parents": len(self.parents),
                "passage_chain_sha256": self._digest.hexdigest()}
        digest = sha256(_canonical(base)).hexdigest()
        return HierarchyManifest(
            schema=MANIFEST_SCHEMA, document_version_id=self.version,
            source_sha256=self.source_digest, embedding_model=MODEL_NAME,
            expected_pages=self.expected_pages, accounted_pages=self.pages,
            block_count=len(self.blocks), passage_count=len(self.point_ids),
            parent_count=len(self.parents),
            point_ids=tuple(self.point_ids), digest=digest)


def iter_manifested_pdf_passages(
    pages: Iterable[PdfPageStructure], builder: HierarchyManifestBuilder,
    tokenizer,
) -> Iterator[HierarchicalPassage]:
    """Stream ordered PDF pages; account for blanks before indexing text."""
    if builder.expected_pages is None:
        raise ValueError("PDF manifest must declare its original page count")
    def source() -> Iterator[SourceBlock]:
        for page in pages:
            builder.add_pdf_page(page)
            yield from page.blocks
    for passage in iter_hierarchical_passages(source(), tokenizer):
        builder.add_passage(passage)
        yield passage


def iter_manifested_docx_passages(
    blocks: Iterable[SourceBlock], builder: HierarchyManifestBuilder,
    tokenizer,
) -> Iterator[HierarchicalPassage]:
    if builder.expected_pages is not None:
        raise ValueError("DOCX has no verified PDF page count")
    for passage in iter_hierarchical_passages(blocks, tokenizer):
        builder.add_passage(passage)
        yield passage
