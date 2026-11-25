# Phase 4.3D — Checksum-pinned local E5 reference inference

## Goal and boundary

DocMind AI should answer questions about private English/Bangla documents,
including facts deep inside very large books, with traceable source evidence.
A vector index is only useful if its model and preprocessing are reproducible.
Phase 4.3C verifies numerical fidelity but takes a caller-supplied model; the
existing v1 FastEmbed loader is not revision/checksum pinned.

This batch supplies a separate **offline, test-only v2 reference path**. It
does not switch normal retrieval, change v1, modify schema, persist a new
candidate, activate shadow data or run jobs against protected QA knowledge bases.

## Implementation

- ModelArtifactPin requires an operator-approved immutable 40-hex repository
  revision, exact SHA-256 of the ONNX file and tokenizer.json, and output name.
  Its canonical digest includes the E5 model and preprocessing profile.
- PinnedE5Model reads bounded local artifacts, verifies both SHA-256 hashes
  before constructing a tokenizer or ONNX Runtime session, and loads the
  exact verified bytes rather than reopening paths. No downloader or fallback.
  Only CPU, standard int64 input tensors, optional token types, and a named
  float32 token-level 384-dimensional output are supported.
- Tokenization disables implicit padding/truncation. Inference enforces a
  512-token limit, explicit E5 prefixes, bounded batches, correct attention
  masking, mean pooling, finite values and L2 normalization. The adapter
  implements encode() for the existing hierarchy token counter and embed()
  for staging/fidelity experiments; it is not wired into live ingestion.
- verify_pinned_shadow() requires an explicit test-only flag, refuses
  production and protected QA scopes, uses the existing full source/vector
  audit and fixed 0.999 cosine minimum, and checks the processing lease
  before and after. Its in-memory receipt binds the artifact pin, source and
  ledger hashes to the existing vector audit. It cannot publish or query.
- CI executes the actual tokenizer and ONNX Runtime with a tiny deterministic
  synthetic token graph, testing padding invariance, verified-byte loading,
  malformed pins, tampering, token/batch limits, and the audit boundary.
  The synthetic graph is not the actual multilingual E5 model.

## Operator notes and remaining gates

Use only artifacts obtained from a trusted publisher and independently
approved revision/hash manifest; never accept model files supplied by users.
The revision label itself is operator-supplied, not a verified signature or
proof that bytes came from that repository. The adapter deliberately does not
support ONNX models requiring separately loaded external tensor data.
Use the existing local worker environment (FastEmbed depends on ONNX Runtime,
tokenizers and NumPy). No secret or real model is bundled. The dedicated
test command from python-worker is:

    python -m unittest worker.test_pinned_e5

Tests additionally need the onnx package to construct the synthetic graph.

Before activation: verify the real E5 export and numerical equivalence on the
actual target hardware, pin runtime and model provenance in durable
generation metadata, implement a v2 reader with fresh relational permission
and generation checks, and design transactional activation/revocation.
The receipt is not signed, persisted or a substitute for those checks.
Do not reuse a generation's point IDs across distinct staged generations;
the existing shadow point identity and recovery protocol also require
review before any multi-generation cutover. Large-book real OCR/layout and
hybrid retrieval quality/latency acceptance remain pending.

No production data operations, migration, public endpoint or model download
are part of this change.
