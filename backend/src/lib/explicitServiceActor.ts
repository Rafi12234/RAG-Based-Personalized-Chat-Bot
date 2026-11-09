/**
 * Conservative fallback for "Which service [action] [objects]?" questions.
 * Extracts a named sentence subject only when the question and a verified
 * sentence contain the same verb and nearly all meaningful object terms.
 * No product names, dataset labels or tenant-specific assumptions are encoded.
 * Not general entailment. Called only after the local model abstains twice.
 */
import type { VerifiedSource } from "./retrievalEligibility.js";

export type ServiceActorResult =
  | Readonly<{ state: "not_applicable" | "missing" | "conflict" }>
  | Readonly<{ state: "found"; name: string; sourceIndex: number }>;

const ACTION = String.raw`(?:transports?|delivers?|routes?|sends?|carries?)`;
const REQUEST = new RegExp(String.raw`^\s*which\s+(?:named\s+)?(?:service|system|component|tool|broker)\s+(` +
  ACTION + String.raw`)\s+(.+?)\s*\?\s*$`, "iu");
const ASSERTION = new RegExp(String.raw`^\s*([A-Z][A-Za-z0-9_-]{1,39})\s+(` +
  ACTION + String.raw`)\s+(.+)$`, "iu");
const STOPWORDS = new Set([
  "the", "a", "an", "to", "in", "on", "and", "or", "with", "between",
  "from", "for", "by", "at", "of", "that", "this", "which", "service", "system",
  "component", "tool", "broker", "are", "is",
]);
const BAD_ACTORS = new Set(["the", "a", "an", "not", "never", "no", "this", "that", "it"]);

function words(s: string): string[] {
  return [...new Set((s.toLowerCase().match(/[a-z][a-z0-9_-]{1,}/gu) ?? [])
    .filter(w => !STOPWORDS.has(w)))];
}

function verbBase(s: string): string {
  const v = s.toLowerCase();
  if (v === "carries") return "carry";
  if (v === "routes") return "route";
  if (v === "sends") return "send";
  if (v === "delivers") return "deliver";
  if (v === "transports") return "transport";
  return v;
}

export function resolveExplicitServiceActor(
  question: string,
  sources: readonly VerifiedSource[],
): ServiceActorResult {
  const request = REQUEST.exec(question);
  if (!request) return { state: "not_applicable" };
  const subjectWords = words(request[2]);
  // Require a specific multi-term relation, never generic "Which service sends data?".
  if (subjectWords.length < 4) return { state: "not_applicable" };
  const candidates: Array<{ name: string; sourceIndex: number }> = [];
  for (const [sourceIndex, source] of sources.entries()) {
    const segments = source.excerpt.slice(0, 800)
      .split(/(?<=[.!?।])\s+|[\r\n]+|;/u)
      .map(x => x.trim().replace(/^passage:\s*/iu, ""));
    for (const segment of segments) {
      const m = ASSERTION.exec(segment);
      if (!m || BAD_ACTORS.has(m[1].toLowerCase()) ||
          verbBase(m[2]) !== verbBase(request[1])) continue;
      // A negative source sentence is not affirmative evidence.
      if (/\b(?:not|never|no)\b/iu.test(m[3])) continue;
      const actual = new Set(words(m[3]));
      const overlap = subjectWords.filter(x => actual.has(x)).length;
      if (overlap < 4 || overlap / subjectWords.length < 0.75) continue;
      candidates.push({ name: m[1], sourceIndex });
    }
  }
  if (!candidates.length) return { state: "missing" };
  if (new Set(candidates.map(x => x.name.toLowerCase())).size !== 1) {
    return { state: "conflict" };
  }
  return { state: "found", ...candidates[0] };
}
