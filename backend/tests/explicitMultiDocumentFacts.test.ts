/** Offline multi-document QA; no DB, vector service, or LLM. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import { extractMultiDocumentFact } from "../src/lib/explicitMultiDocumentFacts.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";
function src(n: number, documentTitle: string, excerpt: string): VerifiedSource {
  return { chunkId: "123e4567-e89b-42d3-a456-42661417400" + n,
    documentId: "synthetic-" + n, documentVersionId: "version-" + n,
    documentTitle, excerpt, chunkIndex: 0, pageNumber: null, score: 0.91 };
}
const riverbend = src(1, "riverbend_platform.txt",
  "RIVERBEND PLATFORM. Qdrant stores the platform's 384-dimensional document embedding vectors and their chunk metadata.");
const aurora = src(2, "aurora_support_rules.txt",
  "AURORA SUPPORT. Resolved ticket attachments are retained for 30 days after resolution; they are then removed. " +
  "Resolved ticket metadata is retained for 90 days.");
const q = "What is Riverbend's vector database and Aurora's resolved attachment retention period?";
async function answer(sources: VerifiedSource[]) {
  return generateGroundedAnswer(q, sources, async () => {
    throw new Error("LLM must not run for this verified two-fact question");
  });
}
test("two explicit facts require two original citations", async () => {
  let reason = "";
  const a = await generateGroundedAnswer(q, [riverbend, aurora], async () => {
    throw new Error("LLM called");
  }, value => { reason = value; });
  assert.equal(reason, "multi_document_fact");
  assert.equal(a.status, "answered");
  assert.equal(a.answer, "Qdrant [S1] and 30 days [S2].");
  assert.deepEqual(a.citations.map(c => c.documentTitle), ["riverbend_platform.txt", "aurora_support_rules.txt"]);
});
test("rank order inversion keeps each claim tied to its source", async () => {
  const a = await answer([aurora, riverbend]);
  assert.equal(a.answer, "Qdrant [S2] and 30 days [S1].");
  assert.deepEqual(a.citations.map(c => c.sourceId), ["S1", "S2"]);
});
test("either missing source returns abstention without partial answer", async () => {
  for (const s of [[riverbend], [aurora]]) {
    const a = await answer(s);
    assert.equal(a.status, "insufficient_evidence");
    assert.deepEqual(a.citations, []);
  }
});
test("conflicts in either organization's source return abstention", async () => {
  const otherDb = src(3, "riverbend_revision.txt",
    "RIVERBEND REVISION. AlphaDB stores the platform's 384-dimensional document embedding vectors.");
  const otherRetention = src(4, "aurora_revision.txt",
    "AURORA REVISION. Resolved ticket attachments are retained for 45 days after resolution.");
  for (const s of [[riverbend, otherDb, aurora], [riverbend, aurora, otherRetention]]) {
    const a = await answer(s);
    assert.equal(a.status, "insufficient_evidence");
    assert.deepEqual(a.citations, []);
  }
});
test("unrelated organization and metadata cannot answer attachment retention", async () => {
  const unrelated = src(5, "elsewhere_support.txt",
    "ELSEWHERE SUPPORT. Resolved ticket attachments are retained for 60 days.");
  const onlyMeta = src(6, "aurora_metadata.txt",
    "AURORA SUPPORT. Resolved ticket metadata is retained for 90 days.");
  for (const s of [[riverbend, unrelated], [riverbend, onlyMeta]]) {
    const a = await answer(s);
    assert.equal(a.status, "insufficient_evidence");
    assert.deepEqual(a.citations, []);
  }
});
test("titles alone do not provide either requested fact", async () => {
  const emptyDb = src(7, "riverbend_platform.txt", "RIVERBEND. No vector database is specified.");
  const emptyRetention = src(8, "aurora_support_rules.txt", "AURORA. No attachment retention period specified.");
  for (const s of [[emptyDb, aurora], [riverbend, emptyRetention]]) {
    const a = await answer(s);
    assert.equal(a.status, "insufficient_evidence");
    assert.deepEqual(a.citations, []);
  }
});
test("unrelated questions do not trigger multi-document extraction", () => {
  assert.deepEqual(extractMultiDocumentFact("What is the document database?", [riverbend, aurora]),
    { state: "not_applicable" });
});

const harbor = src(9, "harbor_finance_rules.txt",
  "HARBOR FINANCE. Customers may request invoice corrections within seven business days of receiving the invoice. " +
  "A return label is issued within two business days after the refund manager acknowledges a valid return.");
const storeRiverbend = src(1, "riverbend_platform.txt",
  "RIVERBEND PLATFORM. The Riverbend document platform keeps original uploaded PDF, DOCX and TXT files in RustFS, an S3-compatible object store. " +
  "Qdrant stores the platform's 384-dimensional document embedding vectors.");
const mixed = "Riverbend object store আর Harbor invoice correction time কত?";
async function answerMixed(sources: VerifiedSource[]) {
  return generateGroundedAnswer(mixed, sources, async () => {
    throw new Error("Mixed multi-document fact must be extracted from verified sources");
  });
}
test("Phase4.1 mixed two-document QA returns source-backed store and invoice deadline", async () => {
  let resolution = "";
  const result = await generateGroundedAnswer(mixed, [storeRiverbend, harbor],
    async () => { throw new Error("No LLM should be needed"); },
    value => { resolution = value; });
  assert.equal(resolution, "multi_document_fact");
  assert.equal(result.status, "answered");
  assert.equal(result.answer, "RustFS [S1] and seven business days [S2].");
  assert.deepEqual(result.citations.map(c => c.documentTitle),
    ["riverbend_platform.txt", "harbor_finance_rules.txt"]);
});
test("Phase4.1 mixed answer preserves claims when retrieval ranking changes", async () => {
  const result = await answerMixed([harbor, storeRiverbend]);
  assert.equal(result.answer, "RustFS [S2] and seven business days [S1].");
  assert.deepEqual(result.citations.map(c => c.sourceId), ["S1", "S2"]);
});
test("Phase4.1 mixed answer abstains when either fact is unavailable", async () => {
  for(const s of [[storeRiverbend], [harbor],
    [storeRiverbend, src(7, "harbor_finance_rules.txt", "HARBOR FINANCE. No invoice deadline is specified.")],
    [harbor, src(8, "riverbend_platform.txt", "RIVERBEND PLATFORM. No object store is specified.")]]) {
    const result = await answerMixed(s);
    assert.equal(result.status, "insufficient_evidence");
    assert.deepEqual(result.citations, []);
  }
});
test("Phase4.1 mixed answer abstains when store or correction deadline conflicts", async () => {
  const otherStore = src(3, "riverbend_revision.txt",
    "RIVERBEND REVISION. Riverbend keeps original uploaded PDF files in AlphaStore, an S3-compatible object store.");
  const otherInvoice = src(4, "harbor_update.txt",
    "HARBOR FINANCE UPDATE. Customers may request invoice corrections within nine business days of receiving the invoice.");
  for (const s of [[storeRiverbend, otherStore, harbor], [storeRiverbend, harbor, otherInvoice]]) {
    const result = await answerMixed(s);
    assert.equal(result.status, "insufficient_evidence");
    assert.deepEqual(result.citations, []);
  }
});
test("Phase4.1 mixed answer cannot borrow unrelated deadlines or negated policy", async () => {
  const unrelated = src(5, "elsewhere_finance.txt",
    "ELSEWHERE FINANCE. Customers may request invoice corrections within nine business days of receiving the invoice.");
  const wrongDeadline = src(6, "harbor_other.txt",
    "HARBOR FINANCE. Refund processing takes seven business days after approval.");
  const denied = src(7, "harbor_denied.txt",
    "HARBOR FINANCE. Customers may not request invoice corrections within seven business days of receiving the invoice.");
  for (const s of [[storeRiverbend, unrelated], [storeRiverbend, wrongDeadline], [storeRiverbend, denied]]) {
    const result = await answerMixed(s);
    assert.equal(result.status, "insufficient_evidence");
    assert.deepEqual(result.citations, []);
  }
});
