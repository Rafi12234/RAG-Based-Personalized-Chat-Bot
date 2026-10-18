import type { RequestHandler } from "express";
import { prisma } from "../config/prisma.js";
import { HttpError } from "../lib/http.js";
import { hashToken, readSessionCookie } from "../lib/security.js";

export const requireAuth: RequestHandler = async (req, _res, next) => {
  const token = readSessionCookie(req.headers.cookie);
  if (!token) return next(new HttpError(401, "Authentication required"));
  const tokenHash = hashToken(token);
  const session = await prisma.authSession.findUnique({
    where: { tokenHash },
    include: { user: { select: { id: true, isActive: true } } },
  });
  if (!session || session.expiresAt <= new Date() || !session.user.isActive) {
    return next(new HttpError(401, "Session expired or invalid"));
  }
  req.auth = { userId: session.userId, sessionId: session.id, tokenHash };
  next();
};

export function currentUserId(req: { auth?: { userId: string } }): string {
  if (!req.auth) throw new HttpError(401, "Authentication required");
  return req.auth.userId;
}
