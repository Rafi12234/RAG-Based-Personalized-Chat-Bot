import { prisma } from "../config/prisma.js";
import { HttpError } from "../lib/http.js";
import { retryTransientPoolRead } from "../lib/transientDatabase.js";

export async function membershipFor(userId: string, organizationId: string) {
  const membership = await retryTransientPoolRead(() => prisma.organizationMember.findUnique({
    where: { userId_organizationId: { userId, organizationId } },
  }));
  if (!membership) throw new HttpError(404, "Resource not found");
  return membership;
}

export function requireManager(role: "OWNER" | "ADMIN" | "MEMBER") {
  if (role === "MEMBER") throw new HttpError(403, "Manager access required");
}

export async function accessibleKnowledgeBase(userId: string, knowledgeBaseId: string) {
  const kb = await retryTransientPoolRead(() => prisma.knowledgeBase.findUnique({ where: { id: knowledgeBaseId } }));
  if (!kb) throw new HttpError(404, "Knowledge base not found");
  const membership = await membershipFor(userId, kb.organizationId);
  return { kb, membership };
}
