/** Phase 3.8 all ten QA cases: no DB, Qdrant, Ollama or private fixtures. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import { extractDirectPolicyFact } from "../src/lib/directPolicyFacts.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

function source(excerpt: string, id: number, title: string): VerifiedSource {
  return {
    chunkId: `123e4567-e89b-42d3-a456-42661417400${id}`,
    documentId: `synthetic-doc-${id}`, documentVersionId: `version-${id}`,
    documentTitle: title, chunkIndex: 0, pageNumber: null, score: 0.9, excerpt,
  };
}
const policy = source(
  "The Route North same-day dispatch cutoff is 16:00 Asia/Dhaka on business days. " +
  "The Route East same-day dispatch cutoff is 14:30 Asia/Dhaka on business days. " +
  "When a parcel is visibly damaged, staff must upload a photograph to the incident ticket within 72 hours of the damage being identified. " +
  "A refund is processed within five business days after the refund request is approved, not within five hours of the initial request.",
  1, "phase3_8_dispatch_policy.txt"
);
const conflict = source(
  "The Route North same-day dispatch cutoff is 15:00 Asia/Dhaka on business days. There is no disagreement about Route East.",
  2, "phase3_8_dispatch_counterstatement.txt"
);
const support = source(
  "The handbook does not publish a public support phone number or a direct support email address. " +
  "The support team aims to acknowledge a new ticket within one business day. " +
  "A customer may request an invoice correction within seven business days of receiving the invoice. " +
  "The policy specifies that resolved ticket attachments are removed after 30 days; metadata about the ticket is retained for 90 days. " +
  "The handbook does not specify encryption keys, database root credentials, or the phone number of the administrator.",
  3, "phase3_8_support_handbook.txt"
);
async function direct(question: string, evidence: VerifiedSource[]) {
  return generateGroundedAnswer(question, evidence, async () => {
    throw new Error("Ollama must not be called for a directly supported fact or explicit abstention");
  });
}

const positives: Array<[string, string, string]> = [
  ["What is the Route East same-day dispatch cutoff?", "14:30 Asia/Dhaka", policy.documentTitle],
  ["How many hours after damage is identified must staff upload a photograph?", "72 hours", policy.documentTitle],
  ["When does the five-business-day refund processing period start?", "After the refund request is approved", policy.documentTitle],
  ["How soon should Cedarstone acknowledge a new ticket?", "one business day", support.documentTitle],
  ["How long are resolved ticket attachments retained?", "30 days", support.documentTitle],
  ["How long is resolved ticket metadata retained?", "90 days", support.documentTitle],
  ["How many business days after receiving the invoice can a customer request a correction?", "seven business days", support.documentTitle],
];
for (const [question, expected, document] of positives) {
  test("phase3.8 positive evidence: " + question, async () => {
    const result = await direct(question, [conflict, support, policy]);
    assert.equal(result.status, "answered");
    assert.ok(result.answer.startsWith(expected + " [S"), result.answer);
    assert.equal(result.citations.length, 1);
    assert.equal(result.citations[0].documentTitle, document);
    assert.ok(result.answer.includes("[" + result.citations[0].sourceId + "]"));
  });
}

const negatives = [
  "What is the Route North same-day dispatch cutoff?",
  "What is Cedarstone's public support telephone number?",
  "What is the administrator password for Cedarstone Helpdesk?",
];
for (const question of negatives) {
  test("phase3.8 abstention: " + question, async () => {
    const result = await direct(question, [policy, conflict, support]);
    assert.equal(result.status, "insufficient_evidence");
    assert.deepEqual(result.citations, []);
  });
}

test("refund deadline must not answer the wrong number in a question", () => {
  const result = extractDirectPolicyFact(
    "When does the six-business-day refund processing period start?", [policy]
  );
  assert.equal(result, null);
});

test("a conflicting refund approval trigger must abstain", () => {
  const other = source(
    "A refund is processed within five business days after the refund request is submitted.", 4, "other.txt"
  );
  assert.equal(extractDirectPolicyFact(
    "When does the five-business-day refund processing period start?", [policy, other]
  ), null);
});

test("contradictory attachment retention periods must abstain", () => {
  const other = source("Resolved ticket attachments are removed after 45 days.", 5, "other.txt");
  assert.equal(extractDirectPolicyFact(
    "How long are resolved ticket attachments retained?", [support, other]
  ), null);
});

test("wrong object cannot borrow 90-day metadata retention for attachments", () => {
  const onlyMeta = source("Metadata about the ticket is retained for 90 days.", 6, "meta.txt");
  assert.equal(extractDirectPolicyFact(
    "How long are resolved ticket attachments retained?", [onlyMeta]
  ), null);
});

test("a policy question not in the recognized direct forms stays on model path", () => {
  assert.equal(extractDirectPolicyFact(
    "Why should the operations supervisor review an incident ticket?", [policy]
  ), undefined);
});

test("conflicting public telephone instructions do not yield a false absence claim", () => {
  const affirmative = source("The public support phone number is +1 555 0199.", 7, "contacts.txt");
  assert.equal(extractDirectPolicyFact(
    "What is Cedarstone's public support telephone number?", [support, affirmative]
  ), undefined);
});


test("Phase4.1: Aurora retention wording answers a mixed Bangla-English question without an LLM", async () => {
  const aurora = source(
    "Resolved ticket attachments are retained for 30 days after resolution; they are then removed. " +
    "Resolved ticket metadata is retained for 90 days after resolution.",
    8, "aurora_support_rules.txt",
  );
  const result = await generateGroundedAnswer(
    "Aurora resolved ticket attachments কত দিন retain করে?",
    [policy, aurora],
    async () => { throw new Error("Explicit verified retention must not invoke Ollama"); },
  );
  assert.equal(result.status, "answered");
  assert.equal(result.answer, "30 days [S2].");
  assert.equal(result.citations[0].documentTitle, "aurora_support_rules.txt");
});

test("Phase4.1: metadata retention cannot be confused with attachment retention", async () => {
  const aurora = source(
    "Resolved ticket attachments are retained for 30 days after resolution. " +
    "Resolved ticket metadata is retained for 90 days after resolution.",
    8, "aurora_support_rules.txt",
  );
  assert.equal(
    extractDirectPolicyFact("How long is resolved ticket metadata retained?", [aurora])?.text,
    "90 days",
  );
  assert.equal(
    extractDirectPolicyFact("How long are resolved ticket attachments retained?", [aurora])?.text,
    "30 days",
  );
});

test("Phase4.1: conflicting or negated retention facts fail closed", () => {
  const affirmative = source("Resolved ticket attachments are retained for 30 days after resolution.", 8, "aurora.txt");
  const conflicting = source("Resolved ticket attachments are retained for 45 days after resolution.", 9, "conflict.txt");
  const denied = source("Resolved ticket attachments are not retained for 30 days.", 6, "denied.txt");
  const query = "Aurora resolved ticket attachments কত দিন retain করে?";
  assert.equal(extractDirectPolicyFact(query, [affirmative, conflicting]), null);
  assert.equal(extractDirectPolicyFact(query, [denied]), null);
  assert.equal(extractDirectPolicyFact(query, [source("Resolved ticket metadata is retained for 90 days.", 6, "other.txt")]), null);
});
