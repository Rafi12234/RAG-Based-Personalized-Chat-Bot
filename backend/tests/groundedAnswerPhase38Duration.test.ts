/** Phase 3.8: read-only, offline tests of source-grounded duration extraction. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import { extractExplicitDuration } from "../src/lib/explicitDuration.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const source = (excerpt: string, index: number): VerifiedSource => ({
  chunkId: `123e4567-e89b-42d3-a456-42661417400${index}`,
  documentId: `synthetic-doc-${index}`, documentVersionId: `synthetic-ver-${index}`,
  documentTitle: `synthetic-${index}.txt`, score: 0.8, chunkIndex: 0,
  pageNumber: null, excerpt,
});
const question = "How many hours after damage is identified must staff upload a photograph?";
const correct = source(
  "When a parcel is visibly damaged, staff must upload a photograph to the incident ticket within 72 hours of the damage being identified. The incident ticket must reference the parcel scan ID.",
  1,
);
const unrelated = source(
  "The support team must acknowledge the ticket within 24 hours after staff upload a receipt photograph.",
  2,
);

test("phase3.8: explicit 72-hour deadline is answered and cited even if Ollama unavailable", async () => {
  const result = await generateGroundedAnswer(question, [unrelated, correct], async () => {
    throw new Error("Ollama must not be called for explicit duration fact");
  });
  assert.equal(result.status, "answered");
  assert.equal(result.answer, "72 hours [S2].");
  assert.deepEqual(result.citations.map(c => c.documentTitle), ["synthetic-1.txt"]);
});

test("phase3.8: wrong event/action cannot borrow an unrelated 24-hour deadline", () => {
  assert.equal(extractExplicitDuration(question, [unrelated]), null);
});

test("phase3.8: contradictory 48-hour deadline abstains without model generation", async () => {
  const conflict = source("Staff must upload a photograph within 48 hours after the damage is identified.", 3);
  const result = await generateGroundedAnswer(question, [correct, conflict], async () => {
    throw new Error("No model call allowed for ambiguous explicit-duration question");
  });
  assert.equal(result.status, "insufficient_evidence");
  assert.deepEqual(result.citations, []);
});

test("phase3.8: missing matching duration abstains without fabricated citation", async () => {
  const result = await generateGroundedAnswer(question, [unrelated], async () => {
    throw new Error("No model call expected");
  });
  assert.equal(result.status, "insufficient_evidence");
  assert.equal(result.citations.length, 0);
});

test("phase3.8: bare numbers and a different duration unit cannot be borrowed", async () => {
  const different = source("Staff upload a photograph 72 days after the damage is identified.", 3);
  assert.equal(extractExplicitDuration(question, [different]), null);
});

test("phase3.8: negated 72-hour duration is not affirmative evidence", () => {
  const negated = source("Staff must not upload a photograph within 72 hours after damage is identified.", 4);
  assert.equal(extractExplicitDuration(question, [negated]), null);
});

test("phase3.8: non-duration question remains delegated to normal model path", () => {
  assert.equal(extractExplicitDuration("Which object store holds uploaded files?", [correct]), undefined);
});

test("phase3.8: no hardcoded 72, other explicitly sourced durations can answer", async () => {
  const other = source("When a parcel is visibly damaged, staff must upload a photograph within 36 hours of the damage being identified.", 5);
  const result = await generateGroundedAnswer(question, [other], async () => {
    throw new Error("No model invocation expected");
  });
  assert.equal(result.answer, "36 hours [S1].");
});
