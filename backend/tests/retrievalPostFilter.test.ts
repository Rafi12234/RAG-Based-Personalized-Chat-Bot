import { test } from "node:test";
import assert from "node:assert/strict";
import { candidateDocumentIds, selectAuthorizedSources } from "../src/lib/retrievalPostFilter.js";
import type { RelationalSource, RetrievedPoint } from "../src/lib/retrievalEligibility.js";

const org = "cmvw9ro3w000548l1gkuh2gx8";
const kb = "cmvwaibvc000848l1r4r3vgjv";
const doc = "cmvw9ro3w000548l1gkuh2gx8";
const version = "cmvwaibvc000848l1r4r3vgjv";
const scope = { organizationId: org, knowledgeBaseId: kb };
const id = "123e4567-e89b-42d3-a456-426614174000";
const record: RelationalSource = {
  documentId: doc, documentVersionId: version, latestVersionId: version,
  versionStatus: "READY", organizationId: org, knowledgeBaseId: kb,
  deletedAt: null, documentTitle: "Trustworthy document title",
};
const hit = (overrides: Record<string, unknown> = {}, pointId = id): RetrievedPoint => ({
  id: pointId, score: 0.81,
  payload: { organization_id: org, knowledge_base_id: kb, document_id: doc,
    document_version_id: version, index_state: "ready", chunk_index: 0,
    page_number: null, text: "Trusted excerpt", ...overrides },
});

test("collects only valid document IDs for bounded batch lookup", () => {
  assert.deepEqual(candidateDocumentIds([hit(), hit(), hit({document_id: "not an id"})]), [doc]);
});

test("filters sources without relational authority or from archived versions", () => {
  assert.deepEqual(selectAuthorizedSources(scope, [hit()], new Map(), 5), []);
  assert.deepEqual(selectAuthorizedSources(scope, [hit()], new Map([[doc, {...record, deletedAt: new Date()}]]), 5), []);
  assert.deepEqual(selectAuthorizedSources(scope, [hit({organization_id: "other"})], new Map([[doc, record]]), 5), []);
  assert.deepEqual(selectAuthorizedSources(scope, [hit({index_state: "staging"})], new Map([[doc, record]]), 5), []);
});

test("deduplicates chunks, enforces topK and uses MySQL title", () => {
  const result = selectAuthorizedSources(scope, [
    hit({filename: "untrusted label"}),
    hit(),
    hit({chunk_index: 1}, "123e4567-e89b-42d3-a456-426614174001"),
  ], new Map([[doc, record]]), 1);
  assert.equal(result.length, 1);
  assert.equal(result[0].documentTitle, record.documentTitle);
  assert.equal(result[0].chunkId, id);
  assert.ok(!("filename" in result[0]));
});
