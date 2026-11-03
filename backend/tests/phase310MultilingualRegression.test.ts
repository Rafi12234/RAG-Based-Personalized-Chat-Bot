/** Phase 3.10 live multilingual regression: all source facts are synthetic and scoped. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const mk = (excerpt: string, suffix: number, title: string): VerifiedSource => ({
  chunkId: `123e4567-e89b-42d3-a456-42661417400${suffix}`,
  documentId: `qa-doc-${suffix}`,
  documentVersionId: `qa-version-${suffix}`,
  documentTitle: title,
  chunkIndex: 0,
  pageNumber: null,
  excerpt,
  score: 0.91 - suffix / 100,
});
const vectors = mk(
  "DocMind-AI QA fixture: Vector search\n\nDocMind-AI stores document embeddings in Qdrant, a vector database.\nThe embedding model produces 384-dimensional vectors.",
  1, "phase3_3d_vectors.txt",
);
const security = mk(
  "Cross-organization access to an inaccessible knowledge base must return a 404 error.",
  2, "phase3_3d_security.txt",
);
const storage = mk(
  "passage: DocMind-AI QA fixture: Infrastructure\n\nRustFS is the S3-compatible object store that holds uploaded source documents.\nMySQL stores account records. Qdrant stores document vectors, not original PDF files.",
  3, "phase3_3d_storage.txt",
);
const expected: Array<[string, string, string]> = [
  ["ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?", "Qdrant [S1].", vectors.documentTitle],
  ["আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?", "RustFS [S3].", storage.documentTitle],
  ["DocMind-AI source documents কোন object store-এ থাকে?", "RustFS [S3].", storage.documentTitle],
];

for (const [question, answer, title] of expected) {
  test("phase3.10 multilingual: evidence-first, no model: " + question, async () => {
    const result = await generateGroundedAnswer(question, [vectors, security, storage], async () => {
      throw new Error("Ollama must not be called for explicit verified infrastructure facts");
    });
    assert.equal(result.status, "answered");
    assert.equal(result.answer, answer);
    assert.deepEqual(result.citations.map(c => c.documentTitle), [title]);
    assert.equal(result.citations[0].sourceId, answer.match(/\[(S\d+)\]/u)?.[1]);
  });
}

test("phase3.10 multilingual: verified source may appear at another rank", async () => {
  const result = await generateGroundedAnswer(
    "ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?",
    [storage, vectors], async () => { throw new Error("Model must not run"); },
  );
  assert.equal(result.answer, "Qdrant [S2].");
  assert.equal(result.citations[0].documentTitle, vectors.documentTitle);
});

test("phase3.10 multilingual: absence of correct fact abstains without guessing", async () => {
  const result = await generateGroundedAnswer(
    "আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?",
    [vectors, security], async () => { throw new Error("Model must not run"); },
  );
  assert.equal(result.status, "insufficient_evidence");
  assert.deepEqual(result.citations, []);
});

test("phase3.10 multilingual: contradictory object stores abstain without a citation", async () => {
  const conflicting = mk(
    "AlphaStore is the S3-compatible object store that holds uploaded source documents.",
    4, "conflicting.txt",
  );
  const result = await generateGroundedAnswer(
    "DocMind-AI source documents কোন object store-এ থাকে?",
    [storage, conflicting], async () => { throw new Error("Model must not run"); },
  );
  assert.equal(result.status, "insufficient_evidence");
  assert.deepEqual(result.citations, []);
});

test("phase3.10 multilingual: contradictory embedding vector databases abstain", async () => {
  const conflicting = mk(
    "DocMind-AI stores document embeddings in AlphaDB, a vector database.",
    4, "conflicting.txt",
  );
  const result = await generateGroundedAnswer(
    "ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?",
    [vectors, conflicting], async () => { throw new Error("Model must not run"); },
  );
  assert.equal(result.status, "insufficient_evidence");
  assert.deepEqual(result.citations, []);
});

test("phase3.10 multilingual: title alone cannot confer factual authority", async () => {
  const bait = mk("There is no stated storage technology in this passage.", 4, "RustFS.txt");
  const result = await generateGroundedAnswer(
    "আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?",
    [bait], async () => { throw new Error("Model must not run"); },
  );
  assert.equal(result.status, "insufficient_evidence");
  assert.deepEqual(result.citations, []);
});

test("phase3.10 multilingual: unrelated Bengali query is not routed into named-fact extraction", async () => {
  let requests = 0;
  const abstain = JSON.stringify({abstain:true,answer:"",source_id:"",evidence_quote:""});
  const result = await generateGroundedAnswer(
    "চাঁদের রাজপ্রাসাদের ফোন নম্বর কত?", [vectors, storage], async () => {
      requests++;
      return abstain;
    },
  );
  assert.equal(result.status, "insufficient_evidence");
  assert.deepEqual(result.citations, []);
  assert.equal(requests, 2);
});
