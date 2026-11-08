import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import argon2 from "argon2";
import app from "../src/app.js";
import { prisma } from "../src/config/prisma.js";
import { hashToken } from "../src/lib/security.js";

let server: Server;
let baseUrl = "";
const userIds: string[] = [];
const suffix = randomUUID();

async function request(path: string, data: unknown, cookie = "") {
  const response = await fetch(baseUrl + "/api/v1" + path, {
    method: "POST",
    headers: {
      origin: "http://localhost:5173",
      "x-docmind-request": "1",
      "content-type": "application/json",
      ...(cookie ? { cookie } : {}),
    },
    body: JSON.stringify(data),
  });
  return {
    status: response.status,
    body: await response.json() as { message?: string; success: boolean },
    cookie: (response.headers.get("set-cookie") ?? "").split(";")[0],
  };
}

before(async () => {
  await prisma.$connect();
  server = app.listen(0);
  await new Promise<void>(resolve => server.once("listening", resolve));
  baseUrl = "http://127.0.0.1:" + (server.address() as AddressInfo).port;
});

after(async () => {
  try {
    await prisma.passwordResetToken.deleteMany({ where: { userId: { in: userIds } } });
    await prisma.authSession.deleteMany({ where: { userId: { in: userIds } } });
    await prisma.user.deleteMany({ where: { id: { in: userIds } } });
  } finally {
    if (server) await new Promise<void>((resolve, reject) =>
      server.close(error => error ? reject(error) : resolve()));
    await prisma.$disconnect();
  }
});

test("Phase 4.1: invalid/expired/reused token rejection, password change and session revocation", async () => {
  const address = "phase4-recovery-" + suffix + "@example.invalid";
  const otherAddress = "phase4-other-" + suffix + "@example.invalid";
  const oldPassword = "OldLocalTestPassword!2026";
  const newPassword = "NewLocalTestPassword!2026";
  const oldHash = await argon2.hash(oldPassword, { type: argon2.argon2id });
  const user = await prisma.user.create({ data: { name: "Reset Test", email: address, passwordHash: oldHash } });
  const other = await prisma.user.create({ data: { name: "Other Test", email: otherAddress, passwordHash: oldHash } });
  userIds.push(user.id, other.id);

  const login = await request("/auth/login", { email: address, password: oldPassword });
  assert.equal(login.status, 200);
  assert.ok(login.cookie.startsWith("docmind_session="));
  const otherLogin = await request("/auth/login", { email: otherAddress, password: oldPassword });
  assert.equal(otherLogin.status, 200);

  const bad = await request("/auth/reset-password", { token: "not-a-token", password: newPassword });
  assert.equal(bad.status, 400);
  assert.match(bad.body.message ?? "", /invalid or expired/i);

  const expiredToken = randomBytes(32).toString("base64url");
  await prisma.passwordResetToken.create({
    data: { userId: user.id, tokenHash: hashToken(expiredToken), expiresAt: new Date(Date.now() - 1000) },
  });
  assert.equal((await request("/auth/reset-password", { token: expiredToken, password: newPassword })).status, 400);

  const validToken = randomBytes(32).toString("base64url");
  await prisma.passwordResetToken.create({
    data: { userId: user.id, tokenHash: hashToken(validToken), expiresAt: new Date(Date.now() + 30 * 60_000) },
  });
  const reset = await request("/auth/reset-password", { token: validToken, password: newPassword });
  assert.equal(reset.status, 200, JSON.stringify(reset.body));
  assert.equal((await request("/auth/reset-password", { token: validToken, password: newPassword })).status, 400);
  assert.equal((await request("/auth/login", { email: address, password: oldPassword })).status, 401);
  assert.equal((await request("/auth/login", { email: address, password: newPassword })).status, 200);

  const oldSession = await fetch(baseUrl + "/api/v1/auth/me", { headers: { cookie: login.cookie } });
  assert.equal(oldSession.status, 401);
  const otherSession = await fetch(baseUrl + "/api/v1/auth/me", { headers: { cookie: otherLogin.cookie } });
  assert.equal(otherSession.status, 200);
  assert.equal(await prisma.passwordResetToken.count({ where: { userId: user.id } }), 0);
});
