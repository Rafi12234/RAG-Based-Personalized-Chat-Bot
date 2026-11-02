/** Conservative check for model-authored answers paired with verified quotations.
 * Rejects unsupported literal terms, incorrect numbers and simple reversals of
 * explicit negations. NOT a semantic-entailment model or an authorization check.
 * This deliberately favors abstention over unsupported English paraphrases.
 */
const FILLER = new Set([
  "a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "for", "with", "by",
  "as", "is", "are", "was", "were", "be", "been", "it", "this", "that", "they",
  "from", "which", "what", "who", "where", "when", "how", "can", "does", "do",
  "noted", "answer", "according", "fact", "facts", "about",
]);

const normalizeDigits = (input: string): string => input.replace(/[০-৯]/gu, digit =>
  String(digit.codePointAt(0)! - "০".codePointAt(0)!));

const latinTokens = (input: string): string[] =>
  input.toLowerCase().match(/[a-z][a-z0-9_]*(?:-[a-z0-9_]+)*/gu) ?? [];

/** Words are limited to safe literal alphanumeric/hyphen tokens; no regex injection. */
const isWord = (word: string, text: string): boolean =>
  new RegExp("(^|[^a-z0-9_])" + word + "($|[^a-z0-9_])", "u").test(text);

export function evidenceSupportsAnswerTerms(answer: string, quote: string): boolean {
  const a = normalizeDigits(answer).toLowerCase();
  const q = normalizeDigits(quote).toLowerCase();

  // Do not accept a guessed HTTP code, count or other number because a
  // different number happens to occur in the source or collection identifier.
  for (const number of a.match(/\d+/gu) ?? []) {
    if (!isWord(number, q)) return false;
  }

  const multilingual = /[\u0980-\u09ff]/u.test(a);
  const terms = latinTokens(a).filter(word => !FILLER.has(word));
  for (const term of terms) {
    // Descriptive English in a Bengali-English response may be implicit in
    // an English source. Entity names and numerical values still must match.
    if (multilingual && ["source", "docs"].includes(term)) continue;
    if (term === "dimensions" && q.includes("dimension")) continue;
    if (term === "docs" && q.includes("document")) continue;
    if (isWord(term, q)) continue;
    // Limited singular/plural normalization; never use it for numeric tokens.
    if (term.length >= 5 && (isWord(term + "s", q) ||
        (term.endsWith("s") && isWord(term.slice(0, -1), q)))) continue;
    return false;
  }

  // Reject common assertion inversions: a positive storage claim cannot use
  // an object that the quotation explicitly says is NOT stored or held.
  if (/\b(?:stores?|holds?|contains?)\b/u.test(a) && !/\b(?:not|never)\b/u.test(a)) {
    for (const match of q.matchAll(/\b(?:not|never)\s+([^.!?;]+)/gu)) {
      const negated = new Set(latinTokens(match[1]).filter(word => !FILLER.has(word)));
      const objects = terms.filter(word =>
        !["stores", "store", "holds", "hold", "contains", "contain"].includes(word));
      if (objects.filter(word => negated.has(word)).length >= 2) return false;
    }
  }
  return true;
}
