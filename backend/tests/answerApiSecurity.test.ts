/** Offline HTTP exercises the real /answer route with injected in-memory services. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import express from "express";
import { HttpError, errorHandler } from "../src/lib/http.js";
import { ANSWER_ENGINE_VERSION } from "../src/lib/answerRuntime.js";
import { currentUserId } from "../src/middleware/auth.js";
import { createRetrievalRouter, type RetrievalRouteDependencies, type RetrievalDocumentRecord } from "../src/routes/retrieval.js";
import type { RetrievedPoint } from "../src/lib/retrievalEligibility.js";

const KB = "cmvw9ro3w000548l1gkuh2gx8";
const ORG = "cmvw9ro3w000548l1gkuh2gx9";
const DOC = "cmvw9ro3w000548l1gkuh2gxa";
const VER = "cmvw9ro3w000548l1gkuh2gxb";
const point: RetrievedPoint = { id: "123e4567-e89b-42d3-a456-426614174000", score: 0.9, payload: {
  organization_id: ORG, knowledge_base_id: KB, document_id: DOC, document_version_id: VER,
  index_state: "ready", chunk_index: 0, page_number: null, text: "The vector database is Qdrant.",
}};
const record: RetrievalDocumentRecord = { id: DOC, title: "vector-qa.txt", knowledgeBaseId: KB, deletedAt: null,
  knowledgeBase: { organizationId: ORG }, versions: [{ id: VER, status: "READY" }] };

type State = { authCalls: number; searchCalls: number; modelCalls: number; revoked: boolean; hits: RetrievedPoint[];
  docs: RetrievalDocumentRecord[]; modelResult: string; deps: RetrievalRouteDependencies };
function fixture(): State {
  const s = {authCalls: 0, searchCalls: 0, modelCalls: 0, revoked: false,
    hits: [point], docs: [record], modelResult: JSON.stringify({abstain: false, answer: "Qdrant stores vectors [S1]."})} as State;
  s.deps = {
    authenticate(req, _res, next) {
      const user = req.get("x-test-user");
      if (user !== "alice" && user !== "bob") return next(new HttpError(401, "Authentication required"));
      req.auth = {userId: user, sessionId: "fixture", tokenHash: "fixture"}; next();
    },
    identifyUser: currentUserId,
    async authorizeKnowledgeBase(userId, kbId) {
      s.authCalls++;
      if (userId !== "alice" || kbId !== KB || s.revoked) throw new HttpError(404, "Resource not found");
      return { kb: {id: KB, organizationId: ORG} };
    },
    async searchPoints(scope) { s.searchCalls++; assert.deepEqual(scope, {organizationId: ORG, knowledgeBaseId: KB}); return s.hits; },
    async loadDocuments() { return s.docs; },
    async generateFromPrompt(prompt) { s.modelCalls++; assert.ok(prompt.includes("untrusted data")); return s.modelResult; },
  };
  return s;
}
async function withServer<T>(s: State, fn: (base: string) => Promise<T>): Promise<T> {
  const app = express(); app.use(express.json()); app.use("/api/v1/knowledge-bases", createRetrievalRouter(s.deps)); app.use(errorHandler);
  const server = app.listen(0, "127.0.0.1"); await once(server, "listening");
  try { return await fn(`http://127.0.0.1:${(server.address() as AddressInfo).port}`); }
  finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
}
async function post(base: string, user="alice", payload: object={question: "Which database stores vectors?",topK: 5},kb=KB) {
  const response = await fetch(`${base}/api/v1/knowledge-bases/${kb}/answer`, {
    method: "POST", headers: {"content-type":"application/json", ...(user ? {"x-test-user":user}: {})}, body: JSON.stringify(payload),
  });
  return {code:response.status,body:await response.json(),cache:response.headers.get("cache-control")};
}

test("HTTP answer: only verified source citations and no excerpt in response", async () => {
  const s=fixture(); await withServer(s, async base => {
    const r=await post(base); assert.equal(r.code,200); assert.equal(r.body.status,"answered");
    assert.equal(r.body.citations.length,1); assert.equal(r.body.citations[0].documentTitle,"vector-qa.txt");
    assert.equal(r.cache,"no-store"); assert.equal(s.authCalls,3);
    assert.equal(r.body.engineVersion, ANSWER_ENGINE_VERSION);
    assert.ok(!JSON.stringify(r.body).includes("The vector database is Qdrant."));
  });
});

test("HTTP answer: foreign or unauthenticated user rejected before search or model", async () => {
  for (const [user, code] of [["bob",404],["",401]] as const) {
    const s=fixture(); await withServer(s, async base => { const r=await post(base,user); assert.equal(r.code,code); });
    assert.equal(s.searchCalls,0); assert.equal(s.modelCalls,0);
  }
});

test("HTTP answer: forged organization and excessive topK rejected", async () => {
  for (const payload of [{question:"ok",organizationId:"other"},{question:"ok",topK:100}]) {
    const s=fixture(); await withServer(s, async base => {assert.equal((await post(base,"alice",payload)).code,400);});
    assert.equal(s.searchCalls,0); assert.equal(s.modelCalls,0);
  }
});

test("HTTP answer: archived/not-ready sources cause abstention without model", async () => {
  for(const docs of [[{...record,deletedAt:new Date()}],[{...record,versions:[{id:VER,status:"FAILED"}]}],[]]) {
    const s=fixture();s.docs=docs;await withServer(s, async base=>{
      const r=await post(base);assert.equal(r.code,200);assert.equal(r.body.status,"insufficient_evidence");
      assert.deepEqual(r.body.citations,[]);
    });assert.equal(s.modelCalls,0);
  }
});

test("HTTP answer: invented citation does not reach user", async () => {
  const s=fixture();s.modelResult=JSON.stringify({abstain:false,answer:"A secret claim [S77]."});
  await withServer(s,async base=>{const r=await post(base);assert.equal(r.code,503);assert.equal(r.body.citations,undefined);});
});

test("HTTP answer: malicious citationless model response fails closed", async () => {
  const s=fixture();s.modelResult=JSON.stringify({abstain:false,answer:"The model knows the password."});
  await withServer(s,async base=>{const r=await post(base);assert.equal(r.code,503);});
});

test("HTTP answer: membership revoked during generation returns 404", async () => {
  const s=fixture();s.deps.generateFromPrompt=async()=>{s.modelCalls++;s.revoked=true;return s.modelResult;};
  await withServer(s,async base=>{const r=await post(base);assert.equal(r.code,404);assert.equal(r.body.citations,undefined);});
  assert.equal(s.authCalls,3);
});

test("HTTP answer: model abstention returns no claimed citations", async () => {
  const s=fixture();s.modelResult=JSON.stringify({abstain:true,answer:""});
  await withServer(s,async base=>{const r=await post(base);assert.equal(r.code,200);assert.equal(r.body.status,"insufficient_evidence");assert.equal(r.body.citations.length,0);});
});


// End-to-end route contract: same ranked, authenticated READY sources as the
// real browser path; only the local retrieval/model dependencies are in-memory.
// No live KB, Redis, Ollama, MySQL or Qdrant is touched.
const VECTOR_DOC = "cmvw9ro3w000548l1gkuh2gx1";
const VECTOR_VER = "cmvw9ro3w000548l1gkuh2gx4";
const SECURITY_DOC = "cmvw9ro3w000548l1gkuh2gx2";
const SECURITY_VER = "cmvw9ro3w000548l1gkuh2gx5";
const STORAGE_DOC = "cmvw9ro3w000548l1gkuh2gx3";
const STORAGE_VER = "cmvw9ro3w000548l1gkuh2gx6";
function qaPoint(documentId: string, documentVersionId: string, index: number, excerpt: string, score: number): RetrievedPoint {
  return {
    id: `123e4567-e89b-42d3-a456-42661417400${index}`, score,
    payload: {
      organization_id: ORG, knowledge_base_id: KB, document_id: documentId,
      document_version_id: documentVersionId, index_state: "ready",
      chunk_index: 0, page_number: null, text: excerpt,
    },
  };
}
function qaRecord(documentId: string, versionId: string, title: string): RetrievalDocumentRecord {
  return {
    id: documentId, title, knowledgeBaseId: KB, deletedAt: null,
    knowledgeBase: { organizationId: ORG }, versions: [{ id: versionId, status: "READY" }],
  };
}
function multilingualFixture(): State {
  const s = fixture();
  s.hits = [
    qaPoint(VECTOR_DOC, VECTOR_VER, 1,
      "passage: DocMind-AI QA fixture: Vector search\\nDocMind-AI stores document embeddings in Qdrant, a vector database. The embedding model produces 384-dimensional vectors.", 0.94),
    qaPoint(SECURITY_DOC, SECURITY_VER, 2,
      "Cross-organization access to an inaccessible knowledge base must return a 404 error.", 0.89),
    qaPoint(STORAGE_DOC, STORAGE_VER, 3,
      "passage: DocMind-AI QA fixture: Infrastructure\\nRustFS is the S3-compatible object store that holds uploaded source documents. Qdrant stores document vectors, not original PDF files.", 0.85),
  ];
  s.docs = [
    qaRecord(VECTOR_DOC, VECTOR_VER, "phase3_3d_vectors.txt"),
    qaRecord(SECURITY_DOC, SECURITY_VER, "phase3_3d_security.txt"),
    qaRecord(STORAGE_DOC, STORAGE_VER, "phase3_3d_storage.txt"),
  ];
  s.deps.generateFromPrompt = async () => {
    s.modelCalls++;
    throw new Error("The model must not be used for explicit verified multilingual infrastructure facts");
  };
  return s;
}

for (const [question, expected, document] of [
  ["ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?", "Qdrant [S1].", "phase3_3d_vectors.txt"],
  ["আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?", "RustFS [S3].", "phase3_3d_storage.txt"],
  ["DocMind-AI source documents কোন object store-এ থাকে?", "RustFS [S3].", "phase3_3d_storage.txt"],
] as const) {
  test("HTTP answer: verified multilingual infrastructure fact " + document + " " + question, async () => {
    const s = multilingualFixture();
    await withServer(s, async base => {
      const r = await post(base, "alice", { question, topK: 3 });
      assert.equal(r.code, 200);
      assert.equal(r.body.status, "answered");
      assert.equal(r.body.answer, expected);
      assert.equal(r.body.citations.length, 1);
      assert.equal(r.body.citations[0].documentTitle, document);
      assert.equal(r.body.engineVersion, ANSWER_ENGINE_VERSION);
      assert.equal(r.cache, "no-store");
      assert.ok(!JSON.stringify(r.body).includes("QA fixture: Infrastructure"));
    });
    assert.equal(s.searchCalls, 1);
    assert.equal(s.modelCalls, 0);
    assert.equal(s.authCalls, 3);
  });
}

test("HTTP answer: developer resolution code is safe, opt-in and limited to an authenticated answer", async () => {
  const oldEnv = process.env.NODE_ENV;
  const oldDiag = process.env.DOCMIND_ANSWER_DIAGNOSTICS;
  try {
    process.env.NODE_ENV = "development";
    process.env.DOCMIND_ANSWER_DIAGNOSTICS = "1";
    const s = multilingualFixture();
    await withServer(s, async base => {
      const r = await post(base, "alice", {
        question: "ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?", topK: 3,
      });
      assert.equal(r.body.engineVersion, ANSWER_ENGINE_VERSION);
      assert.equal(r.body.diagnostic, "named_infrastructure");
      assert.equal((await post(base, "bob")).body.engineVersion, undefined);
    });
  } finally {
    if (oldEnv === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = oldEnv;
    if (oldDiag === undefined) delete process.env.DOCMIND_ANSWER_DIAGNOSTICS;
    else process.env.DOCMIND_ANSWER_DIAGNOSTICS = oldDiag;
  }
});
