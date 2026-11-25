"""Phase 4.4/4.7: Unicode-safe local BM25 + RRF, immutable per-session corpus."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from heapq import nsmallest
from math import log1p, isfinite
from types import MappingProxyType
import unicodedata as ud

MAX_PASSAGES = 50_000
MAX_TEXT_BYTES = 64 * 1024 * 1024
MAX_POSTINGS = 2_000_000
MAX_QUERY_TERMS = 96
MAX_BRANCH = 100


def normalize(text: str) -> str:
    """Search-only normalization; original source is never rewritten."""
    text = ud.normalize("NFKC", text).casefold()
    result = []
    for c in text:
        if c in ("\u200c", "\u200d", "\u200b", "\ufeff"):
            continue
        if c.category() == "Nd":
            c = str(ud.decimal(c))
        result.append(c)
    return ud.normalize("NFC", "".join(result))


def terms(text: str) -> tuple[str, ...]:
    """Keep Bengali combining marks and decimal/identifier punctuation.

    Index components AND full hyphen/decimal identifiers without replacing
    source spans. No automatic transliteration or translation guesses.
    """
    out = []
    current = []
    for c in normalize(text):
        if ud.category(c)[0] in "LNM":
            current.append(c)
        elif c in "-_.:/":
            if current:
                current.append(c)
        else:
            if current:
                out.append("".join(current).strip("-_.:/"))
                current = []
    if current:
        out.append("".join(current).strip("-_.:/"))
    expanded = []
    for t in out:
        if not t:
            continue
        expanded.append(t)
        if any(c in t for c in "-_.:/"):
            parts = t.translate(str.maketrans({c: " " for c in "-_.:/"})).split()
            expanded.extend(parts)
    return tuple(expanded)


def language(text: str) -> str:
    bn = any("\u0980" <= c <= "\u09ff" and ud.category(c)[0] in "LM" for c in text)
    en = any("LATIN" in ud.name(c, "") and ud.category(c)[0] == "L" for c in text)
    return "mixed" if bn and en else "bangla" if bn else "english" if en else "other"


@dataclass(frozen=True)
class Passage:
    id: str
    document_id: str
    version_id: str
    generation: str
    title: str
    page: int | None
    locator: str
    section: tuple[str, ...]
    parent: str
    previous: str | None
    text: str


class BM25:
    """Bounded, reusable global inverted index; no per-query corpus scan.

    Each corpus belongs to a single authenticated session and selected KB.
    Build once after complete source verification. Never serialize plaintext.
    """
    def __init__(self, passages):
        docs = {}
        postings = defaultdict(list)
        section_postings = defaultdict(set)
        lengths = {}
        total_bytes = total_posts = 0
        for p in passages:
            if (type(p) is not Passage or p.id in docs or not p.text.strip() or
                    len(docs) >= MAX_PASSAGES):
                raise ValueError("Invalid or oversized lexical corpus")
            total_bytes += len(p.text.encode("utf-8")) + sum(len(s.encode("utf-8")) for s in p.section)
            if total_bytes > MAX_TEXT_BYTES:
                raise ValueError("Lexical corpus byte budget exceeded")
            words = terms(p.text)
            freqs = Counter(words)
            total_posts += len(freqs)
            if total_posts > MAX_POSTINGS:
                raise ValueError("Lexical posting budget exceeded")
            for word, count in freqs.items():
                postings[word].append((p.id, count))
            # Route sections through unique indexed terms, not per-query scans.
            for word in set(terms(" ".join(p.section))):
                key=(p.version_id,p.section)
                if key not in section_postings[word]:
                    total_posts += 1
                    if total_posts > MAX_POSTINGS:
                        raise ValueError("Lexical posting budget exceeded")
                    section_postings[word].add(key)
            docs[p.id] = p
            lengths[p.id] = len(words)
        if not docs:
            raise ValueError("Empty corpus")
        self.docs = MappingProxyType(docs)
        self._postings = MappingProxyType({k: tuple(v) for k, v in postings.items()})
        self._sections = MappingProxyType({k: frozenset(v) for k, v in section_postings.items()})
        self._lengths = MappingProxyType(lengths)
        self._avg = max(1., sum(lengths.values()) / len(docs))
        self.text_bytes = total_bytes
        self.postings_count = total_posts

    def rank(self, question: str, limit: int = 50, *, sections: bool = False):
        if type(limit) is not int or not 1 <= limit <= MAX_BRANCH:
            raise ValueError("Invalid lexical result bound")
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            raise ValueError("Invalid query")
        q = tuple(dict.fromkeys(terms(question)))
        if len(q) > MAX_QUERY_TERMS:
            raise ValueError("Query term budget exceeded")
        scores = defaultdict(float)
        routes = Counter()
        if sections:
            for t in q:
                for s in self._sections.get(t, ()):
                    routes[s] += 1
        # Highest matching sections are a bonus branch only; never replace global.
        selected = set(sorted(routes, key=lambda x: (-routes[x], x))[:8])
        if sections and not selected:
            return ()
        n = len(self.docs)
        for word in q:
            posting = self._postings.get(word, ())
            if not posting:
                continue
            idf = log1p((n - len(posting) + .5) / (len(posting) + .5))
            for key, tf in posting:
                p = self.docs[key]
                if sections and (p.version_id, p.section) not in selected:
                    continue
                norm = 1.2 * (.25 + .75 * self._lengths[key] / self._avg)
                scores[key] += idf * tf * 2.2 / (tf + norm)
        return tuple(nsmallest(limit, scores, key=lambda k: (-scores[k], k)))


def reciprocal_rank_fusion(branches, *, limit: int = 50, k: int = 60):
    """Deterministic weighted RRF; ranks, not incomparable raw scores."""
    if type(limit) is not int or not 1 <= limit <= MAX_BRANCH or type(k) is not int or k < 1:
        raise ValueError("Invalid fusion parameters")
    if not 1 <= len(branches) <= 12:
        raise ValueError("Invalid branch count")
    scores = defaultdict(float)
    for ids, weight in branches:
        if (type(weight) not in (int, float) or not isfinite(weight) or
                not 0 < weight <= 4 or len(ids) > MAX_BRANCH or len(set(ids)) != len(ids)):
            raise ValueError("Invalid ranked branch")
        for rank, key in enumerate(ids, 1):
            scores[key] += weight / (k + rank)
    return tuple(sorted(scores, key=lambda key: (-scores[key], key))[:limit])
