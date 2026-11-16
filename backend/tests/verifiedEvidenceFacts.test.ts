/**
 * Phase 4.1 end-to-end answer-engine regression from the repo's public
 * synthetic six-document fixture corpus. NO DB, network, Ollama or live KB.
 *
 * These tests exercise verified excerpts (not retrieval ranking quality).
 * Output assertions check factual values AND source document identity.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const root = new URL("../../quality-fixtures/phase4_1_pilot/", import.meta.url);
type ManifestCase = { case_id: string; question: string; expected_status: string;
  expected_document_filenames: string[]; reference_answer: string };
const manifest = JSON.parse(readFileSync(new URL("manifest.json", root), "utf8")) as
  { document_filenames: string[]; cases: ManifestCase[] };
const fixtures = new Map(manifest.document_filenames.map((filename, n) => {
  const data = readFileSync(new URL(filename, root), "utf8");
  const source: VerifiedSource = {
    chunkId: "123e4567-e89b-42d3-a456-42661417400" + n,
    documentId: "synthetic-fixture-" + n,
    documentVersionId: "synthetic-version-" + n,
    documentTitle: filename, chunkIndex: 0, pageNumber: null, score: 0.91 - n / 100,
    excerpt: data,
  };
  return [filename, source] as const;
}));
const docs = {
  riverbend: "riverbend_platform.txt",
  cedarstone: "cedarstone_dispatch_policy.txt",
  revision: "cedarstone_north_revision.txt",
  harbor: "harbor_finance_rules.txt",
  aurora: "aurora_support_rules.txt",
  shapla: "shapla_bangla_policy.txt",
};
const abstain = JSON.stringify({ abstain: true, answer: "", source_id: "", evidence_quote: "" });
const normalize = (s: string) => s.toLowerCase().replace(/[০-৯]/gu,
  c => String(c.charCodeAt(0) - "০".charCodeAt(0))).replace(/\s+/gu, " ");
async function run(question: string, filenames: string[]) {
  const inputs = filenames.map(filename => {
    const found = fixtures.get(filename);
    assert.ok(found, "Unexpected fixture " + filename);
    return found;
  });
  return generateGroundedAnswer(question, inputs, async () => abstain);
}
const checks: Record<string, RegExp[]> = {
  "en-object-store": [/rustfs/iu], "en-vector-store": [/qdrant/iu],
  "en-metadata": [/mysql/iu], "en-queue": [/redis/iu], "en-ready": [/\bno\b/iu],
  "en-east": [/14:30/u, /asia\/dhaka/iu], "en-south": [/11:45/u, /asia\/dhaka/iu],
  "en-damage": [/72\s+hours?/iu], "en-next-day": [/next working day/iu, /dispatch queue/iu],
  "en-bulletin": [/\bno\b/iu], "en-refund": [/manager approval|refund request is approved/iu],
  "en-invoice": [/seven business days|7 business days/iu],
  "en-surcharge": [/12 percent/iu, /pre.tax subtotal/iu],
  "en-attachments": [/30 days/iu], "en-metadata-retention": [/90 days/iu],
  "en-ticket-ack": [/one business day|1 business day/iu],
  "en-cross-doc": [/qdrant/iu, /30 days/iu],
  "bn-return": [/14\s+(?:দিন|days?)/iu], "bn-damage": [/48\s+(?:ঘণ্টা|hours?)/iu],
  "bn-approval": [/তিন কর্মদিবস|3 business days/iu], "bn-east": [/14:30/u, /asia\/dhaka/iu],
  "bn-storage": [/rustfs/iu], "mixed-support": [/30 days/iu],
  "mixed-vector": [/qdrant/iu], "mixed-refund": [/manager approval|refund request is approved/iu],
  "mixed-multi": [/rustfs/iu, /seven business days|7 business days/iu],
};
for (const c of manifest.cases) {
  test("phase4.1 official " + c.case_id, async () => {
    const input = c.expected_document_filenames.length ? c.expected_document_filenames
      : [docs.riverbend, docs.cedarstone, docs.revision, docs.harbor, docs.aurora];
    const result = await run(c.question, input);
    assert.equal(result.status, c.expected_status, c.case_id + ": " + result.answer);
    if (c.expected_status === "insufficient_evidence") {
      assert.deepEqual(result.citations, []);
    } else {
      assert.deepEqual(new Set(result.citations.map(x => x.documentTitle)),
        new Set(c.expected_document_filenames));
      const answer = normalize(result.answer);
      for (const re of checks[c.case_id] ?? []) {
        assert.match(answer, re, c.case_id + ": " + result.answer);
      }
    }
  });
}
type Edge = { id: string; question: string; files: string[]; cited: string[];
  patterns?: RegExp[] };
const E: Edge[] = [
  { id: "two-retentions", question: "How long are Aurora resolved ticket attachments and metadata retained, respectively?",
    files: [docs.aurora], cited: [docs.aurora], patterns: [/30 days/u, /90 days/u] },
  { id: "english-shapla-return", question: "How many days can customers request returns of regular products at Shapla?",
    files: [docs.shapla], cited: [docs.shapla], patterns: [/14 days/u] },
  { id: "english-shapla-damage", question: "For Shapla damaged goods, within how many hours must a photo be submitted?",
    files: [docs.shapla], cited: [docs.shapla], patterns: [/48 hours/u] },
  { id: "mixed-shapla-return", question: "Shapla return request deadline কত দিন?",
    files: [docs.shapla], cited: [docs.shapla], patterns: [/14 days/u] },
  { id: "bangla-shapla-support", question: "শাপলা গ্রাহক সহায়তার যোগাযোগ মাধ্যম কী?",
    files: [docs.shapla], cited: [docs.shapla], patterns: [/অনলাইন টিকিট/u] },
  { id: "dual-route", question: "What are the same-day dispatch cutoffs for Cedarstone Route East and Route South?",
    files: [docs.cedarstone], cited: [docs.cedarstone], patterns: [/14:30/u, /11:45/u] },
  { id: "damage-variant", question: "For Cedarstone, what is the deadline to upload a damage photograph after damage is identified?",
    files: [docs.cedarstone], cited: [docs.cedarstone], patterns: [/72 hours/u] },
  { id: "bulletin-variant", question: "Was the Route North 15:00 dispatch bulletin approved?",
    files: [docs.revision], cited: [docs.revision], patterns: [/\bno\b/u] },
  { id: "bangla-north-conflict", question: "রুট নর্থের চূড়ান্ত একই দিনে পাঠানোর শেষ সময় কখন?",
    files: [docs.cedarstone, docs.revision], cited: [] },
  { id: "invoice-direct", question: "How many business days after receiving an invoice can customers request corrections?",
    files: [docs.harbor], cited: [docs.harbor], patterns: [/seven business days|7 business days/iu] },
  { id: "refund-five", question: "How many business days is the Harbor refund processing period after manager approval?",
    files: [docs.harbor], cited: [docs.harbor], patterns: [/five business days|5 business days/iu] },
  { id: "return-label", question: "How many business days after a valid return is acknowledged is the Harbor return label issued?",
    files: [docs.harbor], cited: [docs.harbor], patterns: [/two business days|2 business days/iu] },
  { id: "route-and-invoice", question: "What is Route East's same-day cutoff and Harbor's invoice correction window?",
    files: [docs.harbor, docs.cedarstone], cited: [docs.harbor, docs.cedarstone],
    patterns: [/14:30/u, /seven business days|7 business days/iu] },
  { id: "mixed-vector-retention", question: "Riverbend vector database আর Aurora attachments retain কত দিন?",
    files: [docs.aurora, docs.riverbend], cited: [docs.aurora, docs.riverbend], patterns: [/qdrant/u, /30 days/u] },
  { id: "object-and-metadata", question: "What is Riverbend's object store and Aurora's ticket metadata retention?",
    files: [docs.riverbend, docs.aurora], cited: [docs.riverbend, docs.aurora], patterns: [/rustfs/u, /90 days/u] },
  { id: "service-and-database", question: "Which service transports Riverbend ingestion tasks and which relational database stores memberships?",
    files: [docs.riverbend], cited: [docs.riverbend], patterns: [/redis/u, /mysql/u] },
  { id: "aurora-phone-private", question: "What is Riverbend's CEO's private phone number?",
    files: [docs.riverbend], cited: [] },
  { id: "harbor-fx", question: "What is Harbor's current USD to BDT foreign exchange rate?",
    files: [docs.harbor], cited: [] },
  { id: "aurora-password", question: "What is Aurora's private administrative password?",
    files: [docs.aurora], cited: [] },
  { id: "harbor-exact-80", question: "What exact administration surcharge applies to a Harbor order of 80 items?",
    files: [docs.harbor], cited: [] },
  { id: "wrong-org-return", question: "Aurora general products return request deadline কত দিন?",
    files: [docs.aurora, docs.shapla], cited: [] },
  { id: "wrong-org-attachments", question: "How long does Riverbend retain resolved ticket attachments?",
    files: [docs.riverbend, docs.aurora], cited: [] },
  { id: "wrong-org-invoice", question: "How many business days does Aurora allow for invoice corrections?",
    files: [docs.aurora, docs.harbor], cited: [] },
  { id: "attachments-mixed", question: "Aurora resolved ticket metadata কত দিন retain করে?",
    files: [docs.aurora], cited: [docs.aurora], patterns: [/90 days/u] },
  { id: "attachment-form", question: "How long are resolved ticket attachments retained?",
    files: [docs.aurora], cited: [docs.aurora], patterns: [/30 days/u] },
];
assert.equal(manifest.cases.length, 32);
assert.equal(E.length, 25);
for (const edge of E) {
  test("phase4.1 edge " + edge.id, async () => {
    const result = await run(edge.question, edge.files);
    assert.equal(result.status, edge.cited.length ? "answered" : "insufficient_evidence", edge.id + ": " + result.answer);
    assert.deepEqual(new Set(result.citations.map(c => c.documentTitle)), new Set(edge.cited));
    const answer = normalize(result.answer);
    for (const pattern of edge.patterns ?? []) assert.match(answer, pattern);
  });
}
