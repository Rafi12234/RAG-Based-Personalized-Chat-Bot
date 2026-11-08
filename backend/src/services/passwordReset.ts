import { randomBytes } from "node:crypto";
import argon2 from "argon2";
import { prisma } from "../config/prisma.js";
import { env } from "../config/env.js";
import { HttpError } from "../lib/http.js";
import { hashToken } from "../lib/security.js";

// Never put reset tokens in logs, API replies, or the database in plaintext.
const RESET_LIFETIME_MS = 30 * 60_000;
const REQUEST_WINDOW_MS = 60 * 60_000;
const REQUEST_LIMIT_PER_ACCOUNT = 3;
export const RESET_REQUEST_REPLY =
  "If this email belongs to an active account, a password-reset link will be sent.";

export function resetMailConfigured(): boolean {
  return Boolean(process.env.RESEND_API_KEY?.trim() && process.env.RESET_EMAIL_FROM?.trim());
}

async function deliverResetMail(address: string, resetUrl: string): Promise<void> {
  // Fixed HTTPS API endpoint: no user-controlled host or redirect destination.
  const key = process.env.RESEND_API_KEY?.trim();
  const from = process.env.RESET_EMAIL_FROM?.trim();
  if (!key || !from) throw new Error("Reset email delivery not configured");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await fetch("https://api.resend.com/emails", {
      method: "POST",
      signal: controller.signal,
      redirect: "error",
      headers: {
        authorization: `Bearer ${key}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        from,
        to: [address],
        subject: "Reset your DocMind-AI password",
        text: [
          "You requested a DocMind-AI password reset.",
          "Open this link within 30 minutes to choose a new password:",
          resetUrl,
          "If you did not request this, you can ignore this message.",
        ].join("\n\n"),
      }),
    });
    if (!response.ok) throw new Error("Email provider rejected password-reset delivery");
  } finally {
    clearTimeout(timeout);
  }
}

export async function requestPasswordReset(address: string): Promise<void> {
  // Check configuration before the lookup to avoid different HTTP statuses for
  // existing versus unknown accounts.
  if (!resetMailConfigured()) throw new HttpError(503, "Password recovery is temporarily unavailable");
  const user = await prisma.user.findUnique({
    where: { email: address },
    select: { id: true, isActive: true },
  });
  if (!user?.isActive) return;

  const since = new Date(Date.now() - REQUEST_WINDOW_MS);
  const issued = await prisma.passwordResetToken.count({
    where: { userId: user.id, createdAt: { gt: since } },
  });
  if (issued >= REQUEST_LIMIT_PER_ACCOUNT) return;

  const token = randomBytes(32).toString("base64url");
  const record = await prisma.passwordResetToken.create({
    data: {
      userId: user.id,
      tokenHash: hashToken(token),
      expiresAt: new Date(Date.now() + RESET_LIFETIME_MS),
    },
    select: { id: true },
  });
  try {
    // A fragment is not sent to the frontend web server during initial loading.
    const resetUrl = `${env.FRONTEND_URL.replace(/\/$/, "")}/#reset=${token}`;
    await deliverResetMail(address, resetUrl);
  } catch {
    // Never log mail request bodies, raw token, reset URL or email address.
    await prisma.passwordResetToken.deleteMany({ where: { id: record.id } });
    console.error("DocMind password-reset email delivery failed (details redacted)");
    // Keep the same public response regardless of account existence.
  }
}

export async function redeemPasswordReset(token: string, password: string): Promise<void> {
  if (!/^[A-Za-z0-9_-]{43}$/u.test(token)) {
    throw new HttpError(400, "Reset link is invalid or expired");
  }
  const tokenHash = hashToken(token);
  const record = await prisma.passwordResetToken.findUnique({
    where: { tokenHash },
    select: { id: true, userId: true, consumedAt: true, expiresAt: true, user: { select: { isActive: true } } },
  });
  if (!record || record.consumedAt || record.expiresAt <= new Date() || !record.user.isActive) {
    throw new HttpError(400, "Reset link is invalid or expired");
  }
  const passwordHash = await argon2.hash(password, { type: argon2.argon2id });
  await prisma.$transaction(async tx => {
    // Conditional claim is a write-atomic single-use guard even under concurrent requests.
    const claim = await tx.passwordResetToken.updateMany({
      where: { id: record.id, consumedAt: null, expiresAt: { gt: new Date() } },
      data: { consumedAt: new Date() },
    });
    if (claim.count !== 1) throw new HttpError(400, "Reset link is invalid or expired");
    const user = await tx.user.updateMany({
      where: { id: record.userId, isActive: true },
      data: { passwordHash },
    });
    if (user.count !== 1) throw new HttpError(400, "Reset link is invalid or expired");
    await tx.authSession.deleteMany({ where: { userId: record.userId } });
    await tx.passwordResetToken.deleteMany({ where: { userId: record.userId } });
  });
}
