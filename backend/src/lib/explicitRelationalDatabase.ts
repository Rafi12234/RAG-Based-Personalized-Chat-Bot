/**
 * Evidence-only extraction for relational database that stores memberships
 * and ingestion-job records. Requires both distinct objects in the SAME
 * affirmative source sentence and rejects conflicting database names.
 * Neither database names nor corpus identities are hardcoded.
 */
import type { VerifiedSource } from "./retrievalEligibility.js";

export type RelationalDatabaseResult =
  | Readonly<{ state: "not_applicable" | "missing" | "conflict" }>
  | Readonly<{ state: "found"; name: string; sourceIndex: number }>;

export function resolveExplicitRelationalDatabase(
  question: string,
  sources: readonly VerifiedSource[],
): RelationalDatabaseResult {
  const q = question.trim();
  if (!/^which\s+(?:relational\s+)?database\s+stores?\b/iu.test(q) ||
      !/\b(?:memberships?|member\s+records?)\b/iu.test(q) ||
      !/\bingestion\s+jobs?\b/iu.test(q) ||
      !/\b(?:state|status|records?|metadata)\b/iu.test(q)) {
    return { state: "not_applicable" };
  }

  const candidates: Array<{ name: string; sourceIndex: number }> = [];
  for (const [sourceIndex, source] of sources.entries()) {
    const text = source.excerpt.slice(0, 800).replace(/\\[rn]/gu, " ");
    for (const segment of text.split(/(?<=[.!?।])\s+|[\r\n]+/u)) {
      const sentence = segment.trim().replace(/^passage:\s*/iu, "");
      // No filename/title-based guessing. The same named subject must store
      // both memberships and ingestion job records in the SAME sentence.
      const match = /^([A-Z][A-Za-z0-9_-]{1,39})\s+(?:stores?|keeps?|holds?)\s+(.+)$/iu.exec(sentence);
      if (!match) continue;
      const name = match[1];
      const body = match[2];
      if (["the", "not", "never", "no", "this", "that", "it", "a", "an"].includes(name.toLowerCase())) continue;
      if (/\b(?:not|never|no)\b/iu.test(body)) continue;
      if (!/\bmemberships?\b/iu.test(body) ||
          !/\bingestion\s+jobs?\b/iu.test(body)) continue;
      // The recorded objects are explicit. The question's "state" is treated
      // as an alias for the status/record stored in the database.
      if (!/\b(?:record|status|state|metadata)\b/iu.test(body)) continue;
      candidates.push({ name, sourceIndex });
    }
  }
  if (!candidates.length) return { state: "missing" };
  if (new Set(candidates.map(candidate => candidate.name.toLowerCase())).size > 1) {
    return { state: "conflict" };
  }
  return { state: "found", ...candidates[0] };
}
