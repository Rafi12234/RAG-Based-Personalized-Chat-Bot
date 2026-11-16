/** Phase 3.8 synthetic multi-document regression: no model/network/database calls. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const source = (excerpt: string, index: number): VerifiedSource => ({
  chunkId: `123e4567-e89b-42d3-a456-42661417400${index}`,
  documentId: `synthetic-doc-${index}`,
  documentVersionId: `synthetic-version-${index}`,
  documentTitle: index === 1 ? "phase3_8_dispatch_policy.txt" : "phase3_8_dispatch_counterstatement.txt",
  chunkIndex: 0, pageNumber: null, score: 0.85, excerpt,
});
const policy = source(
  "The Route North same-day dispatch cutoff is 16:00 Asia/Dhaka on business days. A Route North parcel scanned after 16:00 is queued for the next business day. The Route East same-day dispatch cutoff is 14:30 Asia/Dhaka on business days. A Route East parcel scanned at 14:45 is queued for the next business day.",
  1,
);
const counterstatement = source(
  "This independent fictional operations note asserts: the Route North same-day dispatch cutoff is 15:00 Asia/Dhaka on business days. This statement deliberately conflicts with the separate policy, which says Route North uses 16:00 Asia/Dhaka. There is no disagreement about Route East in this counterstatement because this note does not set an East cutoff.",
  2,
);
const abstain = JSON.stringify({ abstain: true, answer: "", source_id: "", evidence_quote: "" });
async function run(question: string, sources: VerifiedSource[]) {
  let modelCalls = 0;
  const answer = await generateGroundedAnswer(question, sources, async () => {
    modelCalls += 1;
    return abstain;
  });
  return { answer, modelCalls };
}

test("phase3.8: East cutoff comes from verified policy, never another route", async () => {
  const { answer, modelCalls } = await run("What is the Route East same-day dispatch cutoff?", [policy, counterstatement]);
  assert.equal(answer.status, "answered");
  assert.equal(answer.answer, "14:30 Asia/Dhaka [S1].");
  assert.equal(answer.citations.length, 1);
  assert.equal(answer.citations[0].documentTitle, "phase3_8_dispatch_policy.txt");
  assert.equal(modelCalls, 0); // exact evidence resolves without Ollama
});

test("phase3.8: East cutoff can cite second source in a ranked response", async () => {
  const { answer } = await run("What is the Route East same-day dispatch cutoff?", [counterstatement, policy]);
  assert.equal(answer.answer, "14:30 Asia/Dhaka [S2].");
  assert.equal(answer.citations[0].sourceId, "S2");
});

test("phase3.8: conflicting North cutoff times cannot be auto-answered", async () => {
  const { answer } = await run("What is the Route North same-day dispatch cutoff?", [policy, counterstatement]);
  assert.equal(answer.status, "insufficient_evidence");
  assert.deepEqual(answer.citations, []);
});

test("phase3.8: matching route without cutoff value must abstain", async () => {
  const { answer } = await run("What is the Route East same-day dispatch cutoff?", [counterstatement]);
  assert.equal(answer.status, "insufficient_evidence");
});

test("phase3.8: unrelated route, missing route and unrelated question abstain", async () => {
  for (const question of [
    "What is the Route South same-day dispatch cutoff?",
    "What is the Route East support phone number?",
    "What is the customer refund deadline?",
  ]) {
    const { answer } = await run(question, [policy, counterstatement]);
    assert.equal(answer.status, "insufficient_evidence");
    assert.equal(answer.citations.length, 0);
  }
});

test("phase3.8: scanned-at time cannot override the cutoff", async () => {
  const { answer } = await run("What is the Route East same-day dispatch cutoff?", [policy]);
  assert.equal(answer.answer, "14:30 Asia/Dhaka [S1].");
  assert.ok(!answer.answer.includes("14:45"));
});

test("phase3.8: contradictory East cutoff causes abstention", async () => {
  const contradictory = source("The Route East same-day dispatch cutoff is 13:15 Asia/Dhaka on business days.", 3);
  const { answer } = await run("What is the Route East same-day dispatch cutoff?", [policy, contradictory]);
  assert.equal(answer.status, "insufficient_evidence");
  assert.deepEqual(answer.citations, []);
});

test("phase3.8: model transport failure cannot prevent exact cutoff extraction", async () => {
  const result = await generateGroundedAnswer(
    "What is the Route East same-day dispatch cutoff?",
    [policy, counterstatement],
    async () => { throw new Error("Simulated Ollama outage"); },
  );
  assert.equal(result.answer, "14:30 Asia/Dhaka [S1].");
});

test("phase3.8: conflicting or missing cutoff never invokes model", async () => {
  for (const sources of [[policy, counterstatement], [counterstatement]]) {
    const question = sources.length === 2
      ? "What is the Route North same-day dispatch cutoff?"
      : "What is the Route East same-day dispatch cutoff?";
    const result = await generateGroundedAnswer(question, sources, async () => {
      throw new Error("Model should not be called");
    });
    assert.equal(result.status, "insufficient_evidence");
    assert.equal(result.citations.length, 0);
  }
});
