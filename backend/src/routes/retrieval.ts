/** Phase 3.1 read-only, session-authenticated retrieval. No LLM or vector writes. */
import { Router } from "express";
import { rateLimit } from "express-rate-limit";
import { prisma } from "../config/prisma.js";
import { HttpError } from "../lib/http.js";
import { idSchema } from "../lib/validate.js";
import { parseRetrievalRequest } from "../lib/retrievalValidation.js";
import type { RelationalSource, TrustedRetrievalScope } from "../lib/retrievalEligibility.js";
import { candidateDocumentIds, selectAuthorizedSources } from "../lib/retrievalPostFilter.js";
import { currentUserId, requireAuth } from "../middleware/auth.js";
import { accessibleKnowledgeBase } from "../middleware/organization.js";
import { fetchInternalPoints } from "../services/retrievalClient.js";

export const retrievalRouter = Router();
retrievalRouter.use(requireAuth);
const retrievalLimiter = rateLimit({ windowMs: 60_000, limit: 12, standardHeaders: "draft-8", legacyHeaders: false });

retrievalRouter.post("/:knowledgeBaseId/retrieve", retrievalLimiter, async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  const parsedId = idSchema.safeParse(String(req.params.knowledgeBaseId ?? ""));
  if (!parsedId.success) throw new HttpError(404, "Resource not found");
  const userId = currentUserId(req);
  // Authorize BEFORE forwarding the question to the internal model or querying Qdrant.
  let kb: Awaited<ReturnType<typeof accessibleKnowledgeBase>>["kb"];
  try { ({ kb } = await accessibleKnowledgeBase(userId, parsedId.data)); }
  catch (error) {
    if (error instanceof HttpError && error.status === 404) throw new HttpError(404, "Resource not found");
    throw error;
  }
  const input = parseRetrievalRequest(req.body);
  const scope: TrustedRetrievalScope = { organizationId: kb.organizationId, knowledgeBaseId: kb.id };
  const hits = await fetchInternalPoints(scope, input.question, input.topK);
  // Avoid accepting an unbounded or malformed internal service response.
  if (hits.length > 40) throw new HttpError(503, "Retrieval service unavailable");
  const ids = candidateDocumentIds(hits);
  const documents = ids.length ? await prisma.document.findMany({
    where: {
      id: { in: ids }, knowledgeBaseId: scope.knowledgeBaseId, deletedAt: null,
      knowledgeBase: { organizationId: scope.organizationId },
    },
    select: {
      id: true, title: true, knowledgeBaseId: true, deletedAt: true,
      knowledgeBase: { select: { organizationId: true } },
      versions: { orderBy: { version: "desc" }, take: 1, select: { id: true, status: true } },
    },
  }) : [];
  const trusted = new Map<string, RelationalSource>();
  for (const d of documents) {
    const v = d.versions[0];
    if (!v) continue;
    trusted.set(d.id, {
      documentId: d.id, documentVersionId: v.id, latestVersionId: v.id,
      versionStatus: v.status, organizationId: d.knowledgeBase.organizationId,
      knowledgeBaseId: d.knowledgeBaseId, deletedAt: d.deletedAt, documentTitle: d.title,
    });
  }
  const sources = selectAuthorizedSources(scope, hits, trusted, input.topK);
  // Recheck membership after the potentially slow Qdrant/embedding/DB operations.
  try {
    const current = await accessibleKnowledgeBase(userId, kb.id);
    if (current.kb.organizationId !== scope.organizationId) throw new HttpError(404, "Resource not found");
  } catch (error) {
    if (error instanceof HttpError && error.status === 404) throw new HttpError(404, "Resource not found");
    throw error;
  }
  res.json({ success: true, status: sources.length ? "retrieved" : "no_relevant_sources", sources });
});
