/** Strict, source-derived extraction for simple numeric duration questions.
 * This is not open-ended semantic reasoning. It only applies when a question
 * asks "how many hours/days" and its subject/action clearly matches the SAME
 * verified evidence sentence. Unsupported or conflicting values abstain.
 * Values are never hardcoded, and authorization happens before this function.
 */
import type { VerifiedSource } from "./retrievalEligibility.js";

export type ExplicitDuration = Readonly<{
  sourceIndex: number;
  amount: number;
  unit: "hours" | "days" | "business days";
}>;

const GENERIC_WORDS = new Set([
  "how", "many", "hour", "hours", "day", "days", "business", "after",
  "before", "within", "from", "when", "what", "which", "where", "must",
  "should", "the", "this", "that", "have", "does", "will", "would", "are",
  "been", "into", "with", "there", "their", "they", "then", "than",
]);
const words = (value: string): string[] =>
  value.toLowerCase().match(/[a-z]{4,}/gu) ?? [];

/** undefined: not a recognized narrow question, allow normal generation.
 * null: recognized question but missing/conflicting evidence, abstain safely.
 */
export function extractExplicitDuration(
  question: string,
  sources: readonly VerifiedSource[],
): ExplicitDuration | null | undefined {
  const intent = /\bhow\s+many\s+(business\s+)?(hours?|days?)\b/iu.exec(question);
  if (!intent) return undefined;
  const unit: ExplicitDuration["unit"] = intent[2].toLowerCase().startsWith("hour")
    ? "hours" : intent[1] ? "business days" : "days";
  const subjects = [...new Set(words(question).filter(word => !GENERIC_WORDS.has(word)))];
  if (subjects.length < 3) return undefined;

  const matches: Array<ExplicitDuration & { overlap: number }> = [];
  for (const [sourceIndex, source] of sources.entries()) {
    const excerpt = source.excerpt.slice(0, 800);
    const sentences = excerpt.split(/(?<=[.!?।])\s+|\r?\n+/u);
    for (const sentence of sentences) {
      const sentenceWords = new Set(words(sentence));
      const overlap = subjects.filter(word => [...sentenceWords].some(
        candidate => candidate === word ||
          (word.length >= 5 && candidate.startsWith(word.slice(0, 5))),
      )).length;
      if (overlap < Math.max(3, Math.ceil(subjects.length * 0.8))) continue;
      const re = /\b(?:within|after|for|before)\s+([1-9]\d{0,3})\s+(business\s+)?(hours?|days?)\b/giu;
      for (const found of sentence.matchAll(re)) {
        const preceding = sentence.slice(Math.max(0, (found.index ?? 0) - 16), found.index);
        const precedingClause = sentence.slice(0, found.index ?? 0);
        if (/\\b(?:not|never)\\s*$/iu.test(preceding) ||
            /\\b(?:must|should|do|does|will|shall|can)\\s+not\\b/iu.test(precedingClause)) {
          continue;
        }
        const foundUnit: ExplicitDuration["unit"] = found[3].toLowerCase().startsWith("hour")
          ? "hours" : found[2] ? "business days" : "days";
        if (foundUnit !== unit) continue;
        matches.push({ sourceIndex, amount: Number(found[1]), unit, overlap });
      }
    }
  }
  if (!matches.length || new Set(matches.map(match => match.amount)).size !== 1) return null;
  matches.sort((a, b) => b.overlap - a.overlap || a.sourceIndex - b.sourceIndex);
  const chosen = matches[0];
  return { sourceIndex: chosen.sourceIndex, amount: chosen.amount, unit };
}
