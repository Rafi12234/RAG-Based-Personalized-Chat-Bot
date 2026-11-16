import { test } from "node:test";
import assert from "node:assert/strict";
import { databasePoolTimeouts } from "../src/lib/databasePoolPolicy.js";

test("local Docker development receives bounded connection creation grace", () => {
  assert.deepEqual(databasePoolTimeouts("development", "127.0.0.1", 3308), {
    connectTimeout: 30_000,
    acquireTimeout: 45_000,
    initializationTimeout: 40_000,
    idleTimeout: 300,
  });
});

test("production, test and remote hosts keep existing pool timeouts", () => {
  const original = databasePoolTimeouts("production", "127.0.0.1", 3308);
  assert.deepEqual(original, {
    connectTimeout: 10_000,
    acquireTimeout: 20_000,
    initializationTimeout: 19_000,
    idleTimeout: 300,
  });
  for (const args of [
    ["test", "127.0.0.1", 3308],
    ["development", "127.0.0.1", 3307],
    ["development", "mysql", 3306],
    ["production", "mysql", 3306],
  ] as const) assert.deepEqual(databasePoolTimeouts(...args), original);
});
