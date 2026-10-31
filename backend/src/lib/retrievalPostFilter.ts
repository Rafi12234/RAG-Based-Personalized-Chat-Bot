/** Pure functions for post-search eligibility, deterministic score ordering and bounded output. */
import { idSchema } from "./validate.js";
import { verifiedSourceFromHit } from "./retrievalEligibility.js";
import type { RelationalSource, TrustedRetrievalScope, RetrievedPoint, VerifiedSource } from "./retrievalEligibility.js";

export function candidateDocumentIds(points: RetrievedPoint[]): string[] {
  const ids = new Set<string>();
  for (const point of points) {
    const p = point.payload;
    if (p && typeof p === "object" && !Array.isArray(p) && "document_id" in p) {
      const id = p.document_id;
      if (typeof id === "string" && idSchema.safeParse(id).success) ids.add(id);
    }
  }
  return [...ids];
}

/**
 * Verify EVERY candidate against relational truth before ranking or deduplication.
 * Rank by verified similarity score descending; break score ties by the original
 * result order. For duplicate chunk IDs, retain the highest-scoring verified hit.
 * Never rank, return, or use the payload of an unauthorized/invalid candidate.
 *
 * This is NOT a relevance threshold or a measure of retrieval accuracy.
 */
export function selectAuthorizedSources(
  scope: TrustedRetrievalScope,
  hits: RetrievedPoint[],
  records: ReadonlyMap<string, RelationalSource>,
  topK: number,
): VerifiedSource[] {
  // Public API already validates topK; fail closed if this helper is misused.
  if (!Number.isInteger(topK) || topK < 1 || topK > 10) return [];

  const verified: Array<{ source: VerifiedSource; position: number }> = [];
  for (const [position, hit] of hits.entries()) {
    const p = hit.payload;
    const id = p && typeof p === "object" && !Array.isArray(p) && "document_id" in p
      ? p.document_id : null;
    const source = verifiedSourceFromHit(scope, hit, typeof id === "string" ? records.get(id) ?? null : null);
    if (source) verified.push({ source, position });
  }

  verified.sort((a, b) => b.source.score - a.source.score || a.position - b.position);
  const seen = new Set<string>();
  const result: VerifiedSource[] = [];
  for (const { source } of verified) {
    if (seen.has(source.chunkId)) continue;
    seen.add(source.chunkId);
    result.push(source);
    if (result.length >= topK) break;
  }
  return result;
}
