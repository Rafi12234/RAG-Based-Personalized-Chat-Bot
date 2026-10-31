/** Phase 3.3 offline similarity ordering: NO database, vector store, network, or model access. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { candidateDocumentIds, selectAuthorizedSources } from "../src/lib/retrievalPostFilter.js";
import type { RelationalSource, RetrievedPoint } from "../src/lib/retrievalEligibility.js";

const org = "cmvw9ro3w000548l1gkuh2gx9";
const kb = "cmvw9ro3w000548l1gkuh2gx8";
const doc = "cmvw9ro3w000548l1gkuh2gxa";
const ver = "cmvw9ro3w000548l1gkuh2gxb";
const scope = { organizationId: org, knowledgeBaseId: kb };
const chunks = [
  "123e4567-e89b-42d3-a456-426614174001",
  "123e4567-e89b-42d3-a456-426614174002",
  "123e4567-e89b-42d3-a456-426614174003",
  "123e4567-e89b-42d3-a456-426614174004",
];
const db: RelationalSource = {
  documentId: doc, documentVersionId: ver, latestVersionId: ver,
  versionStatus: "READY", organizationId: org, knowledgeBaseId: kb,
  deletedAt: null, documentTitle: "Verified MySQL title",
};
const records = new Map([[doc, db]]);

function hit(score: number, chunkId: string, payload: Record<string, unknown> = {}): RetrievedPoint {
  return {
    id: chunkId,
    score,
    payload: {
      organization_id: org,
      knowledge_base_id: kb,
      document_id: doc,
      document_version_id: ver,
      index_state: "ready",
      chunk_index: 0,
      page_number: null,
      text: "Verified example text",
      ...payload,
    },
  };
}

test("quality: verified results sorted descending even if Qdrant results arrive out of order", () => {
  const results = selectAuthorizedSources(scope, [
    hit(0.12, chunks[0]), hit(0.91, chunks[1]), hit(0.52, chunks[2]),
  ], records, 3);
  assert.deepEqual(results.map(s => s.chunkId), [chunks[1], chunks[2], chunks[0]]);
  assert.deepEqual(results.map(s => s.score), [0.91, 0.52, 0.12]);
});

test("quality: topK selects highest VERIFIED result, not first-arriving match", () => {
  const results = selectAuthorizedSources(scope, [hit(0.10, chunks[0]), hit(0.95, chunks[1])], records, 1);
  assert.deepEqual(results.map(s => s.chunkId), [chunks[1]]);
});

test("quality: duplicate chunk retains highest-scoring verified version", () => {
  const results = selectAuthorizedSources(scope, [
    hit(0.20, chunks[0]), hit(0.92, chunks[1]), hit(0.88, chunks[0]),
  ], records, 5);
  assert.deepEqual(results.map(s => s.chunkId), [chunks[1], chunks[0]]);
  assert.equal(results[1].score, 0.88);
});

test("security: unauthorized high-score candidates are excluded before ranking", () => {
  const results = selectAuthorizedSources(scope, [
    hit(0.99, chunks[0], { organization_id: "foreign-tenant" }),
    hit(0.50, chunks[1]),
    hit(0.98, chunks[2], { index_state: "staging" }),
    hit(0.97, chunks[3], { document_version_id: "cmvw9ro3w000548l1gkuh2gxc" }),
  ], records, 5);
  assert.deepEqual(results.map(s => s.chunkId), [chunks[1]]);
});

test("security: missing MySQL truth, archived and processing records yield no sources", () => {
  const points = [hit(0.90, chunks[0])];
  assert.deepEqual(selectAuthorizedSources(scope, points, new Map(), 5), []);
  assert.deepEqual(selectAuthorizedSources(scope, points, new Map([[doc, { ...db, deletedAt: new Date() }]]), 5), []);
  assert.deepEqual(selectAuthorizedSources(scope, points, new Map([[doc, { ...db, versionStatus: "PROCESSING" }]]), 5), []);
});

test("quality: stable tie order and bounded multiple chunks per document", () => {
  const results = selectAuthorizedSources(scope, [
    hit(0.70, chunks[2], { chunk_index: 2 }),
    hit(0.70, chunks[0], { chunk_index: 0 }),
    hit(0.70, chunks[1], { chunk_index: 1 }),
  ], records, 2);
  assert.deepEqual(results.map(s => s.chunkId), [chunks[2], chunks[0]]);
});

test("security: malformed or out-of-range scores never enter ranking", () => {
  const invalid: RetrievedPoint[] = [
    { ...hit(0.12, chunks[0]), score: Number.NaN },
    hit(1.1, chunks[1]),
    hit(-1.1, chunks[2]),
    { ...hit(0.40, chunks[3]), score: "0.4" },
  ];
  assert.deepEqual(selectAuthorizedSources(scope, [...invalid, hit(0.25, chunks[1])], records, 5).map(s => s.chunkId), [chunks[1]]);
});

test("security: valid score does not override trusted document title or leak vector private metadata", () => {
  const results = selectAuthorizedSources(scope, [
    hit(0.70, chunks[0], { filename: "spoofed.pdf", processing_token: "private" }),
  ], records, 5);
  assert.equal(results[0].documentTitle, "Verified MySQL title");
  assert.equal("processing_token" in results[0], false);
  assert.equal("filename" in results[0], false);
});

test("security: invalid topK values fail closed even if this helper is called directly", () => {
  for (const count of [0, -1, 11, 0.5, Number.NaN, Infinity]) {
    assert.deepEqual(selectAuthorizedSources(scope, [hit(0.85, chunks[0])], records, count), []);
  }
});

test("security: candidate document ID collection remains tenant-neutral and deduplicated", () => {
  assert.deepEqual(candidateDocumentIds([
    hit(0.4, chunks[0]), hit(0.5, chunks[1]), hit(0.6, chunks[2], { document_id: "invalid-cuid!" }),
  ]), [doc]);
});
