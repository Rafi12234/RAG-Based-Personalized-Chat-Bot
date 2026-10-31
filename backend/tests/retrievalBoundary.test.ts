import { test } from "node:test";
import assert from "node:assert/strict";
import { HttpError } from "../src/lib/http.js";
import { parseRetrievalRequest } from "../src/lib/retrievalValidation.js";
import { verifiedSourceFromHit, type RelationalSource, type RetrievedPoint } from "../src/lib/retrievalEligibility.js";

const scope = { organizationId: "org-a", knowledgeBaseId: "kb-a" };
const relational: RelationalSource = {
  documentId: "doc-a",
  documentVersionId: "ver-a",
  latestVersionId: "ver-a",
  versionStatus: "READY",
  organizationId: "org-a",
  knowledgeBaseId: "kb-a",
  deletedAt: null,
  documentTitle: "policy.pdf",
};
const hit: RetrievedPoint = {
  id: "c8cf1906-83f5-5caf-bcec-7fb8a56e9c8c",
  score: 0.82,
  payload: {
    organization_id: "org-a",
    knowledge_base_id: "kb-a",
    document_id: "doc-a",
    document_version_id: "ver-a",
    index_state: "ready",
    chunk_index: 0,
    page_number: 2,
    text: "Verified policy statement",
    processing_token: "private-do-not-return",
    filename: "untrusted-filename.pdf",
  },
};
function withPayload(changes: Record<string, unknown>): RetrievedPoint {
  return { ...hit, payload: { ...(hit.payload as object), ...changes } };
}
function withRelational(changes: Partial<RelationalSource>): RelationalSource {
  return { ...relational, ...changes };
}

test("retrieval request validates and applies bounded default topK", () => {
  assert.deepEqual(parseRetrievalRequest({ question: "  Hello বাংলা  " }), { question: "Hello বাংলা", topK: 5 });
  assert.equal(parseRetrievalRequest({ question: "Question", topK: 10 }).topK, 10);
});

test("retrieval request rejects invalid, oversize and forged tenant fields", () => {
  const invalid = [
    {}, { question: "   " }, { question: "x".repeat(1001) },
    { question: "a\u0000b" }, { question: "valid", topK: 0 },
    { question: "valid", topK: 11 }, { question: "valid", topK: "5" },
    { question: "valid", organizationId: "org-b" },
    { question: "valid", documentId: "doc-b" },
  ];
  for (const req of invalid) {
    assert.throws(() => parseRetrievalRequest(req), HttpError, JSON.stringify(req).slice(0, 80));
  }
});

test("authorized ready source keeps trusted DB title, bounded evidence and no token", () => {
  const result = verifiedSourceFromHit(scope, hit, relational);
  assert.ok(result);
  assert.equal(result.documentTitle, "policy.pdf");
  assert.equal(result.pageNumber, 2);
  assert.equal(result.excerpt, "Verified policy statement");
  assert.equal("processing_token" in result, false);
  assert.equal("filename" in result, false);
});

test("does not accept missing relational truth or foreign tenant, KB, document, version", () => {
  assert.equal(verifiedSourceFromHit(scope, hit, null), null);
  for (const payload of [
    withPayload({ organization_id: "org-b" }),
    withPayload({ knowledge_base_id: "kb-b" }),
    withPayload({ document_id: "doc-b" }),
    withPayload({ document_version_id: "ver-b" }),
  ]) assert.equal(verifiedSourceFromHit(scope, payload, relational), null);
  assert.equal(verifiedSourceFromHit(scope, hit, withRelational({ organizationId: "org-b" })), null);
  assert.equal(verifiedSourceFromHit(scope, hit, withRelational({ knowledgeBaseId: "kb-b" })), null);
});

test("staging, archived, failed, processing, or nonlatest version is never visible", () => {
  assert.equal(verifiedSourceFromHit(scope, withPayload({ index_state: "staging" }), relational), null);
  assert.equal(verifiedSourceFromHit(scope, hit, withRelational({ deletedAt: new Date() })), null);
  assert.equal(verifiedSourceFromHit(scope, hit, withRelational({ versionStatus: "PROCESSING" })), null);
  assert.equal(verifiedSourceFromHit(scope, hit, withRelational({ versionStatus: "FAILED" })), null);
  assert.equal(verifiedSourceFromHit(scope, hit, withRelational({ latestVersionId: "ver-new" })), null);
});

test("malformed point IDs, scores and payload fields fail closed", () => {
  assert.equal(verifiedSourceFromHit(scope, { ...hit, id: "unknown" }, relational), null);
  assert.equal(verifiedSourceFromHit(scope, { ...hit, score: Number.NaN }, relational), null);
  assert.equal(verifiedSourceFromHit(scope, withPayload({ chunk_index: -1 }), relational), null);
  assert.equal(verifiedSourceFromHit(scope, withPayload({ page_number: 0 }), relational), null);
  assert.equal(verifiedSourceFromHit(scope, withPayload({ text: " " }), relational), null);
});

test("page absent stays null, excerpts remain bounded", () => {
  const result = verifiedSourceFromHit(scope, withPayload({ page_number: null, text: "😀".repeat(900) }), relational);
  assert.ok(result);
  assert.equal(result.pageNumber, null);
  assert.equal(Array.from(result.excerpt).length, 800);
});
