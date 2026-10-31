/** Phase 3.1 read-only, session-authenticated retrieval. No LLM or vector writes.
 * Phase 3.1 security-regression harness can inject in-memory data sources;
 * the exported production router always uses real auth, Prisma and Python.
 */
import { Router, type Request, type RequestHandler } from "express";
import { rateLimit } from "express-rate-limit";
import { prisma } from "../config/prisma.js";
import { HttpError } from "../lib/http.js";
import { idSchema } from "../lib/validate.js";
import { parseRetrievalRequest } from "../lib/retrievalValidation.js";
import type { RelationalSource, RetrievedPoint, TrustedRetrievalScope } from "../lib/retrievalEligibility.js";
import { candidateDocumentIds, selectAuthorizedSources } from "../lib/retrievalPostFilter.js";
import { currentUserId, requireAuth } from "../middleware/auth.js";
import { accessibleKnowledgeBase } from "../middleware/organization.js";
import { fetchInternalPoints } from "../services/retrievalClient.js";

/** The fields needed from a trusted relational (Prisma) document query. */
export type RetrievalDocumentRecord = {
  id: string;
  title: string;
  knowledgeBaseId: string;
  deletedAt: Date | null;
  knowledgeBase: { organizationId: string };
  versions: Array<{ id: string; status: string }>;
};

/** Injectable only for offline route tests; never accept dependencies from HTTP. */
export type RetrievalRouteDependencies = {
  authenticate: RequestHandler;
  identifyUser: (req: Request) => string;
  authorizeKnowledgeBase: (userId: string, knowledgeBaseId: string) => Promise<{
    kb: { id: string; organizationId: string };
  }>;
  searchPoints: (scope: TrustedRetrievalScope, question: string, topK: number) => Promise<RetrievedPoint[]>;
  loadDocuments: (scope: TrustedRetrievalScope, documentIds: string[]) => Promise<RetrievalDocumentRecord[]>;
};

const productionDependencies: RetrievalRouteDependencies = {
  authenticate: requireAuth,
  identifyUser: currentUserId,
  authorizeKnowledgeBase: accessibleKnowledgeBase,
  searchPoints: fetchInternalPoints,
  async loadDocuments(scope, ids) {
    if (!ids.length) return [];
    return prisma.document.findMany({
      where: {
        id: { in: ids }, knowledgeBaseId: scope.knowledgeBaseId, deletedAt: null,
        knowledgeBase: { organizationId: scope.organizationId },
      },
      select: {
        id: true, title: true, knowledgeBaseId: true, deletedAt: true,
        knowledgeBase: { select: { organizationId: true } },
        versions: { orderBy: { version: "desc" }, take: 1, select: { id: true, status: true } },
      },
    });
  },
};

export function createRetrievalRouter(deps: RetrievalRouteDependencies = productionDependencies) {
  const router = Router();
  router.use(deps.authenticate);
  const limiter = rateLimit({ windowMs: 60_000, limit: 12, standardHeaders: "draft-8", legacyHeaders: false });

  router.post("/:knowledgeBaseId/retrieve", limiter, async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    const parsedId = idSchema.safeParse(String(req.params.knowledgeBaseId ?? ""));
    if (!parsedId.success) throw new HttpError(404, "Resource not found");
    const userId = deps.identifyUser(req);
    // Authorize BEFORE forwarding a question to the embedding model or Qdrant.
    let kb: { id: string; organizationId: string };
    try { ({ kb } = await deps.authorizeKnowledgeBase(userId, parsedId.data)); }
    catch (error) {
      if (error instanceof HttpError && error.status === 404) throw new HttpError(404, "Resource not found");
      throw error;
    }
    const input = parseRetrievalRequest(req.body);
    const scope: TrustedRetrievalScope = { organizationId: kb.organizationId, knowledgeBaseId: kb.id };
    const hits = await deps.searchPoints(scope, input.question, input.topK);
    if (hits.length > 40) throw new HttpError(503, "Retrieval service unavailable");
    const ids = candidateDocumentIds(hits);
    const documents = await deps.loadDocuments(scope, ids);
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
    // Recheck membership after potentially slow embedding/Qdrant/Prisma operations.
    try {
      const current = await deps.authorizeKnowledgeBase(userId, kb.id);
      if (current.kb.organizationId !== scope.organizationId) throw new HttpError(404, "Resource not found");
    } catch (error) {
      if (error instanceof HttpError && error.status === 404) throw new HttpError(404, "Resource not found");
      throw error;
    }
    res.json({ success: true, status: sources.length ? "retrieved" : "no_relevant_sources", sources });
  });
  return router;
}

export const retrievalRouter = createRetrievalRouter();
