import { test } from "node:test";
import assert from "node:assert/strict";
import { allowLocalDevMySqlKeyRetrieval } from "../src/lib/localMySqlAuth.js";

test("development MySQL RSA-key retrieval allowed only for Docker loopback", () => {
  assert.equal(allowLocalDevMySqlKeyRetrieval("development", "127.0.0.1", 3308), true);
});
test("production and test refuse unauthenticated RSA-key retrieval", () => {
  for (const env of ["production", "test"]) {
    assert.equal(allowLocalDevMySqlKeyRetrieval(env, "127.0.0.1", 3308), false);
  }
});
test("remote hosts, alternate ports and localhost aliases are denied", () => {
  for (const [host, port] of [["localhost", 3308], ["192.168.0.8", 3308], ["127.0.0.1", 3306], ["::1", 3308]] as const) {
    assert.equal(allowLocalDevMySqlKeyRetrieval("development", host, port), false);
  }
});
