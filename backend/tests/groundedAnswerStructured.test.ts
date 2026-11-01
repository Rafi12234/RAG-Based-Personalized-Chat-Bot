/** Phase 3.4: structured source + exact quote contract (offline only). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { HttpError } from "../src/lib/http.js";
import { buildEvidencePrompt, generateGroundedAnswer, validateModelAnswer, callLocalOllama } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const first: VerifiedSource = {
  chunkId: "123e4567-e89b-42d3-a456-426614174000", documentId: "doc-A",
  documentVersionId: "ver-A", documentTitle: "Vectors.txt", chunkIndex: 0,
  pageNumber: null, excerpt: "Qdrant stores document vectors. MySQL stores account records.", score: 0.8,
};
const second: VerifiedSource = {
  ...first, chunkId: "123e4567-e89b-42d3-a456-426614174001", documentId: "doc-B",
  documentTitle: "Storage.txt", excerpt: "RustFS stores original uploaded documents.", score: 0.7,
};
const structured = (answer: string, source_id = "S1", evidence_quote = "Qdrant stores document vectors.") =>
  JSON.stringify({ abstain: false, answer, source_id, evidence_quote });

test("structured: server appends citation only with an explicitly selected verified source and exact quote", () => {
  const result = validateModelAnswer(structured("Qdrant"), [first]);
  assert.equal(result.status, "answered");
  assert.equal(result.answer, "Qdrant [S1].");
  assert.equal(result.citations.length, 1);
  assert.equal(result.citations[0].documentId, first.documentId);
  assert.equal(result.citations[0].sourceId, "S1");
});

test("structured: second verified source produces S2 and database-derived citation", () => {
  const result = validateModelAnswer(structured("RustFS", "S2", "RustFS stores original uploaded documents."), [first, second]);
  assert.equal(result.answer, "RustFS [S2].");
  assert.equal(result.citations[0].documentId, "doc-B");
});

test("structured: invented source id is rejected", () => {
  for (const id of ["S0", "S2", "S01", "S77", "other", ""]) {
    assert.throws(() => validateModelAnswer(structured("Qdrant", id), [first]), HttpError);
  }
});

test("structured: exact quote is mandatory and must appear in the CHOSEN excerpt", () => {
  for (const quote of ["", "Qdrant", "Qdrant stores something else.", "RustFS stores original uploaded documents."]) {
    assert.throws(() => validateModelAnswer(structured("Qdrant", "S1", quote), [first, second]), HttpError);
  }
});

test("structured: unsupported quote cannot be used to borrow another source's label", () => {
  assert.throws(() => validateModelAnswer(structured("RustFS", "S2", "Qdrant stores document vectors."), [first, second]), HttpError);
});

test("structured: refuses multiple factual sentences backed by one anchor", () => {
  assert.throws(() => validateModelAnswer(structured("First claim. Second claim.", "S1"), [first]), HttpError);
});

test("structured: rejects marker injection instead of reinterpreting model text", () => {
  for (const ans of ["Qdrant [S9]", "Qdrant [S1].", "Qdrant\nMore text"]) {
    assert.throws(() => validateModelAnswer(structured(ans), [first]), HttpError);
  }
});

test("structured: missing source fields do not silently add citations", () => {
  assert.throws(() => validateModelAnswer(JSON.stringify({abstain:false,answer:"Qdrant"}), [first]), HttpError);
});

test("structured: valid abstention has no answer/citations", () => {
  const result = validateModelAnswer(JSON.stringify({abstain:true,answer:"",source_id:"",evidence_quote:""}), [first]);
  assert.equal(result.status, "insufficient_evidence");
  assert.deepEqual(result.citations, []);
});

test("structured: new instructions mention untrusted data, exact source, quote and no inline marker", () => {
  const prompt = buildEvidencePrompt("Which database?", [first]);
  for (const phrase of ["untrusted data", "source_id", "evidence_quote", "CONTIGUOUS, EXACT", "server adds citations"]) {
    assert.ok(prompt.includes(phrase));
  }
  assert.ok(!prompt.includes(first.documentId));
});

test("structured: model-attributed answer works without requiring model to print [S1]", async () => {
  let calls=0;
  const result = await generateGroundedAnswer("Which database?", [first], async () => {calls++;return structured("Qdrant");});
  assert.equal(calls, 1);
  assert.equal(result.status, "answered");
  assert.equal(result.answer, "Qdrant [S1].");
});

test("structured: retry remains bounded and rejects unverifiable evidence", async () => {
  let calls=0;
  await assert.rejects(generateGroundedAnswer("Which database?", [first], async () => {
    calls++;return structured("Qdrant", "S1", "Invented unsupported quotation.");
  }), HttpError);
  assert.equal(calls, 2);
});

test("structured: old inline-citation JSON remains accepted by existing tests", () => {
  const result = validateModelAnswer(JSON.stringify({abstain:false,answer:"Qdrant stores vectors [S1]."}), [first]);
  assert.equal(result.status, "answered");
});

test("structured: configured Ollama request sends a constrained JSON schema", async () => {
  const originalFetch = globalThis.fetch;
  const previousModel = process.env.DOCMIND_OLLAMA_MODEL;
  const previousUrl = process.env.DOCMIND_OLLAMA_URL;
  let requestCount=0;
  try {
    process.env.DOCMIND_OLLAMA_MODEL="qwen2.5:1.5b";
    process.env.DOCMIND_OLLAMA_URL="http://127.0.0.1:11434/api/generate";
    globalThis.fetch = async (url, init) => {
      requestCount++;
      assert.equal(String(url), "http://127.0.0.1:11434/api/generate");
      const payload = JSON.parse(String(init?.body));
      assert.equal(payload.stream, false);
      assert.equal(payload.format.type, "object");
      assert.equal(payload.format.additionalProperties, false);
      assert.deepEqual(payload.format.required, ["abstain", "answer", "source_id", "evidence_quote"]);
      return new Response(JSON.stringify({response:structured("Qdrant"),done:true}), {status:200});
    };
    assert.equal(await callLocalOllama("safe synthetic test"), structured("Qdrant"));
    assert.equal(requestCount, 1);
  } finally {
    globalThis.fetch=originalFetch;
    if(previousModel===undefined)delete process.env.DOCMIND_OLLAMA_MODEL;
    else process.env.DOCMIND_OLLAMA_MODEL=previousModel;
    if(previousUrl===undefined)delete process.env.DOCMIND_OLLAMA_URL;
    else process.env.DOCMIND_OLLAMA_URL=previousUrl;
  }
});

test("structured: prompt offers server-selected exact evidence IDs for each source", () => {
  const prompt = buildEvidencePrompt("আপলোড করা মূল ফাইল কোথায় থাকে?", [first, second]);
  const parsed = JSON.parse(prompt.split("EVIDENCE JSON:")[1].trim());
  assert.equal(parsed[0].source, "S1");
  assert.equal(parsed[0].evidence_choices[0].id, "E1_1");
  assert.equal(parsed[0].evidence_choices[0].quote, "Qdrant stores document vectors.");
  assert.equal(parsed[1].evidence_choices[0].id, "E2_1");
  assert.equal(parsed[1].evidence_choices[0].quote, "RustFS stores original uploaded documents.");
  assert.ok(prompt.includes("Copy ONLY the ID"));
  assert.ok(!prompt.includes(first.documentId));
});

test("structured: Bangla answer can use verified server-selected evidence ID", async () => {
  const answer = JSON.stringify({ abstain: false, answer: "মূল ফাইল RustFS-এ থাকে", source_id: "S2", evidence_quote: "E2_1" });
  const result = await generateGroundedAnswer("আপলোড করা মূল ফাইল কোথায় থাকে?", [first, second], async () => answer);
  assert.equal(result.status, "answered");
  assert.equal(result.answer, "মূল ফাইল RustFS-এ থাকে [S2].");
  assert.equal(result.citations[0].documentId, second.documentId);
});

test("structured: mixed-language answer can select an evidence ID without translating quote", () => {
  const result = validateModelAnswer(
    JSON.stringify({ abstain: false, answer: "Source docs RustFS-এ থাকে", source_id: "S2", evidence_quote: "E2_1" }),
    [first, second],
  );
  assert.equal(result.answer, "Source docs RustFS-এ থাকে [S2].");
});

test("structured: invented or cross-source evidence IDs fail closed", () => {
  for (const [source_id, evidence_quote] of [["S1", "E1_9"], ["S1", "E2_1"], ["S2", "E1_1"], ["S2", "E2_0"]]) {
    const raw = JSON.stringify({ abstain: false, answer: "RustFS", source_id, evidence_quote });
    assert.throws(() => validateModelAnswer(raw, [first, second]), HttpError);
  }
});

test("structured: translated or paraphrased evidence is still rejected", () => {
  const raw = JSON.stringify({ abstain: false, answer: "মূল ফাইল RustFS-এ থাকে", source_id: "S2", evidence_quote: "মূল ফাইল RustFS-এ থাকে।" });
  assert.throws(() => validateModelAnswer(raw, [first, second]), HttpError);
});
