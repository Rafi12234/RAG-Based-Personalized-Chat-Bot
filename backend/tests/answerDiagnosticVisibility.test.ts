/** Development-only diagnostic visibility: no private model text in API errors. */
import { test } from "node:test";
import assert from "node:assert/strict";
import express from "express";
import { once } from "node:events";
import type { AddressInfo } from "node:net";
import { HttpError, errorHandler } from "../src/lib/http.js";

async function responseFor(error: HttpError) {
  const app = express();
  app.get("/probe", (_req, _res, next) => next(error));
  app.use(errorHandler);
  const server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  try {
    const r = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}/probe`);
    return { status: r.status, reason: r.headers.get("x-docmind-diagnostic-code"),
      expose: r.headers.get("access-control-expose-headers"),
      body: await r.json() };
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close(err => err ? reject(err) : resolve()));
  }
}

test("development opt-in exposes only a safe diagnostic header, not raw text", async () => {
  const prevEnv = process.env.NODE_ENV, prevDiag = process.env.DOCMIND_ANSWER_DIAGNOSTICS;
  try {
    process.env.NODE_ENV = "development";
    process.env.DOCMIND_ANSWER_DIAGNOSTICS = "1";
    const result = await responseFor(new HttpError(503, "Answer service unavailable", "answer_claim_mismatch"));
    assert.equal(result.status, 503);
    assert.equal(result.reason, "answer_claim_mismatch");
    assert.equal(result.expose, "X-DocMind-Diagnostic-Code");
    assert.deepEqual(result.body, { success: false, message: "Answer service unavailable" });
  } finally {
    if (prevEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = prevEnv;
    if (prevDiag === undefined) delete process.env.DOCMIND_ANSWER_DIAGNOSTICS; else process.env.DOCMIND_ANSWER_DIAGNOSTICS = prevDiag;
  }
});

test("production never exposes answer diagnostic reason even with opt-in set", async () => {
  const prevEnv = process.env.NODE_ENV, prevDiag = process.env.DOCMIND_ANSWER_DIAGNOSTICS;
  try {
    process.env.NODE_ENV = "production";
    process.env.DOCMIND_ANSWER_DIAGNOSTICS = "1";
    const result = await responseFor(new HttpError(503, "Answer service unavailable", "unsupported_evidence_quote"));
    assert.equal(result.reason, null);
    assert.equal(result.expose, null);
    assert.deepEqual(result.body, { success: false, message: "Answer service unavailable" });
  } finally {
    if (prevEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = prevEnv;
    if (prevDiag === undefined) delete process.env.DOCMIND_ANSWER_DIAGNOSTICS; else process.env.DOCMIND_ANSWER_DIAGNOSTICS = prevDiag;
  }
});

test("diagnostic headers reject arbitrary strings, including newlines", async () => {
  const prevEnv = process.env.NODE_ENV, prevDiag = process.env.DOCMIND_ANSWER_DIAGNOSTICS;
  try {
    process.env.NODE_ENV = "development";
    process.env.DOCMIND_ANSWER_DIAGNOSTICS = "1";
    const result = await responseFor(new HttpError(503, "Answer service unavailable", "bad\nprivate-output"));
    assert.equal(result.reason, null);
    assert.equal(result.expose, null);
  } finally {
    if (prevEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = prevEnv;
    if (prevDiag === undefined) delete process.env.DOCMIND_ANSWER_DIAGNOSTICS; else process.env.DOCMIND_ANSWER_DIAGNOSTICS = prevDiag;
  }
});

test("non-answer HTTP errors cannot leak diagnostic headers", async () => {
  const prevEnv = process.env.NODE_ENV, prevDiag = process.env.DOCMIND_ANSWER_DIAGNOSTICS;
  try {
    process.env.NODE_ENV = "development";
    process.env.DOCMIND_ANSWER_DIAGNOSTICS = "1";
    const result = await responseFor(new HttpError(404, "Resource not found", "some_internal_state"));
    assert.equal(result.status, 404);
    assert.equal(result.reason, null);
    assert.equal(result.expose, null);
  } finally {
    if (prevEnv === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = prevEnv;
    if (prevDiag === undefined) delete process.env.DOCMIND_ANSWER_DIAGNOSTICS; else process.env.DOCMIND_ANSWER_DIAGNOSTICS = prevDiag;
  }
});
