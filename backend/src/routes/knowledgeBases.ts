import { Router } from "express";
import { z } from "zod";
import { prisma } from "../config/prisma.js";
import { HttpError } from "../lib/http.js";
import { parseInput } from "../lib/validate.js";
import { currentUserId, requireAuth } from "../middleware/auth.js";
import { accessibleKnowledgeBase, membershipFor, requireManager } from "../middleware/organization.js";

export const knowledgeBasesRouter = Router();
knowledgeBasesRouter.use(requireAuth);
const kbSchema = z.object({
  name: z.string().trim().min(2).max(200),
  description: z.string().trim().max(5000).optional(),
});

knowledgeBasesRouter.get("/", async (req, res) => {
  const organizationId = String(req.query.organizationId || "");
  if (!organizationId) throw new HttpError(400, "organizationId query parameter required");
  await membershipFor(currentUserId(req), organizationId);
  const knowledgeBases = await prisma.knowledgeBase.findMany({
    where: { organizationId },
    select: { id: true, organizationId: true, name: true, description: true, createdAt: true,
      _count: { select: { documents: { where: { deletedAt: null } } } },
    },
    orderBy: { createdAt: "desc" },
  });
  res.json({ success: true, knowledgeBases });
});

knowledgeBasesRouter.post("/", async (req, res) => {
  const input = parseInput(kbSchema.extend({ organizationId: z.string().cuid() }), req.body);
  const member = await membershipFor(currentUserId(req), input.organizationId);
  requireManager(member.role);
  const knowledgeBase = await prisma.knowledgeBase.create({ data: input });
  res.status(201).json({ success: true, knowledgeBase });
});

knowledgeBasesRouter.patch("/:knowledgeBaseId", async (req, res) => {
  const id = String(req.params.knowledgeBaseId);
  const { membership } = await accessibleKnowledgeBase(currentUserId(req), id);
  requireManager(membership.role);
  const changes = parseInput(kbSchema.partial().refine(v => Object.keys(v).length > 0), req.body);
  const knowledgeBase = await prisma.knowledgeBase.update({ where: { id }, data: changes });
  res.json({ success: true, knowledgeBase });
});

knowledgeBasesRouter.delete("/:knowledgeBaseId", async (req, res) => {
  const id = String(req.params.knowledgeBaseId);
  const { membership } = await accessibleKnowledgeBase(currentUserId(req), id);
  requireManager(membership.role);
  const count = await prisma.document.count({ where: { knowledgeBaseId: id } });
  if (count > 0) throw new HttpError(409, "Knowledge base contains documents; archive or retain it instead");
  await prisma.knowledgeBase.delete({ where: { id } });
  res.json({ success: true });
});
