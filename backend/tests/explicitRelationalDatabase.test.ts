import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveExplicitRelationalDatabase } from "../src/lib/explicitRelationalDatabase.js";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const question = "Which relational database stores memberships and ingestion job state?";
const fact = "MySQL stores account memberships, knowledge bases, document-version status, and the database record of ingestion jobs.";
const abstain = JSON.stringify({ abstain: true, answer: "", source_id: "", evidence_quote: "" });
function source(excerpt: string, title = "riverbend_platform.txt"): VerifiedSource {
  return {
    chunkId: "123e4567-e89b-42d3-a456-426614174000",
    documentId: title, documentVersionId: "v1", documentTitle: title,
    chunkIndex: 0, pageNumber: null, score: 0.8, excerpt,
  };
}

test("Phase4.1: relational database is read from exact verified sentence", () => {
  assert.deepEqual(resolveExplicitRelationalDatabase(question, [source(fact)]),
    { state: "found", name: "MySQL", sourceIndex: 0 });
  assert.deepEqual(resolveExplicitRelationalDatabase(question, [source(fact.replace("MySQL", "LedgerDB"))]),
    { state: "found", name: "LedgerDB", sourceIndex: 0 });
});

test("Phase4.1: live missed MySQL fact recovers after model abstentions", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer(question, [source(fact)], async () => {
    calls++; return abstain;
  });
  assert.equal(calls, 2);
  assert.equal(result.status, "answered");
  assert.equal(result.answer, "MySQL [S1].");
  assert.equal(result.citations[0].documentTitle, "riverbend_platform.txt");
});

test("Phase4.1: no unsupported or split source claims", () => {
  for (const factText of [
    "MySQL stores account memberships.",
    "MySQL stores account memberships. Another service stores the database record of ingestion jobs.",
    "MySQL does not store account memberships or ingestion job records.",
    "Redis stores the database record of ingestion jobs. MySQL stores account memberships.",
    "The MySQL database is mentioned without an explicit stores relationship.",
  ]) {
    assert.notEqual(resolveExplicitRelationalDatabase(question, [source(factText)]).state, "found");
  }
});

test("Phase4.1: conflicting database names do not get arbitrarily ranked", () => {
  assert.deepEqual(resolveExplicitRelationalDatabase(question, [
    source(fact),
    source(fact.replace("MySQL", "LedgerDB"), "other.txt"),
  ]), { state: "conflict" });
});

test("Phase4.1: unrelated questions stay on ordinary grounded-answer path", () => {
  assert.equal(resolveExplicitRelationalDatabase(
    "What is the CEO's phone number?", [source(fact)]).state, "not_applicable");
  assert.equal(resolveExplicitRelationalDatabase(
    "Which database stores membership records?", [source(fact)]).state, "not_applicable");
});
