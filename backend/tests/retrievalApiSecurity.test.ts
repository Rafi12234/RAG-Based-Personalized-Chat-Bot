/**
 * Offline HTTP-level tests for the REAL Phase 3.1 Express retrieval router.
 * In-memory dependencies mean no MySQL/Qdrant/Redis/RustFS writes or reads.
 * The x-fixture-user header exists ONLY in this test's local fixture, never in production.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import express from "express";
import { HttpError, errorHandler } from "../src/lib/http.js";
import { currentUserId } from "../src/middleware/auth.js";
import {
  createRetrievalRouter, type RetrievalRouteDependencies, type RetrievalDocumentRecord,
} from "../src/routes/retrieval.js";
import type { RetrievedPoint } from "../src/lib/retrievalEligibility.js";

const KB = "cmvw9ro3w000548l1gkuh2gx8";
const ORG = "cmvw9ro3w000548l1gkuh2gx9";
const DOC = "cmvw9ro3w000548l1gkuh2gxa";
const VERSION = "cmvw9ro3w000548l1gkuh2gxb";
const CHUNK = "123e4567-e89b-42d3-a456-426614174000";
const PATH = `/api/v1/knowledge-bases/${KB}/retrieve`;

const goodDocument: RetrievalDocumentRecord = {
  id: DOC, title: "Trusted DB title", knowledgeBaseId: KB, deletedAt: null,
  knowledgeBase: { organizationId: ORG },
  versions: [{ id: VERSION, status: "READY" }],
};
const goodHit: RetrievedPoint = {
  id: CHUNK, score: 0.81,
  payload: {
    organization_id: ORG, knowledge_base_id: KB, document_id: DOC,
    document_version_id: VERSION, index_state: "ready", chunk_index: 0,
    page_number: 1, text: "Permitted evidence", filename: "spoofed-title.pdf",
    processing_token: "never-return-this-token",
  },
};

type Fixture = {
  requests: { authorization: number; search: number; mysql: number };
  revoked: boolean;
  points: RetrievedPoint[];
  rows: RetrievalDocumentRecord[];
  deps: RetrievalRouteDependencies;
};
function fixture(): Fixture {
  const state = {
    requests: { authorization: 0, search: 0, mysql: 0 },
    revoked: false,
    points: [goodHit],
    rows: [goodDocument],
  } as Fixture;
  state.deps = {
    authenticate(req, _res, next) {
      const user = req.get("x-fixture-user");
      if (user !== "alice" && user !== "bob") return next(new HttpError(401, "Authentication required"));
      req.auth = { userId: user, sessionId: "fixture", tokenHash: "fixture" };
      next();
    },
    identifyUser: currentUserId,
    async authorizeKnowledgeBase(userId, kbId) {
      state.requests.authorization++;
      if (userId !== "alice" || kbId !== KB || state.revoked) {
        throw new HttpError(404, "Resource not found");
      }
      return { kb: { id: KB, organizationId: ORG } };
    },
    async searchPoints(scope) {
      state.requests.search++;
      assert.deepEqual(scope, { organizationId: ORG, knowledgeBaseId: KB });
      return state.points;
    },
    async loadDocuments(scope, ids) {
      state.requests.mysql++;
      assert.deepEqual(scope, { organizationId: ORG, knowledgeBaseId: KB });
      assert.ok(ids.every(id => id === DOC), "No untrusted document IDs in query");
      return state.rows;
    },
  };
  return state;
}

async function withHttp<T>(state: Fixture, callback: (base: string) => Promise<T>): Promise<T> {
  const app = express();
  app.use(express.json());
  app.use("/api/v1/knowledge-bases", createRetrievalRouter(state.deps));
  app.use(errorHandler);
  const server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = (server.address() as AddressInfo).port;
  try { return await callback(`http://127.0.0.1:${port}`); }
  finally { await new Promise<void>((resolve, reject) => server.close(err => err ? reject(err) : resolve())); }
}

async function post(base: string, user = "alice", body: unknown = { question: "Where are vectors?", topK: 5 }, path = PATH) {
  const response = await fetch(base + path, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(user ? { "x-fixture-user": user } : {}),
    },
    body: JSON.stringify(body),
  });
  return { code: response.status, cache: response.headers.get("cache-control"), data: await response.json() };
}

test("HTTP: valid READY source is returned with trusted MySQL title and no private metadata", async () => {
  const state = fixture();
  await withHttp(state, async base => {
    const result = await post(base);
    assert.equal(result.code, 200);
    assert.equal(result.cache, "no-store");
    assert.equal(result.data.status, "retrieved");
    assert.equal(result.data.sources.length, 1);
    assert.equal(result.data.sources[0].documentTitle, "Trusted DB title");
    assert.equal(result.data.sources[0].excerpt, "Permitted evidence");
    assert.ok(!JSON.stringify(result.data).includes("never-return-this-token"));
    assert.ok(!JSON.stringify(result.data).includes("spoofed-title.pdf"));
    assert.equal(state.requests.authorization, 2, "Must authorize before and after retrieval");
  });
});

test("HTTP: unauthenticated, foreign-tenant and invalid KB are rejected before any search", async () => {
  for (const scenario of [
    { user: "", path: PATH, expected: 401 },
    { user: "bob", path: PATH, expected: 404 },
    { user: "alice", path: "/api/v1/knowledge-bases/not-a-cuid/retrieve", expected: 404 },
  ]) {
    const state = fixture();
    await withHttp(state, async base => {
      const result = await post(base, scenario.user, undefined, scenario.path);
      assert.equal(result.code, scenario.expected);
      assert.equal(result.data.sources, undefined);
      assert.equal(state.requests.search, 0);
      assert.equal(state.requests.mysql, 0);
    });
  }
});

test("HTTP: forged organizationId and excessive topK are rejected before vector search", async () => {
  for (const body of [
    { question: "hello", organizationId: "a-forged-organization" },
    { question: "hello", topK: 100 },
  ]) {
    const state = fixture();
    await withHttp(state, async base => {
      const result = await post(base, "alice", body);
      assert.equal(result.code, 400);
      assert.equal(result.data.sources, undefined);
      assert.equal(state.requests.search, 0);
      assert.equal(state.requests.mysql, 0);
    });
  }
});

test("HTTP: archived, not-ready, stale-version and mismatched sources never leave the API", async () => {
  const cases: Array<{ name: string; points?: RetrievedPoint[]; rows?: RetrievalDocumentRecord[] }> = [
    { name: "archived", rows: [{ ...goodDocument, deletedAt: new Date() }] },
    { name: "processing", rows: [{ ...goodDocument, versions: [{ id: VERSION, status: "PROCESSING" }] }] },
    { name: "failed", rows: [{ ...goodDocument, versions: [{ id: VERSION, status: "FAILED" }] }] },
    { name: "superseded", rows: [{ ...goodDocument, versions: [{ id: "cmvw9ro3w000548l1gkuh2gxc", status: "READY" }] }] },
    { name: "no relational row", rows: [] },
    { name: "wrong tenant", points: [{ ...goodHit, payload: { ...(goodHit.payload as object), organization_id: "another-org" } }] },
    { name: "staging", points: [{ ...goodHit, payload: { ...(goodHit.payload as object), index_state: "staging" } }] },
  ];
  for (const scenario of cases) {
    const state = fixture();
    state.rows = scenario.rows ?? [goodDocument];
    state.points = scenario.points ?? [goodHit];
    await withHttp(state, async base => {
      const result = await post(base);
      assert.equal(result.code, 200, scenario.name);
      assert.equal(result.data.status, "no_relevant_sources", scenario.name);
      assert.deepEqual(result.data.sources, [], scenario.name);
      assert.equal(state.requests.authorization, 2);
    });
  }
});

test("HTTP: membership revoked during retrieval fails closed at final authorization", async () => {
  const state = fixture();
  const originalSearch = state.deps.searchPoints;
  state.deps.searchPoints = async (scope, question, topK) => {
    const points = await originalSearch(scope, question, topK);
    state.revoked = true;
    return points;
  };
  await withHttp(state, async base => {
    const result = await post(base);
    assert.equal(result.code, 404);
    assert.equal(result.data.sources, undefined);
    assert.equal(state.requests.authorization, 2);
  });
});

test("HTTP: unbounded internal point response fails closed", async () => {
  const state = fixture();
  state.points = Array.from({ length: 41 }, () => goodHit);
  await withHttp(state, async base => {
    const result = await post(base);
    assert.equal(result.code, 503);
    assert.equal(result.data.sources, undefined);
    assert.equal(state.requests.mysql, 0);
  });
});
