/** Phase 3.1-3.4: authenticated retrieval and opt-in grounded answers.
 * Both endpoints use identical trusted MySQL + ready-vector eligibility checks.
 * Only the local test harness can inject dependencies; production uses real services.
 */
import { Router, type Request, type RequestHandler } from "express";
import { rateLimit } from "express-rate-limit";
import { prisma } from "../config/prisma.js";
import { HttpError } from "../lib/http.js";
import { idSchema } from "../lib/validate.js";
import { parseRetrievalRequest } from "../lib/retrievalValidation.js";
import type { RelationalSource, RetrievedPoint, TrustedRetrievalScope, VerifiedSource } from "../lib/retrievalEligibility.js";
import { candidateDocumentIds, selectAuthorizedSources } from "../lib/retrievalPostFilter.js";
import { currentUserId, requireAuth } from "../middleware/auth.js";
import { accessibleKnowledgeBase } from "../middleware/organization.js";
import { fetchInternalPoints } from "../services/retrievalClient.js";
import { generateGroundedAnswer } from "../services/groundedAnswer.js";

export type RetrievalDocumentRecord = {
  id: string;
  title: string;
  knowledgeBaseId: string;
  deletedAt: Date | null;
  knowledgeBase: { organizationId: string };
  versions: Array<{ id: string; status: string }>;
};

export type RetrievalRouteDependencies = {
  authenticate: RequestHandler;
  identifyUser: (req: Request) => string;
  authorizeKnowledgeBase: (userId: string, knowledgeBaseId: string) => Promise<{
    kb: { id: string; organizationId: string };
  }>;
  searchPoints: (scope: TrustedRetrievalScope, question: string, topK: number) => Promise<RetrievedPoint[]>;
  loadDocuments: (scope: TrustedRetrievalScope, documentIds: string[]) => Promise<RetrievalDocumentRecord[]>;
  /** Only in offline tests: production never accepts a browser-supplied provider. */
  generateFromPrompt?: (prompt: string) => Promise<string>;
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

async function authorizeAgain(deps: RetrievalRouteDependencies, userId: string, kb: {id: string; organizationId: string}) {
  try {
    const current = await deps.authorizeKnowledgeBase(userId, kb.id);
    if (current.kb.id !== kb.id || current.kb.organizationId !== kb.organizationId) {
      throw new HttpError(404, "Resource not found");
    }
  } catch (error) {
    if (error instanceof HttpError && error.status === 404) throw new HttpError(404, "Resource not found");
    throw error;
  }
}

/** Shared security-critical path: authorization -> vector search -> MySQL -> authorization. */
async function verifiedSearch(deps: RetrievalRouteDependencies, req: Request): Promise<{
  question: string;
  sources: VerifiedSource[];
  kb: { id: string; organizationId: string };
  userId: string;
}> {
  const parsedId = idSchema.safeParse(String(req.params.knowledgeBaseId ?? ""));
  if (!parsedId.success) throw new HttpError(404, "Resource not found");
  const userId = deps.identifyUser(req);
  let kb: { id: string; organizationId: string };
  try { ({ kb } = await deps.authorizeKnowledgeBase(userId, parsedId.data)); }
  catch (error) {
    if (error instanceof HttpError && error.status === 404) throw new HttpError(404, "Resource not found");
    throw error;
  }
  const input = parseRetrievalRequest(req.body);
  const scope: TrustedRetrievalScope = { organizationId: kb.organizationId, knowledgeBaseId: kb.id };
  const hits = await deps.searchPoints(scope, input.question, input.topK);
  if (!Array.isArray(hits) || hits.length > 40) throw new HttpError(503, "Retrieval service unavailable");
  const documents = await deps.loadDocuments(scope, candidateDocumentIds(hits));
  const trusted = new Map<string, RelationalSource>();
  for (const d of documents) {
    const latest = d.versions[0];
    if (!latest) continue;
    trusted.set(d.id, {
      documentId: d.id, documentVersionId: latest.id, latestVersionId: latest.id,
      versionStatus: latest.status, organizationId: d.knowledgeBase.organizationId,
      knowledgeBaseId: d.knowledgeBaseId, deletedAt: d.deletedAt, documentTitle: d.title,
    });
  }
  const sources = selectAuthorizedSources(scope, hits, trusted, input.topK);
  await authorizeAgain(deps, userId, kb);
  return { question: input.question, sources, kb, userId };
}

export function createRetrievalRouter(deps: RetrievalRouteDependencies = productionDependencies) {
  const router = Router();
  router.use(deps.authenticate);
  const retrievalLimiter = rateLimit({ windowMs: 60_000, limit: 12, standardHeaders: "draft-8", legacyHeaders: false });
  const answerLimiter = rateLimit({ windowMs: 60_000, limit: 4, standardHeaders: "draft-8", legacyHeaders: false });

  router.post("/:knowledgeBaseId/retrieve", retrievalLimiter, async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    const { sources } = await verifiedSearch(deps, req);
    res.json({ success: true, status: sources.length ? "retrieved" : "no_relevant_sources", sources });
  });

  router.post("/:knowledgeBaseId/answer", answerLimiter, async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    const { question, sources, kb, userId } = await verifiedSearch(deps, req);
    const generated = await generateGroundedAnswer(question, sources, deps.generateFromPrompt);
    // Local generation can be slow; membership may have changed during inference.
    await authorizeAgain(deps, userId, kb);
    res.json({ success: true, ...generated });
  });
  return router;
}

export const retrievalRouter = createRetrievalRouter();
