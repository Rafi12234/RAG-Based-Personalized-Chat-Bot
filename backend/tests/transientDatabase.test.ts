/** Phase 3.8 MySQL pool reliability: synthetic offline failures only. */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  isPrismaPoolUnavailable, poolFailureCauseCategory, retryTransientPoolRead,
} from "../src/lib/transientDatabase.js";

const poolError = (message = "pool failed to retrieve a connection from pool", cause?: unknown) => ({
  code: "P2039",
  meta: { driverAdapterError: { message, cause } },
});

test("pool retry: known transient acquisition error retries one read and recovers", async () => {
  let attempts = 0;
  let pauses = 0;
  const result = await retryTransientPoolRead(async () => {
    attempts += 1;
    if (attempts === 1) throw poolError();
    return "authorized-read";
  }, async () => { pauses++; });
  assert.equal(result, "authorized-read");
  assert.equal(attempts, 2);
  assert.equal(pauses, 1);
});

test("pool retry: persistent outage fails after exactly two reads", async () => {
  let attempts = 0;
  await assert.rejects(
    retryTransientPoolRead(async () => { attempts++; throw poolError(); }, async () => {}),
    error => isPrismaPoolUnavailable(error),
  );
  assert.equal(attempts, 2);
});

test("pool retry: unrelated errors never retried", async () => {
  let attempts = 0;
  const error = new Error("unknown application failure");
  await assert.rejects(
    retryTransientPoolRead(async () => { attempts++; throw error; }, async () => {}),
    thrown => thrown === error,
  );
  assert.equal(attempts, 1);
});

test("pool retry: unrelated Prisma P2039 does not qualify", () => {
  assert.equal(isPrismaPoolUnavailable(poolError("some other adapter failure")), false);
  assert.equal(isPrismaPoolUnavailable({ code: "P2002" }), false);
  assert.equal(isPrismaPoolUnavailable(null), false);
});

test("pool diagnostic: only pre-approved category, never raw driver message", () => {
  const failure = poolError(undefined, {
    code: "ECONNREFUSED",
    message: "Connection refused for sensitive-host secret-password",
  });
  assert.equal(poolFailureCauseCategory(failure), "connection_refused");
  assert.equal(poolFailureCauseCategory({ code: "P2002" }), "not_pool_failure");
});

test("pool diagnostic: unknown causes remain generic", () => {
  assert.equal(poolFailureCauseCategory(poolError()), "cause_unknown");
});
