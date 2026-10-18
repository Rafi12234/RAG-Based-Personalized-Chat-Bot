import { Router } from "express";
import { z } from "zod";
import { prisma } from "../config/prisma.js";
import { HttpError } from "../lib/http.js";
import { parseInput } from "../lib/validate.js";
import { requireAuth, currentUserId } from "../middleware/auth.js";
import { membershipFor, requireManager } from "../middleware/organization.js";

export const organizationsRouter = Router();
organizationsRouter.use(requireAuth);

organizationsRouter.get("/", async (req, res) => {
  const memberships = await prisma.organizationMember.findMany({
    where: { userId: currentUserId(req) },
    select: { role: true, organization: { select: { id: true, name: true, createdAt: true } } },
    orderBy: { joinedAt: "asc" },
  });
  res.json({ success: true, organizations: memberships.map(m => ({ ...m.organization, role: m.role })) });
});

organizationsRouter.post("/", async (req, res) => {
  const input = parseInput(z.object({ name: z.string().trim().min(2).max(150) }), req.body);
  const org = await prisma.organization.create({
    data: { name: input.name, members: { create: { userId: currentUserId(req), role: "OWNER" } } },
  });
  res.status(201).json({ success: true, organization: org });
});

organizationsRouter.get("/:organizationId/members", async (req, res) => {
  const organizationId = String(req.params.organizationId);
  await membershipFor(currentUserId(req), organizationId);
  const members = await prisma.organizationMember.findMany({
    where: { organizationId },
    select: { role: true, joinedAt: true, user: { select: { id: true, name: true, email: true } } },
    orderBy: { joinedAt: "asc" },
  });
  res.json({ success: true, members });
});

organizationsRouter.post("/:organizationId/members", async (req, res) => {
  const organizationId = String(req.params.organizationId);
  const member = await membershipFor(currentUserId(req), organizationId);
  requireManager(member.role);
  const input = parseInput(z.object({
    email: z.email().max(191).transform(e => e.trim().toLowerCase()),
    role: z.enum(["ADMIN", "MEMBER"]).default("MEMBER"),
  }), req.body);
  if (input.role === "ADMIN" && member.role !== "OWNER") {
    throw new HttpError(403, "Only an owner can appoint administrators");
  }
  const user = await prisma.user.findUnique({ where: { email: input.email } });
  if (!user || !user.isActive) throw new HttpError(404, "User not found. Ask them to register first.");
  const existing = await prisma.organizationMember.findUnique({
    where: { userId_organizationId: { userId: user.id, organizationId } },
  });
  if (existing) throw new HttpError(409, "User is already a member");
  const created = await prisma.organizationMember.create({
    data: { userId: user.id, organizationId, role: input.role },
    select: { id: true, role: true, user: { select: { id: true, name: true, email: true } } },
  });
  res.status(201).json({ success: true, member: created });
});
