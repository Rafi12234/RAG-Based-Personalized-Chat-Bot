/**
 * Phase 3.1: last-mile validation of untrusted Qdrant matches against MySQL truth.
 * Pure function: performs no I/O and never grants access on its own.
 *
 * The caller MUST have already authenticated the session, authorized the KB,
 * derived `scope` from MySQL, and batch-loaded each relational record directly
 * from MySQL. Missing relational records MUST be passed as null.
 * The caller MUST recheck authorization at the final response boundary.
 */
import { z } from "zod";

const vectorPayloadSchema = z.object({
  organization_id: z.string().min(1).max(128),
  knowledge_base_id: z.string().min(1).max(128),
  document_id: z.string().min(1).max(128),
  document_version_id: z.string().min(1).max(128),
  index_state: z.literal("ready"),
  chunk_index: z.number().int().nonnegative(),
  page_number: z.number().int().positive().nullable().optional(),
  text: z.string().min(1).max(10_000).refine(value => value.trim().length > 0),
});

export type TrustedRetrievalScope = Readonly<{
  organizationId: string;
  knowledgeBaseId: string;
}>;

/** Projection sourced from Prisma, not from the vector payload or the browser. */
export type RelationalSource = Readonly<{
  documentId: string;
  documentVersionId: string;
  latestVersionId: string;
  versionStatus: string;
  organizationId: string;
  knowledgeBaseId: string;
  deletedAt: Date | null;
  documentTitle: string;
}>;

export type RetrievedPoint = Readonly<{
  id: unknown;
  score: unknown;
  payload: unknown;
}>;

export type VerifiedSource = Readonly<{
  chunkId: string;
  documentId: string;
  documentVersionId: string;
  documentTitle: string;
  chunkIndex: number;
  pageNumber: number | null;
  excerpt: string;
  score: number;
}>;

const MAX_EXCERPT_CODEPOINTS = 800;

/**
 * Return null (not a partially trusted source) when ANY guard fails.
 * Caller may rank/deduplicate only AFTER these checks.
 */
export function verifiedSourceFromHit(
  scope: TrustedRetrievalScope,
  point: RetrievedPoint,
  relational: RelationalSource | null,
): VerifiedSource | null {
  if (!scope.organizationId || !scope.knowledgeBaseId || !relational) return null;
  const idResult = z.uuid().safeParse(point.id);
  const scoreResult = z.number().finite().min(-1).max(1).safeParse(point.score);
  const payloadResult = vectorPayloadSchema.safeParse(point.payload);
  if (!idResult.success || !scoreResult.success || !payloadResult.success) return null;

  const p = payloadResult.data;
  if (p.organization_id !== scope.organizationId ||
      p.knowledge_base_id !== scope.knowledgeBaseId ||
      p.document_id !== relational.documentId ||
      p.document_version_id !== relational.documentVersionId ||
      relational.organizationId !== scope.organizationId ||
      relational.knowledgeBaseId !== scope.knowledgeBaseId ||
      relational.deletedAt !== null ||
      relational.versionStatus !== "READY" ||
      relational.latestVersionId !== relational.documentVersionId ||
      !relational.documentTitle.trim()) {
    return null;
  }

  return {
    chunkId: idResult.data,
    documentId: relational.documentId,
    documentVersionId: relational.documentVersionId,
    documentTitle: relational.documentTitle,
    chunkIndex: p.chunk_index,
    pageNumber: p.page_number ?? null,
    // Never propagate unbounded vector text to a client.
    excerpt: Array.from(p.text).slice(0, MAX_EXCERPT_CODEPOINTS).join(""),
    score: scoreResult.data,
  };
}
