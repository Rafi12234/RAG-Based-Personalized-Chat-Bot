/** Direct evidence-fact resolver regressions. No Qdrant/DB/model dependencies. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolveNamedInfrastructure, infrastructureQuestionKind } from "../src/lib/namedInfrastructure.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const VQ = "ডকুমেন্ট এমবেডিং কোন ভেক্টর ডাটাবেসে রাখা হয়?";
const SQ = "আপলোড করা মূল ফাইলগুলো কোন স্টোরেজে রাখা হয়?";
const MQ = "DocMind-AI source documents কোন object store-এ থাকে?";
const V = "DocMind-AI stores document embeddings in Qdrant, a vector database.";
const S = "RustFS is the S3-compatible object store that holds uploaded source documents.";

function source(excerpt: string, documentTitle = "fixture.txt"): VerifiedSource {
  return {
    chunkId: "123e4567-e89b-42d3-a456-426614174000",
    documentId: "synthetic-doc", documentVersionId: "synthetic-version",
    documentTitle, chunkIndex: 0, pageNumber: null, score: 0.8, excerpt,
  };
}

test("named resolver recognizes Bangla and mixed named-infrastructure intents", () => {
  assert.equal(infrastructureQuestionKind(VQ), "vector_database");
  assert.equal(infrastructureQuestionKind(SQ), "object_store");
  assert.equal(infrastructureQuestionKind(MQ), "object_store");
  assert.equal(infrastructureQuestionKind("চাঁদের রাজপ্রাসাদের ফোন নম্বর কত?"), null);
});

for (const [question, statement, expected] of [
  [VQ, V, "Qdrant"],
  [SQ, S, "RustFS"],
  [MQ, S, "RustFS"],
] as const) {
  test("verified named fact survives real and literal escaped newlines: " + question, () => {
    for (const prelude of [
      "DocMind-AI QA fixture: Infrastructure\n\n",
      "passage: DocMind-AI QA fixture: Infrastructure\\n",
      "DocMind-AI QA fixture: Infrastructure\r\n",
      "DocMind-AI QA fixture: Infrastructure\\r\\n",
    ]) {
      const result = resolveNamedInfrastructure(question, [source(prelude + statement)]);
      assert.deepEqual(result, {state:"found",sourceIndex:0,name:expected});
    }
  });
}

test("vector relation can survive hard-wrapped source lines", () => {
  const text = "DocMind-AI stores document\nembeddings in Qdrant, a vector database.";
  assert.deepEqual(resolveNamedInfrastructure(VQ, [source(text)]),
    {state:"found",sourceIndex:0,name:"Qdrant"});
});

test("storage relation can survive hard-wrapped source lines", () => {
  const text = "RustFS is the S3-compatible object store that\nholds uploaded source documents.";
  assert.deepEqual(resolveNamedInfrastructure(MQ, [source(text)]),
    {state:"found",sourceIndex:0,name:"RustFS"});
});

test("ranking does not assign an answer to an unrelated first source", () => {
  const result = resolveNamedInfrastructure(SQ, [
    source(V, "vectors.txt"), source("No contact information here.", "security.txt"),
    source(S, "storage.txt"),
  ]);
  assert.deepEqual(result, {state:"found",sourceIndex:2,name:"RustFS"});
});

test("conflicting vector database assertions always fail closed", () => {
  const result = resolveNamedInfrastructure(VQ, [
    source(V), source("DocMind-AI stores document embeddings in AltDB, a vector database."),
  ]);
  assert.deepEqual(result, {state:"conflict"});
});

test("conflicting object store assertions always fail closed", () => {
  const result = resolveNamedInfrastructure(SQ, [
    source(S), source("AltStore is the S3-compatible object store that holds uploaded source documents."),
  ]);
  assert.deepEqual(result, {state:"conflict"});
});

test("missing evidence is distinct from conflicting evidence", () => {
  assert.deepEqual(resolveNamedInfrastructure(VQ, [source(S)]), {state:"missing"});
  assert.deepEqual(resolveNamedInfrastructure(SQ, []), {state:"missing"});
});

test("never infer infrastructure names from titles alone", () => {
  assert.deepEqual(resolveNamedInfrastructure(SQ,
    [source("No information about uploads is provided here.", "RustFS.txt")]), {state:"missing"});
});

test("wrong relationship is not enough to substantiate the answer", () => {
  const text = "Qdrant stores document vectors, not uploaded source documents. " +
    "RustFS holds some snapshots, but no object storage type is specified.";
  assert.deepEqual(resolveNamedInfrastructure(SQ, [source(text)]), {state:"missing"});
});

test("opposite sentence cannot supply the source-document relation", () => {
  const text = "RustFS is an S3-compatible object store. " +
    "Another service holds uploaded source documents.";
  assert.deepEqual(resolveNamedInfrastructure(SQ, [source(text)]), {state:"missing"});
});

test("negated statement does not count as affirmative evidence", () => {
  const text = "DocMind-AI does not store document embeddings in Qdrant, a vector database.";
  assert.deepEqual(resolveNamedInfrastructure(VQ, [source(text)]), {state:"missing"});
});

test("unknown question form is not automatically answered", () => {
  assert.deepEqual(resolveNamedInfrastructure("What was the invoice total?", [source(S)]),
    {state:"not_applicable"});
});

test("Phase 3 QA: actual repository fixture file bytes provide three supported facts", () => {
  const vectors = readFileSync(
    new URL("../../quality-fixtures/phase3_3d_vectors.txt", import.meta.url), "utf8",
  );
  const storage = readFileSync(
    new URL("../../quality-fixtures/phase3_3d_storage.txt", import.meta.url), "utf8",
  );
  for (const [q, expected, index] of [
    [VQ, "Qdrant", 0],
    [SQ, "RustFS", 1],
    [MQ, "RustFS", 1],
  ] as const) {
    const result = resolveNamedInfrastructure(q, [
      source("passage: " + vectors, "phase3_3d_vectors.txt"),
      source("passage: " + storage, "phase3_3d_storage.txt"),
    ]);
    assert.deepEqual(result, {state:"found",sourceIndex:index,name:expected});
  }
});
