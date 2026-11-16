/** Phase 3.6 verified numeric fact fallback: synthetic sources, no database access. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const source = (excerpt: string, suffix: number): VerifiedSource => ({
  chunkId: `123e4567-e89b-42d3-a456-42661417400${suffix}`,
  documentId: `synthetic-${suffix}`, documentVersionId: `synthetic-version-${suffix}`,
  documentTitle: `synthetic-${suffix}.txt`,
  chunkIndex: 0, pageNumber: null, excerpt, score: 0.8,
});
const factual = source("The embedding model is a local multilingual model, producing 384-dimensional vectors.", 1);
const irrelevant = source("The configured collection is named vectors_999_v1. Similarity uses cosine distance.", 2);
const abstain = JSON.stringify({abstain:true,answer:"",source_id:"",evidence_quote:""});

test("dimensions: source-derived 384 after two abstentions", async () => {
  let calls=0;
  const result=await generateGroundedAnswer("How many dimensions does the embedding model produce?", [irrelevant,factual], async()=>{calls++;return abstain;});
  assert.equal(result.answer,"384 dimensions [S2].");
  assert.equal(result.citations[0]?.documentTitle,"synthetic-1.txt");
  assert.equal(calls,2);
});
test("dimensions: conflicting supported dimensionalities cause abstention", async () => {
  const other=source("The embedding model produces embeddings with 512 dimensions.",3);
  const result=await generateGroundedAnswer("How many dimensions does the embedding model produce?", [factual,other], async()=>abstain);
  assert.equal(result.status,"insufficient_evidence");
  assert.deepEqual(result.citations,[]);
});
test("dimensions: unrelated collection version number cannot become an answer", async () => {
  const result=await generateGroundedAnswer("How many dimensions does the embedding model produce?", [irrelevant], async()=>abstain);
  assert.equal(result.status,"insufficient_evidence");
});
test("dimensions: unrelated numerical question cannot use embedding dimensions", async () => {
  const result=await generateGroundedAnswer("What is the IP address of the embedding model with 384 dimensions?", [factual], async()=>abstain);
  assert.equal(result.status,"insufficient_evidence");
});
test("dimensions: alternate wording and Bangla dimension question", async () => {
  for(const question of ["What is the embedding model dimensionality?","এমবেডিং মডেলের ডাইমেনশন কয়টি?"]){
    const result=await generateGroundedAnswer(question,[factual],async()=>abstain);
    assert.equal(result.answer,"384 dimensions [S1].");
  }
});
