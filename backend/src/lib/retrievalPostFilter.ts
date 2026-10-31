/** Pure functions for post-search eligibility and bounded output. */
import { idSchema } from "./validate.js";
import { verifiedSourceFromHit } from "./retrievalEligibility.js";
import type { RelationalSource, TrustedRetrievalScope, RetrievedPoint } from "./retrievalEligibility.js";

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

/** Pure post-filter: fail-closed on missing DB truth, never use payload titles. */
export function selectAuthorizedSources(
  scope: TrustedRetrievalScope,
  hits: RetrievedPoint[],
  records: ReadonlyMap<string, RelationalSource>,
  topK: number,
) {
  const seen = new Set<string>();
  const result: NonNullable<ReturnType<typeof verifiedSourceFromHit>>[] = [];
  for (const hit of hits) {
    const p = hit.payload;
    const id = p && typeof p === "object" && !Array.isArray(p) && "document_id" in p
      ? p.document_id : null;
    const source = verifiedSourceFromHit(scope, hit, typeof id === "string" ? records.get(id) ?? null : null);
    if (!source || seen.has(source.chunkId)) continue;
    seen.add(source.chunkId);
    result.push(source);
    if (result.length >= topK) break;
  }
  return result;
}

