import { Router } from "express";
import { rateLimit } from "express-rate-limit";
import argon2 from "argon2";
import { z } from "zod";
import { prisma } from "../config/prisma.js";
import { HttpError } from "../lib/http.js";
import { parseInput } from "../lib/validate.js";
import {
  SESSION_COOKIE, hashToken, newSessionToken, sessionCookieOptions, sessionExpiry,
} from "../lib/security.js";
import { currentUserId, requireAuth } from "../middleware/auth.js";

export const authRouter = Router();
const authLimiter = rateLimit({ windowMs: 15 * 60_000, limit: 10, standardHeaders: "draft-8", legacyHeaders: false });
const email = z.email().max(191).transform(x => x.trim().toLowerCase());
const registerSchema = z.object({
  name: z.string().trim().min(2).max(120),
  email,
  password: z.string().min(12).max(128),
  organizationName: z.string().trim().min(2).max(150),
});
const loginSchema = z.object({ email, password: z.string().min(1) });

async function openSession(userId: string, res: import("express").Response) {
  const token = newSessionToken();
  const expiresAt = sessionExpiry();
  await prisma.authSession.create({ data: { userId, tokenHash: hashToken(token), expiresAt } });
  res.cookie(SESSION_COOKIE, token, { ...sessionCookieOptions(), expires: expiresAt });
}

const userSelect = {
  id: true, name: true, email: true, createdAt: true,
  memberships: { select: { organizationId: true, role: true, organization: { select: { id: true, name: true } } } },
} as const;

authRouter.post("/register", authLimiter, async (req, res) => {
  const input = parseInput(registerSchema, req.body);
  const passwordHash = await argon2.hash(input.password, { type: argon2.argon2id });
  const user = await prisma.$transaction(async tx => {
    const created = await tx.user.create({
      data: { name: input.name, email: input.email, passwordHash },
    });
    const org = await tx.organization.create({ data: { name: input.organizationName } });
    await tx.organizationMember.create({
      data: { userId: created.id, organizationId: org.id, role: "OWNER" },
    });
    return created;
  });
  await openSession(user.id, res);
  const fullUser = await prisma.user.findUniqueOrThrow({ where: { id: user.id }, select: userSelect });
  res.status(201).json({ success: true, user: fullUser });
});

authRouter.post("/login", authLimiter, async (req, res) => {
  const input = parseInput(loginSchema, req.body);
  const user = await prisma.user.findUnique({ where: { email: input.email } });
  // A failed login must not disclose whether a particular email exists.
  if (!user || !user.isActive || !(await argon2.verify(user.passwordHash, input.password))) {
    throw new HttpError(401, "Invalid email or password");
  }
  await openSession(user.id, res);
  const fullUser = await prisma.user.findUniqueOrThrow({ where: { id: user.id }, select: userSelect });
  res.json({ success: true, user: fullUser });
});

authRouter.get("/me", requireAuth, async (req, res) => {
  const user = await prisma.user.findUnique({
    where: { id: currentUserId(req) }, select: userSelect,
  });
  if (!user) throw new HttpError(401, "Session expired or invalid");
  res.json({ success: true, user });
});

authRouter.post("/logout", requireAuth, async (req, res) => {
  await prisma.authSession.deleteMany({ where: { id: req.auth!.sessionId } });
  res.clearCookie(SESSION_COOKIE, sessionCookieOptions());
  res.json({ success: true });
});
