import { test } from "node:test";
import assert from "node:assert/strict";
import express from "express";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import { HttpError, errorHandler } from "../src/lib/http.js";

async function errorResponse(error: unknown) {
  const app = express();
  app.get("/probe", (_req, _res, next) => next(error));
  app.use(errorHandler);
  const server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  try {
    const response = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}/probe`);
    return { status: response.status, body: await response.json() };
  } finally {
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
}

test("database pool connection acquisition failure returns sanitized 503", async () => {
  const error = {
    code: "P2039",
    meta: { driverAdapterError: { message: "pool failed to retrieve a connection from pool (pool connections: active=0 idle=0 limit=5)" } },
  };
  const result = await errorResponse(error);
  assert.equal(result.status, 503);
  assert.deepEqual(result.body, { success: false, message: "Database temporarily unavailable" });
  assert.ok(!JSON.stringify(result.body).includes("limit=5"));
});

test("unrelated adapter errors cannot be disguised as a pool outage", async () => {
  const error = { code: "P2039", meta: { driverAdapterError: { message: "Different driver failure" } } };
  const prior = console.error;
  console.error = () => {};
  try {
    const result = await errorResponse(error);
    assert.equal(result.status, 500);
    assert.deepEqual(result.body, { success: false, message: "Unexpected server error" });
  } finally {
    console.error = prior;
  }
});

test("authorization failures retain their 404 response", async () => {
  const result = await errorResponse(new HttpError(404, "Resource not found"));
  assert.equal(result.status, 404);
  assert.equal(result.body.message, "Resource not found");
});
