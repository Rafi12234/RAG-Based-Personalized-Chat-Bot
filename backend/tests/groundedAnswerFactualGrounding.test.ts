/** Phase 3.4: factual grounding regression tests using synthetic, authorized sources. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { HttpError } from "../src/lib/http.js";
import { generateGroundedAnswer, validateModelAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const security: VerifiedSource = {
  chunkId: "123e4567-e89b-42d3-a456-426614174000", documentId: "security-doc",
  documentVersionId: "sec-ver", documentTitle: "Security.txt", chunkIndex: 0,
  pageNumber: null, score: 0.84,
  excerpt: "DocMind-AI checks the user's authenticated session and organization membership before retrieving document chunks.\nCross-organization access to an inaccessible knowledge base must return a 404 error without disclosing document text."
};
const storage: VerifiedSource = {
  ...security, chunkId: "123e4567-e89b-42d3-a456-426614174001",
  documentId: "storage-doc", documentVersionId: "storage-ver",
  documentTitle: "Storage.txt", score: 0.78,
  excerpt: "RustFS is the S3-compatible object store that holds uploaded source documents.\nQdrant stores document vectors, not the original binary files."
};
const vectors: VerifiedSource = {
  ...security, chunkId: "123e4567-e89b-42d3-a456-426614174002",
  documentId: "vectors-doc", documentVersionId: "vectors-ver",
  documentTitle: "Vectors.txt", score: 0.82,
  excerpt: "DocMind-AI stores document embeddings in Qdrant, a vector database.\nSimilarity uses cosine distance."
};
const abstain = JSON.stringify({ abstain: true, answer: "", source_id: "", evidence_quote: "" });
const supported = (answer: string, source_id: string, evidence_quote: string) =>
  JSON.stringify({ abstain: false, answer, source_id, evidence_quote });

test("factual: verified English status code is extracted without guessing", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer(
    "What HTTP status code is returned when accessing an inaccessible knowledge base?",
    [security, storage], async () => { calls++; return abstain; }
  );
  assert.equal(result.answer, "HTTP 404 [S1].");
  assert.equal(result.citations[0].documentId, security.documentId);
  assert.equal(calls, 0);
});
test("factual: mixed question uses correct security source", async () => {
  const result = await generateGroundedAnswer("Cross-org KB access কোন HTTP status return করে?", [security, storage], async () => {
    throw new Error("No model call expected");
  });
  assert.equal(result.answer, "HTTP 404 [S1].");
  assert.equal(result.citations[0].documentTitle, "Security.txt");
});
test("factual: unrelated HTTP question cannot borrow 404", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer("What HTTP status code does a rocket launch return?", [security], async () => {
    calls++; return abstain;
  });
  assert.equal(result.status, "insufficient_evidence");
  assert.deepEqual(result.citations, []);
  assert.equal(calls, 2);
});
test("factual: conflicting codes cannot be auto-answered", async () => {
  const conflict = { ...security, chunkId:"123e4567-e89b-42d3-a456-426614174003", documentId:"another-doc",
    excerpt:"Cross-organization access to an inaccessible knowledge base must return a 403 error." };
  let calls = 0;
  const result = await generateGroundedAnswer("What HTTP status code is returned for an inaccessible knowledge base?", [security, conflict], async () => {
    calls++; return abstain;
  });
  assert.equal(result.status, "insufficient_evidence");
  assert.equal(calls, 2);
});
test("factual: unrelated answer cannot borrow real quote", () => {
  assert.throws(() => validateModelAnswer(supported("Qdrant", "S1", "E1_1"), [storage]), HttpError);
});
test("factual: unnecessary model abstention retries once with focused evidence", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer("Which system contains DocMind-AI document embeddings?", [vectors], async prompt => {
    calls++;
    if (calls === 1) return abstain;
    assert.match(prompt, /SOURCE FACTS/);
    return supported("Qdrant", "S1", "E1_1");
  });
  assert.equal(result.answer, "Qdrant [S1].");
  assert.equal(calls, 2);
});
test("factual: model's unrelated but genuine citation cannot answer Atlantis question", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer("What is the favorite food of the founder of Atlantis?", [vectors], async () => {
    calls++; return calls === 1 ? supported("Qdrant", "S1", "E1_1") : abstain;
  });
  assert.equal(result.status, "insufficient_evidence");
  assert.equal(calls, 2);
});
test("factual: focused retry cannot cite different source", async () => {
  let calls = 0;
  await assert.rejects(generateGroundedAnswer("Where are uploaded source documents stored?", [vectors, storage], async () => {
    calls++; return calls === 1 ? abstain : supported("Qdrant", "S1", "E1_1");
  }), HttpError);
  assert.equal(calls, 2);
});
test("factual: mixed-language storage query uses correct verified source without model", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer("DocMind-AI source documents কোন object store-এ থাকে?", [vectors, storage], async () => {
    calls++;
    throw new Error("Explicit verified named fact should not invoke the model");
  });
  assert.equal(result.answer, "RustFS [S2].");
  assert.equal(result.citations[0].documentId, storage.documentId);
  assert.equal(calls, 0);
});
