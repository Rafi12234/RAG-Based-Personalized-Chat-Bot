"""Crash-recoverable, version-bound PDF page-range checkpoints (Phase 4.2A).

This module does NOT connect to MySQL, Qdrant, Redis or an LLM. A caller must
authorize a document version, own its processing lease and supply a private
checkpoint directory. Cached extraction contains PRIVATE document text: do
not enable this feature on shared/untrusted storage.

Every checkpoint is bound to the immutable source SHA-256, document version,
exact page range and per-page text hashes. It is written atomically only after
an entire shard is extracted. A corrupted or mismatched checkpoint FAILS
CLOSED; it is never silently skipped or trusted as READY content.

Durability is for same-machine worker restart, not multi-host HA. Production
object-store checkpointing and source encryption are separate milestones.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterable, Iterator
import json
import os
import re
import tempfile

MAX_PAGES = 5000
DEFAULT_SHARD_PAGES = 32
MAX_SHARD_PAGES = 128
MAX_PAGE_CHARS = 4_000_000
MAX_CHECKPOINT_BYTES = 80 * 1024 * 1024
VERSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
METHODS = frozenset({"native", "ocr", "blank"})


class ShardIntegrityError(ValueError):
    """Missing source coverage, tampering, or incompatible checkpoint."""


@dataclass(frozen=True)
class PageRecord:
    page_number: int
    text: str
    method: str  # native, ocr or blank


@dataclass(frozen=True)
class ShardRange:
    first: int  # inclusive, 1-based
    last: int   # inclusive, 1-based


def shard_ranges(page_count: int, *, shard_pages: int = DEFAULT_SHARD_PAGES) -> Iterator[ShardRange]:
    if type(page_count) is not int or not 1 <= page_count <= MAX_PAGES:
        raise ValueError("Document page count must be 1..5000")
    if type(shard_pages) is not int or not 1 <= shard_pages <= MAX_SHARD_PAGES:
        raise ValueError("Shard size must be 1..128 pages")
    for first in range(1, page_count + 1, shard_pages):
        yield ShardRange(first, min(page_count, first + shard_pages - 1))


def source_sha256(path: Path | str) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_record(item: PageRecord, expected: int) -> None:
    if not isinstance(item, PageRecord) or type(item.page_number) is not int or item.page_number != expected:
        raise ShardIntegrityError("Page range is incomplete, duplicated or out of order")
    if type(item.text) is not str or len(item.text) > MAX_PAGE_CHARS:
        raise ShardIntegrityError("Invalid or oversized page text")
    if item.method not in METHODS:
        raise ShardIntegrityError("Invalid extraction method")
    if item.method == "ocr" and not item.text.strip():
        raise ShardIntegrityError("OCR page has no extracted text")
    if item.method == "blank" and item.text.strip():
        raise ShardIntegrityError("Blank page contains text")


class ShardCheckpointStore:
    """Private, atomic, version-scoped local checkpoint store.

    The store creates only its own version directory. It refuses symlinked
    roots, version dirs and checkpoint files. It never removes files or
    modifies any other document version. Do not use an attacker-writable root.
    """
    def __init__(self, root: Path | str, version_id: str, source_digest: str,
                 page_count: int, *, shard_pages: int = DEFAULT_SHARD_PAGES) -> None:
        if not isinstance(version_id, str) or not VERSION_RE.fullmatch(version_id):
            raise ValueError("Unsafe document version key")
        if not isinstance(source_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", source_digest):
            raise ValueError("Source SHA-256 is required")
        self.ranges = tuple(shard_ranges(page_count, shard_pages=shard_pages))
        self.page_count = page_count
        self.version_id = version_id
        self.source_digest = source_digest
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise ShardIntegrityError("Symlinked checkpoint root is forbidden")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Reject an unexpectedly accessible POSIX directory; Windows ACLs must
        # be managed by the deployment. This is opt-in local processing only.
        if os.name == "posix" and self.root.stat().st_mode & 0o077:
            raise ShardIntegrityError("Checkpoint root must be private (0700)")
        self.version_dir = self.root / version_id
        if self.version_dir.is_symlink():
            raise ShardIntegrityError("Symlinked version directory is forbidden")
        self.version_dir.mkdir(mode=0o700, exist_ok=True)
        if os.name == "posix" and self.version_dir.stat().st_mode & 0o077:
            raise ShardIntegrityError("Checkpoint version directory must be private")

    def _path(self, shard: ShardRange) -> Path:
        if shard not in self.ranges:
            raise ValueError("Unrecognized shard range")
        return self.version_dir / f"pages-{shard.first:05d}-{shard.last:05d}.json"

    def _metadata(self, shard: ShardRange) -> dict:
        return {"schema": 1, "version": self.version_id,
                "source_sha256": self.source_digest, "page_count": self.page_count,
                "first_page": shard.first, "last_page": shard.last}

    def read(self, shard: ShardRange) -> tuple[PageRecord, ...] | None:
        path = self._path(shard)
        if path.is_symlink():
            raise ShardIntegrityError("Symlinked checkpoint is forbidden")
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > MAX_CHECKPOINT_BYTES:
            raise ShardIntegrityError("Invalid checkpoint size")
        try:
            raw = path.read_bytes()
            document = json.loads(raw)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ShardIntegrityError("Checkpoint cannot be decoded") from exc
        if (type(document) is not dict or
                set(document) != {"scope", "pages", "checksum"} or
                document["scope"] != self._metadata(shard) or
                type(document["pages"]) is not list):
            raise ShardIntegrityError("Checkpoint scope mismatch")
        expected_checksum = sha256(json.dumps(
            {"scope": document["scope"], "pages": document["pages"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if document["checksum"] != expected_checksum:
            raise ShardIntegrityError("Checkpoint checksum mismatch")
        pages = document["pages"]
        if len(pages) != shard.last - shard.first + 1:
            raise ShardIntegrityError("Checkpoint missing pages")
        result: list[PageRecord] = []
        for n, page in enumerate(pages, shard.first):
            if type(page) is not dict or set(page) != {"page", "text", "method", "sha256"}:
                raise ShardIntegrityError("Invalid page checkpoint schema")
            record = PageRecord(page["page"], page["text"], page["method"])
            _check_record(record, n)
            if sha256(record.text.encode("utf-8")).hexdigest() != page["sha256"]:
                raise ShardIntegrityError("Page text checksum mismatch")
            result.append(record)
        return tuple(result)

    def write(self, shard: ShardRange, pages: Iterable[PageRecord]) -> None:
        path = self._path(shard)
        if path.exists() or path.is_symlink():
            # Never overwrite a previously saved, possibly conflicting shard.
            if self.read(shard) is None:
                raise ShardIntegrityError("Unexpected checkpoint state")
            raise ShardIntegrityError("Checkpoint already exists")
        records: list[dict] = []
        size = 0
        for n, record in enumerate(pages, shard.first):
            if n > shard.last:
                raise ShardIntegrityError("Too many pages in shard")
            _check_record(record, n)
            size += len(record.text.encode("utf-8"))
            if size > MAX_CHECKPOINT_BYTES // 2:
                raise ShardIntegrityError("Page shard exceeds safe text budget")
            records.append({"page": record.page_number, "text": record.text,
                            "method": record.method,
                            "sha256": sha256(record.text.encode("utf-8")).hexdigest()})
        if len(records) != shard.last - shard.first + 1:
            raise ShardIntegrityError("Incomplete shard cannot be checkpointed")
        unsigned = {"scope": self._metadata(shard), "pages": records}
        checksum = sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
        encoded = json.dumps({**unsigned, "checksum": checksum},
                             ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_CHECKPOINT_BYTES:
            raise ShardIntegrityError("Checkpoint serialized size exceeded")
        fd, name = tempfile.mkstemp(prefix=".pending-", suffix=".json",
                                   dir=self.version_dir)
        pending = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                os.chmod(pending, 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if path.exists() or path.is_symlink():
                raise ShardIntegrityError("Checkpoint appeared during write")
            # The lock/lease owner is responsible for exclusivity; an atomic
            # rename prevents partially visible files, not concurrent writers.
            os.replace(pending, path)
        finally:
            if pending.exists():
                pending.unlink()


def iter_checkpointed_pages(
    store: ShardCheckpointStore,
    extract: Callable[[int, int], Iterable[PageRecord]],
    *, max_ocr_pages: int = MAX_PAGES,
) -> Iterator[PageRecord]:
    """Resume by shard, validating ALL pages before yielding each shard.

    The extraction callback must be page-range aware. Every produced page is
    checked before saving and yielding; a failed shard never gets a checkpoint.
    OCR budget includes cached pages so resume cannot silently bypass limits.
    """
    if type(max_ocr_pages) is not int or not 0 <= max_ocr_pages <= MAX_PAGES:
        raise ValueError("Invalid OCR page budget")
    if not callable(extract):
        raise TypeError("extract must be callable")
    total_ocr = 0
    for shard in store.ranges:
        cached = store.read(shard)
        if cached is None:
            fresh = tuple(extract(shard.first, shard.last))
            store.write(shard, fresh)
            records = fresh
        else:
            records = cached
        for record in records:
            total_ocr += record.method == "ocr"
            if total_ocr > max_ocr_pages:
                raise ShardIntegrityError("Document exceeds its total OCR page budget")
            yield record
