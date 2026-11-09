import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveExplicitServiceActor } from "../src/lib/explicitServiceActor.js";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

function source(excerpt: string, documentTitle = "riverbend_platform.txt"): VerifiedSource {
  return {
    chunkId: "123e4567-e89b-42d3-a456-426614174000",
    documentId: "synthetic-doc",
    documentVersionId: "synthetic-version",
    documentTitle, chunkIndex: 0, pageNumber: null, score: 0.8, excerpt,
  };
}

const question = "Which service transports ingestion task messages between the API dispatcher and Celery workers?";
const evidence = "Redis transports ingestion task messages between the API dispatcher and Celery workers; Redis does not contain authoritative document versions.";
const abstain = JSON.stringify({ abstain: true, answer: "", source_id: "", evidence_quote: "" });

test("Phase4.1: service actor is identified by verified sentence, not memorized brand name", () => {
  assert.deepEqual(resolveExplicitServiceActor(question, [source(evidence)]),
    { state: "found", name: "Redis", sourceIndex: 0 });
  assert.deepEqual(resolveExplicitServiceActor(
    question, [source(evidence.replace("Redis", "MessageBus"))]),
    { state: "found", name: "MessageBus", sourceIndex: 0 });
});

test("Phase4.1: model abstention on real service relation recovers a cited answer", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer(question, [source(evidence)], async () => {
    calls++; return abstain;
  });
  assert.equal(calls, 2);
  assert.equal(result.status, "answered");
  assert.equal(result.answer, "Redis [S1].");
  assert.equal(result.citations[0].documentTitle, "riverbend_platform.txt");
});

test("Phase4.1: unrelated, negated, or absent actor relationships cannot be inferred", () => {
  for (const excerpt of [
    "Redis stores customer accounts and transaction metadata.",
    "Redis does not transport ingestion task messages between the API dispatcher and Celery workers.",
    "A fictional platform transports ingestion task messages between the API dispatcher and Celery workers.",
    "Redis transports customer invoices between accounting departments.",
  ]) {
    assert.notEqual(resolveExplicitServiceActor(question, [source(excerpt)]).state, "found");
  }
});

test("Phase4.1: conflicting named service actors fail closed", () => {
  const a = source("Redis transports ingestion task messages between the API dispatcher and Celery workers.");
  const b = source("StreamBus transports ingestion task messages between the API dispatcher and Celery workers.");
  assert.deepEqual(resolveExplicitServiceActor(question, [a, b]), { state: "conflict" });
});

test("Phase4.1: only very specific which-service relation questions are supported", () => {
  assert.deepEqual(resolveExplicitServiceActor("Which service sends data?", [source(evidence)]),
    { state: "not_applicable" });
  assert.deepEqual(resolveExplicitServiceActor("What is the CEO's private phone number?", [source(evidence)]),
    { state: "not_applicable" });
});
