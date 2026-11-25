# Phase 4.4–4.8 — Advanced RAG implementation batch

Goal: secure private document assistance for students, researchers,
professionals and organizations; exact source evidence in English/Bangla,
including deeply buried facts in long books. Do not claim production readiness
from synthetic tests.

## Implemented engineering scope

| Phase | Code |
|---|---|
| 4.4 | Per-session, read-only source-verified BM25 index, rare identifiers, global dense and lexical branches, section routing with global fallback, weighted RRF. Multiple selected documents within ONE KB. |
| 4.5 | Local checksum-pinned ONNX pair-classifier reranker, bounded batches, diversity/dedup, verified same-parent neighbor expansion, caller-supplied exact full-prompt token counter. |
| 4.6 | Bounded explicit subqueries, structured claim drafts, exact unique quote spans, numeric checks, pinned multilingual NLI entailment and conflict checks, whole-answer abstention, no tool execution, final authorization after generation. |
| 4.7 | Unicode NFKC/NFC normalization, Bengali combining marks, Bengali/ASCII numeral equivalence, code-switched identifiers, per-language measurements. Original source bytes/text remain unchanged. |
| 4.8 | Retrieval recall@10/50, all-evidence recall, MRR, nDCG, p50/95/99, error-inclusive denominators and size/language splits. Real generated native PDF 500/2,000/5,000-page lexical build/lookup smoke, page accounting, measured Python allocations/throughput. |

## Integration and safety

Use AdvancedShadowSession with server-derived SessionScope, selected
document identities, authenticated ledgers, original processing tokens,
a pinned E5 model, fresh SQL connection factory and shadow Qdrant client.
It calls the 4.3E authorization/provenance SQL before reading source, at
bounded scroll and dense search boundaries, and before returning context
or answers. All selected documents must belong to the same tenant/KB.
Only the explicit development/test flag from 4.3E is accepted; protected
QA KBs are rejected. The existing queryEnabled=FALSE CHECK remains.
No HTTP endpoint, task wiring, migrations, live collection changes, automatic
activation, or model download. Corpus plaintext is held only in the
trusted evaluator's memory, never serialized or logged. Do not reuse
sessions across requests from different users; release the object after
the evaluator session ends. No process-wide caching.

Corpus limit: eight selected documents, 50,000 passages, 64 MiB text,
two million unique term/section postings. A corpus is built once by scanning
exact scoped shadow points with ledger hash checks. It is not persisted,
nor a production-wide sparse index. Query cost still depends on posting
length and number of SQL calls. Dense retrieval requests up to 50 per
document, fuses global top 50; reranking up to 80; context up to 20.
No guarantee of full-book coverage or recall. Reranker and NLI require
approved local pair ONNX exports, exact hashes, revision and explicit
label order. A model tag/revision label is not a signature. The generator
is a trusted local no-tools callable, not supplied by users; it receives
a structured prompt. The caller must provide an exact full-prompt token
counter including system instructions, tokenizer template and reserved
completion tokens. The source excerpt offsets are passage-relative.
NLI is a probabilistic classifier, not a proof of truth or completeness.
Numbers must occur in cited quotes; arithmetic derivations are conservatively
rejected. Explicit subqueries are caller-supplied; automatic question
decomposition/transliteration/translation is not implemented. Conflict
detection sees only packed evidence, not the entire book.

## Run the checks

From python-worker:

    python -m unittest worker.test_advanced_rag worker.test_local_pair_model
    python -m worker.advanced_scale_smoke
    python -m worker.advanced_rag_metrics --input <independent-evaluation-json>

The scale smoke generates and removes temporary PDFs. It uses a synthetic
token counter, actual PyMuPDF extraction and hierarchy/BM25. It does NOT run
real E5, NLI, OCR, Qdrant or answer generation. The pair model test uses
a real runtime and synthetic graph. Integration tests use simulated
Qdrant/SQL/model with the actual orchestration. Previous CI retains the
real isolated MySQL authorization and migration integration tests.

The metrics CLI accepts {dataset_kind,hardware,model_config,cases};
each case has id,language,pages,gold (exact evidence IDs), rankings (dense,
lexical,hybrid,reranked), latency_ms and status (ok/error). Missing model
runs should not be represented as successful rankings. Use the existing
Phase 4.1 human adjudication scorer for factual/citation/abstention labels;
this retrieval-only metric does not score answers automatically.

## Acceptance still open

4.3F safe orchestration/cross-store activation, durable sparse artifact
generation and cache invalidation, end-to-end model/hardware calibration,
real multilingual/OCR/table evaluation, independent >=100 labeled cases
on >=10 docs, real 5,000-page end-to-end ingest/retrieve/answer with
measured latency/concurrency/recovery, operational quotas/worker isolation,
and Phase 5 release validation remain mandatory. No production SLO or
accuracy gate is declared passed based on this batch. Existing protected
data and live v1 path remain unchanged.
