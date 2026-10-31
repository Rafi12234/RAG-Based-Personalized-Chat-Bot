/** Phase 3.4 Qwen compatibility and fail-closed citation regression tests; offline only. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { HttpError } from "../src/lib/http.js";
import { generateGroundedAnswer, validateModelAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const source: VerifiedSource = {
  chunkId: "123e4567-e89b-42d3-a456-426614174000", documentId: "doc-A",
  documentVersionId: "ver-A", documentTitle: "QA.txt", chunkIndex: 0,
  pageNumber: null, excerpt: "Qdrant stores document vectors.", score: 0.86,
};

const raw = (answer: string) => JSON.stringify({ abstain: false, answer });

test("compatibility: accepts a citation after sentence punctuation", () => {
  const result = validateModelAnswer(raw("Qdrant stores document vectors. [S1]"), [source]);
  assert.equal(result.status, "answered");
  assert.deepEqual(result.citations.map(x => x.sourceId), ["S1"]);
});

test("compatibility: still accepts citation before final punctuation", () => {
  assert.equal(validateModelAnswer(raw("Qdrant stores document vectors [S1]."), [source]).status, "answered");
});

test("security: uncited sentence remains rejected even if another sentence has a trailing citation", () => {
  assert.throws(() => validateModelAnswer(raw("Unsupported claim. Qdrant stores vectors. [S1]"), [source]), HttpError);
});

test("security: fabricated and noncanonical citations after punctuation are rejected", () => {
  for (const answer of ["Qdrant stores vectors. [S2]", "Qdrant stores vectors. [S01]"]) {
    assert.throws(() => validateModelAnswer(raw(answer), [source]), HttpError);
  }
});

test("reliability: retries once on invalid model output and validates successful retry", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer("Where?", [source], async () => {
    calls++;
    return calls === 1 ? raw("Citation missing") : raw("Qdrant stores vectors. [S1]");
  });
  assert.equal(calls, 2);
  assert.equal(result.status, "answered");
});

test("security: persistent uncited model output fails closed after a single retry", async () => {
  let calls = 0;
  await assert.rejects(() => generateGroundedAnswer("Where?", [source], async () => {
    calls++;
    return raw("No citation");
  }), HttpError);
  assert.equal(calls, 2);
});

test("reliability: a valid first answer does not trigger a second generation", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer("Where?", [source], async () => {
    calls++;
    return raw("Qdrant stores vectors [S1].");
  });
  assert.equal(calls, 1);
  assert.equal(result.status, "answered");
});
