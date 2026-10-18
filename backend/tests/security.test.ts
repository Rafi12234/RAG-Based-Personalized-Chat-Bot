import { test } from "node:test";
import assert from "node:assert/strict";
import { hashToken, newSessionToken, readSessionCookie } from "../src/lib/security.js";

test("session tokens are unique and not stored in plaintext", () => {
  const first = newSessionToken();
  const second = newSessionToken();
  assert.notEqual(first, second);
  assert.notEqual(hashToken(first), first);
  assert.equal(hashToken(first).length, 64);
  assert.equal(hashToken(first), hashToken(first));
});

test("session cookie parsing rejects malformed or absent tokens", () => {
  const token = newSessionToken();
  assert.equal(readSessionCookie(`theme=dark; docmind_session=${token}; foo=bar`), token);
  assert.equal(readSessionCookie("docmind_session=bad"), undefined);
  assert.equal(readSessionCookie(), undefined);
});
