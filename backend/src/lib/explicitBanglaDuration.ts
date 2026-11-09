/**
 * Conservative Bengali duration extraction from already verified RAG excerpts.
 * Requires a Bengali "how many days/hours" question, a same-sentence unit and
 * quantity, and strong overlapping Bengali subject terms. Does not assume
 * source titles, brands, known answers or document corpus.
 *
 * undefined = unsupported question form (use existing answer path)
 * null      = recognized request without unique supported duration (abstain)
 */
import type { VerifiedSource } from "./retrievalEligibility.js";

export type BanglaDurationFact = Readonly<{ sourceIndex: number; text: string }>;
type Unit = "days" | "hours" | "business_days";

const SPELLED: Readonly<Record<string, number>> = {
  এক: 1, দুই: 2, তিন: 3, চার: 4, পাঁচ: 5,
  ছয়: 6, ছয়: 6, সাত: 7, আট: 8, নয়: 9, নয়: 9, দশ: 10,
};
const IGNORE = new Set([
  "কত", "দিনের", "দিনে", "দিন", "ঘণ্টার", "ঘণ্টায়", "ঘণ্টায়",
  "কর্মদিবসের", "কর্মদিবসে", "মধ্যে", "করতে", "হবে", "দিতে",
  "পর", "থেকে", "কখন", "হলে", "যাবে", "জানাতে", "হয়", "হয়",
  "কোন", "কোনো",
]);
const TERMS = /[\u0980-\u09ff]{3,}/gu;
const DURATION = /([০-৯0-9]{1,4}|এক|দুই|তিন|চার|পাঁচ|ছয়|ছয়|সাত|আট|নয়|নয়|দশ)\s+(কর্মদিবস(?:ের)?|দিন(?:ের)?|ঘণ্টা(?:র)?)\s+মধ্যে/gu;

function kind(s: string): Unit | null {
  if (/^কর্মদিবস/u.test(s)) return "business_days";
  if (/^দিন/u.test(s)) return "days";
  if (/^ঘণ্টা/u.test(s)) return "hours";
  return null;
}
function tokens(s: string): string[] {
  return [...new Set((s.normalize("NFC").match(TERMS) ?? [])
    .filter(t => t.length >= 3 && !IGNORE.has(t) &&
      !/^[০-৯]+$/u.test(t)))];
}
function sameSubject(questionTerms: readonly string[], sentence: string): boolean {
  const evidence = tokens(sentence);
  const matches = questionTerms.filter(q => evidence.some(
    word => q === word || (q.length >= 3 && word.length >= 3 && q.slice(0, 3) === word.slice(0, 3)),
  )).length;
  return matches >= 3 && matches / questionTerms.length >= 0.6;
}
function numericValue(s: string): number | null {
  if (s in SPELLED) return SPELLED[s];
  const normalized = s.replace(/[০-৯]/gu, digit =>
    String(digit.codePointAt(0)! - "০".codePointAt(0)!));
  if (!/^\d{1,4}$/u.test(normalized)) return null;
  const n = Number(normalized);
  return Number.isInteger(n) && n >= 1 && n <= 999 ? n : null;
}

export function extractBanglaDuration(
  question: string, sources: readonly VerifiedSource[],
): BanglaDurationFact | null | undefined {
  const request = /কত\s+(কর্মদিবস(?:ের|ে)?|দিন(?:ের|ে)?|ঘণ্টা(?:র|য়|য়)?)/u.exec(question);
  if (!request) return undefined;
  const unit = kind(request[1]);
  if (!unit) return undefined;
  const subject = tokens(question);
  if (subject.length < 3) return undefined;

  const candidates: Array<{ sourceIndex: number; text: string; amount: number }> = [];
  for (const [sourceIndex, source] of sources.entries()) {
    const excerpt = source.excerpt.slice(0, 800).replace(/\\[rn]/gu, " ");
    for (const sentence of excerpt.split(/[.!?।]+|[\r\n]+/u)) {
      if (!sameSubject(subject, sentence)) continue;
      // A negated or forbidden duration is not affirmative supporting evidence.
      if (/(?:\s|^)(?:না|নয়|নয়|নেই|নিষিদ্ধ)(?:\s|$)/u.test(sentence)) continue;
      // Do not borrow another institution's policy merely because the product
      // and action words overlap with this question.
      if (/(?:অন্য|ভিন্ন|পৃথক)\s+(?:প্রতিষ্ঠান|দোকান|সংস্থা)(?:ের|ে|র)?/u.test(sentence)) continue;
      for (const match of sentence.matchAll(DURATION)) {
        if (kind(match[2]) !== unit) continue;
        const amount = numericValue(match[1]);
        if (amount === null) continue;
        const label = unit === "business_days" ? "কর্মদিবস"
          : unit === "days" ? "দিন" : "ঘণ্টা";
        candidates.push({ sourceIndex, amount, text: match[1] + " " + label });
      }
    }
  }
  if (!candidates.length || new Set(candidates.map(c => c.amount)).size !== 1) return null;
  return { sourceIndex: candidates[0].sourceIndex, text: candidates[0].text };
}
