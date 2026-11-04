/** Phase 3.5 regression: the model abstains twice despite explicit, verified named facts.
 * No fixture-specific answers in production code; no vector or database writes.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

function source(documentTitle: string, excerpt: string, score: number, suffix: string): VerifiedSource {
  return {
    chunkId: `123e4567-e89b-42d3-a456-42661417400${suffix}`,
    documentId: `doc-${suffix}`, documentVersionId: `ver-${suffix}`,
    documentTitle, chunkIndex: 0, pageNumber: null, score, excerpt,
  };
}
const security = source("Security.txt", "Cross-organization access to an inaccessible knowledge base must return a 404 error.", 0.9, "0");
const storage = source("Storage.txt", "RustFS is the S3-compatible object store that holds uploaded source documents.\nQdrant stores document vectors, not the original PDF files.", 0.84, "1");
const vectors = source("Vectors.txt", "DocMind-AI stores document embeddings in Qdrant, a vector database.\nSimilarity uses cosine distance.", 0.82, "2");
const abstain = JSON.stringify({ abstain: true, answer: "", source_id: "", evidence_quote: "" });
async function answer(question: string, sources: VerifiedSource[]) {
  let calls = 0;
  const result = await generateGroundedAnswer(question, sources, async () => { calls++; return abstain; });
  return { result, calls };
}
for (const [question, expected, title] of [
  ["Which vector database stores DocMind-AI embeddings?", "Qdrant [S3].", "Vectors.txt"],
  ["ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?", "Qdrant [S3].", "Vectors.txt"],
  ["আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?", "RustFS [S2].", "Storage.txt"],
  ["DocMind-AI source documents কোন object store-এ থাকে?", "RustFS [S2].", "Storage.txt"],
] as const) {
  test(`phase3.5: explicit fact after two abstentions: ${question}`, async () => {
    const { result, calls } = await answer(question, [security, storage, vectors]);
    assert.equal(result.answer, expected);
    assert.equal(result.citations[0]?.documentTitle, title);
    // Every explicitly recognized infrastructure-name question must be answered
    // from verified source text without an LLM (English, Bangla, or mixed).
    assert.equal(calls, 0);
  });
}
test("phase3.5: unsupported English/Bangla/mixed questions abstain without citations", async () => {
  for (const question of [
    "What is the favorite food of the founder of Atlantis?",
    "চাঁদের রাজপ্রাসাদের ফোন নম্বর কত?",
    "DocMind-AI এর Mars headquarters address কী?",
  ]) {
    const { result, calls } = await answer(question, [security, storage, vectors]);
    assert.equal(result.status, "insufficient_evidence");
    assert.deepEqual(result.citations, []);
    assert.equal(calls, 2);
  }
});
test("phase3.5: missing matching source cannot create a named answer", async () => {
  assert.equal((await answer("Which vector database stores DocMind-AI embeddings?", [security, storage])).result.status, "insufficient_evidence");
});
test("phase3.5: conflicting named facts force abstention", async () => {
  const other = source("Conflicting.txt", "DocMind-AI stores document embeddings in FakeDB, a vector database.", 0.7, "3");
  assert.equal((await answer("Which vector database stores DocMind-AI embeddings?", [vectors, other])).result.status, "insufficient_evidence");
});
test("phase3.5: object-store question cannot borrow vector evidence", async () => {
  assert.equal((await answer("DocMind-AI source documents কোন object store-এ থাকে?", [vectors])).result.status, "insufficient_evidence");
});
test("phase3.5: generic vector question cannot trigger narrow fallback", async () => {
  assert.equal((await answer("Which database stores vectors?", [vectors])).result.status, "insufficient_evidence");
});
