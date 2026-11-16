/** Phase 3.7: a genuine quotation does not justify unrelated model claims. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { HttpError } from "../src/lib/http.js";
import { evidenceSupportsAnswerTerms } from "../src/lib/evidenceConsistency.js";
import { validateModelAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

const excerpt = "Qdrant stores document vectors, not the original PDF or DOCX binary files.";
const source: VerifiedSource = {
  chunkId:"123e4567-e89b-42d3-a456-426614174000",
  documentId:"doc-1",documentVersionId:"ver-1",documentTitle:"synthetic.txt",
  chunkIndex:0,pageNumber:null,excerpt,score:0.9,
};
const attributed = (answer:string) =>
  JSON.stringify({abstain:false,answer,source_id:"S1",evidence_quote:"E1_1"});

test("phase3.7: unsupported storage claim cannot borrow a true quote", () => {
  assert.throws(() => validateModelAnswer(attributed("Qdrant stores uploaded source documents"),[source]),HttpError);
});
test("phase3.7: negated object cannot be asserted as stored", () => {
  assert.throws(() => validateModelAnswer(attributed("Qdrant stores original PDF files"),[source]),HttpError);
});
test("phase3.7: true short entity answer remains valid", () => {
  assert.equal(validateModelAnswer(attributed("Qdrant"),[source]).answer,"Qdrant [S1].");
});
test("phase3.7: wrong number fails even with an authentic evidence ID", () => {
  assert.equal(evidenceSupportsAnswerTerms("512 dimensions","The model produces 384-dimensional vectors."),false);
});
test("phase3.7: Bangla-digit mismatch fails", () => {
  assert.equal(evidenceSupportsAnswerTerms("৪০৩","The server returns a 404 error."),false);
});
test("phase3.7: Bangla and mixed-language facts remain valid", () => {
  const q="RustFS stores original uploaded documents.";
  assert.equal(evidenceSupportsAnswerTerms("মূল ফাইল RustFS-এ থাকে",q),true);
  assert.equal(evidenceSupportsAnswerTerms("Source docs RustFS-এ থাকে",q),true);
});
