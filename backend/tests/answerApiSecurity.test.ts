/** Offline HTTP exercises the real /answer route with injected in-memory services. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import express from "express";
import { HttpError, errorHandler } from "../src/lib/http.js";
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
