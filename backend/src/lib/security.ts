import { createHash, randomBytes } from "node:crypto";
import type { CookieOptions } from "express";
import { env } from "../config/env.js";

export const SESSION_COOKIE = "docmind_session";
export const SESSION_DAYS = 7;

export function newSessionToken() {
  return randomBytes(32).toString("base64url");
}
export function hashToken(token: string) {
  return createHash("sha256").update(token).digest("hex");
}
export function sessionExpiry() {
  return new Date(Date.now() + SESSION_DAYS * 86400_000);
}
export function sessionCookieOptions(): CookieOptions {
  return {
    httpOnly: true,
    secure: env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/api/v1",
  };
}
export function readSessionCookie(header?: string): string | undefined {
  const entry = (header || "").split(";").map(x => x.trim())
    .find(x => x.startsWith(`${SESSION_COOKIE}=`));
  const value = entry?.slice(SESSION_COOKIE.length + 1);
  return value && /^[A-Za-z0-9_-]{40,100}$/.test(value) ? value : undefined;
}
