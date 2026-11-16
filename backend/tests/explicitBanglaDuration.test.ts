import { test } from "node:test";
import assert from "node:assert/strict";
import { extractBanglaDuration } from "../src/lib/explicitBanglaDuration.js";
import { generateGroundedAnswer } from "../src/services/groundedAnswer.js";
import type { VerifiedSource } from "../src/lib/retrievalEligibility.js";

function source(excerpt: string, documentTitle = "shapla_bangla_policy.txt"): VerifiedSource {
  return {
    chunkId: "123e4567-e89b-42d3-a456-426614174000",
    documentId: documentTitle, documentVersionId: "synthetic-version",
    documentTitle, chunkIndex: 0, pageNumber: null, score: 0.8, excerpt,
  };
}
const returnQuestion = "শাপলা দোকানে কত দিনের মধ্যে সাধারণ পণ্যের ফেরতের আবেদন করতে হবে?";
const returnFact = "শাপলা দোকানের সাধারণ পণ্য গ্রহণের পর ১৪ দিনের মধ্যে ফেরতের আবেদন করতে হবে।";
const damageQuestion = "শাপলা দোকানে ক্ষতিগ্রস্ত পণ্যের ছবি কত ঘণ্টার মধ্যে জমা দিতে হবে?";
const damageFact = "ক্ষতিগ্রস্ত পণ্যের ক্ষেত্রে ক্ষতি শনাক্তের পর ৪৮ ঘণ্টার মধ্যে ছবি জমা দিতে হবে।";
const approvalQuestion = "শাপলা দোকানে ফেরত অনুমোদনের পর কত কর্মদিবসে সিদ্ধান্ত জানাতে হবে?";
const approvalFact = "ফেরতের আবেদন অনুমোদিত হলে তিন কর্মদিবসের মধ্যে গ্রাহককে সিদ্ধান্ত জানানো হবে।";

test("Phase4.1: return days directly from verified Bangla evidence, no model", async () => {
  let calls = 0;
  const result = await generateGroundedAnswer(returnQuestion, [source(returnFact)], async () => {
    calls++; throw new Error("No model call expected");
  });
  assert.equal(calls, 0);
  assert.equal(result.answer, "১৪ দিন [S1].");
  assert.equal(result.citations[0].documentTitle, "shapla_bangla_policy.txt");
});

test("Phase4.1: extract hours and spelled business days from relevant Bangla sentences", async () => {
  for (const [question, evidence, expected] of [
    [damageQuestion, damageFact, "৪৮ ঘণ্টা [S1]."],
    [approvalQuestion, approvalFact, "তিন কর্মদিবস [S1]."],
  ] as const) {
    const result = await generateGroundedAnswer(question, [source(evidence)], async () => {
      throw new Error("No model call expected");
    });
    assert.equal(result.answer, expected);
  }
});

test("Phase4.1: multilingual source at lower ranking still uses its own citation", async () => {
  const result = await generateGroundedAnswer(returnQuestion, [
    source("The cutoff time for the dispatch route is 14:30 Asia/Dhaka.", "other.txt"),
    source(returnFact),
  ], async () => { throw new Error("No model call expected"); });
  assert.equal(result.answer, "১৪ দিন [S2].");
  assert.equal(result.citations[0].documentTitle, "shapla_bangla_policy.txt");
});

test("Phase4.1: unrelated amounts, missing answers and conflicting amounts abstain", () => {
  for (const excerpt of [
    "শাপলা দোকানের সাধারণ পণ্য গ্রহণের পর ২১ ঘণ্টার মধ্যে ফেরতের আবেদন করতে হবে।",
    "শাপলা দোকানে কত দিনের মধ্যে ফেরতের আবেদন করা যায় তা লেখা নেই।",
    "ভিন্ন প্রতিষ্ঠানের সাধারণ পণ্যের ফেরতের আবেদন করতে ১৪ দিনের মধ্যে জমা দিতে হবে।",
  ]) {
    assert.notEqual(extractBanglaDuration(returnQuestion, [source(excerpt)])?.text, "১৪ দিন");
  }
  assert.equal(extractBanglaDuration(returnQuestion, [
    source(returnFact), source(returnFact.replace("১৪", "২১"), "other.txt"),
  ]), null);
});

test("Phase4.1: non-duration questions do not enter Bangla extractor", () => {
  assert.equal(extractBanglaDuration("শাপলা দোকানের প্রকাশিত ফোন নম্বর কী?", [source(returnFact)]), undefined);
});
