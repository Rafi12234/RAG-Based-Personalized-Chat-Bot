import { test } from "node:test";
import assert from "node:assert/strict";
import { HttpError } from "../src/lib/http.js";
import { buildEvidencePrompt, validateModelAnswer, generateGroundedAnswer, callLocalOllama } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const source: VerifiedSource = {
  chunkId: "123e4567-e89b-42d3-a456-426614174000", documentId: "doc-123",
  documentVersionId: "ver-123", documentTitle: "Qdrant.txt", chunkIndex: 0,
  pageNumber: null, excerpt: "Qdrant stores the document vectors.", score: 0.83,
};
const second = { ...source, chunkId: "123e4567-e89b-42d3-a456-426614174001", documentTitle: "Security.txt" };

test("prompt includes only verified evidence labels and explicitly distrusts excerpts", () => {
  const text = buildEvidencePrompt("Where are vectors?", [source, second]);
  assert.match(text, /untrusted data/);
  assert.match(text, /S1/);
  assert.match(text, /S2/);
  assert.ok(!text.includes(source.documentId));
});

test("supported answer returns citation metadata only from verified source", () => {
  const result = validateModelAnswer(JSON.stringify({ abstain: false, answer: "Qdrant stores vectors [S1]." }), [source]);
  assert.equal(result.status, "answered");
  assert.equal(result.citations[0].sourceId, "S1");
  assert.equal(result.citations[0].documentId, source.documentId);
  assert.equal(result.citations[0].documentTitle, source.documentTitle);
  assert.equal(result.citations[0].chunkId, source.chunkId);
});

test("citation order follows source numbers, not model ordering", () => {
  const answer = "The first fact [S2]. The second fact [S1].";
  const result = validateModelAnswer(JSON.stringify({ abstain: false, answer }), [source, second]);
  assert.deepEqual(result.citations.map(c => c.sourceId), ["S1", "S2"]);
});

test("model can explicitly abstain with no citations", () => {
  const result = validateModelAnswer(JSON.stringify({ abstain: true, answer: "" }), [source]);
  assert.equal(result.status, "insufficient_evidence");
  assert.equal(result.citations.length, 0);
});

test("empty retrieval returns abstention without invoking model", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer("Where?", [], async () => { calls++; return ""; });
  assert.equal(calls, 0);
  assert.equal(result.status, "insufficient_evidence");
});

test("rejects fabricated source numbers", () => {
  for (const answer of ["Qdrant [S2].", "Qdrant [S0].", "Qdrant [S99].", "Qdrant [S01]."]) {
    assert.throws(() => validateModelAnswer(JSON.stringify({ abstain: false, answer }), [source]), HttpError);
  }
});

test("rejects responses with uncited sentences", () => {
  assert.throws(() => validateModelAnswer(JSON.stringify({ abstain: false, answer: "Unsupported fact. Qdrant [S1]." }), [source]), HttpError);
});

test("rejects invalid JSON, extra fields and ungrounded citationless answers", () => {
  for (const raw of ["garbage", "{}", JSON.stringify({ abstain: false, answer: "Qdrant stores vectors." }),
    JSON.stringify({ abstain: false, answer: "Qdrant [S1].", private: "secret" })]) {
    assert.throws(() => validateModelAnswer(raw, [source]), HttpError);
  }
});

test("caps model prompt evidence sources", () => {
  assert.throws(() => buildEvidencePrompt("Question", Array.from({length: 6}, () => source)), HttpError);
  assert.throws(() => buildEvidencePrompt(" ", [source]), HttpError);
});

test("configured local generator refuses absent model before any network request", async () => {
  const prior = process.env.DOCMIND_OLLAMA_MODEL;
  delete process.env.DOCMIND_OLLAMA_MODEL;
  try { await assert.rejects(() => callLocalOllama("probe"), (error: unknown) => error instanceof HttpError && error.status === 503); }
  finally { if (prior === undefined) delete process.env.DOCMIND_OLLAMA_MODEL; else process.env.DOCMIND_OLLAMA_MODEL = prior; }
});

test("local generator rejects a remote URL before connecting", async () => {
  const priorModel = process.env.DOCMIND_OLLAMA_MODEL;
  const priorUrl = process.env.DOCMIND_OLLAMA_URL;
  process.env.DOCMIND_OLLAMA_MODEL = "test:model";
  process.env.DOCMIND_OLLAMA_URL = "https://example.invalid/api/generate";
  try { await assert.rejects(() => callLocalOllama("probe"), (error: unknown) => error instanceof HttpError && error.status === 503); }
  finally {
    if (priorModel === undefined) delete process.env.DOCMIND_OLLAMA_MODEL; else process.env.DOCMIND_OLLAMA_MODEL = priorModel;
    if (priorUrl === undefined) delete process.env.DOCMIND_OLLAMA_URL; else process.env.DOCMIND_OLLAMA_URL = priorUrl;
  }
});
