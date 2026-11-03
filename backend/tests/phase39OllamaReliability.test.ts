/** Phase 3.9: fully offline Ollama transport and error sanitization tests. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { callLocalOllama, buildEvidencePrompt } from "../src/services/groundedAnswer.js";
import { HttpError } from "../src/lib/http.js";

async function withFakeOllama(
  transport: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const prevFetch = globalThis.fetch;
  const prevModel = process.env.DOCMIND_OLLAMA_MODEL;
  const prevUrl = process.env.DOCMIND_OLLAMA_URL;
  try {
    process.env.DOCMIND_OLLAMA_MODEL = "qwen2.5:1.5b";
    process.env.DOCMIND_OLLAMA_URL = "http://127.0.0.1:11434/api/generate";
    globalThis.fetch = transport;
    await run();
  } finally {
    globalThis.fetch = prevFetch;
    if (prevModel === undefined) delete process.env.DOCMIND_OLLAMA_MODEL;
    else process.env.DOCMIND_OLLAMA_MODEL = prevModel;
    if (prevUrl === undefined) delete process.env.DOCMIND_OLLAMA_URL;
    else process.env.DOCMIND_OLLAMA_URL = prevUrl;
  }
}
async function assertReason(reason: string): Promise<void> {
  await assert.rejects(
    callLocalOllama("Synthetic offline test"),
    (error: unknown) => error instanceof HttpError &&
      error.status === 503 && error.message === "Answer service unavailable" &&
      error.diagnosticCode === reason,
  );
}

test("phase3.9 model: bounded structured request includes context and keep-alive", async () => {
  await withFakeOllama(async (_url, init) => {
    const request = JSON.parse(String(init?.body));
    assert.equal(request.model, "qwen2.5:1.5b");
    assert.equal(request.stream, false);
    assert.equal(request.keep_alive, "10m");
    assert.equal(request.options.num_ctx, 4096);
    assert.equal(request.options.num_predict, 256);
    assert.equal(request.format.additionalProperties, false);
    assert.ok(init?.signal instanceof AbortSignal);
    return new Response(JSON.stringify({done: true, response: '{"abstain":true,"answer":"","source_id":"","evidence_quote":""}'}),{status:200});
  }, async () => {
    assert.match(await callLocalOllama("Synthetic"), /"abstain":true/u);
  });
});

test("phase3.9 model: HTTP 503 is distinct from invalid model attribution", async () => {
  await withFakeOllama(async () => new Response("Service unavailable",{status:503}), async () => {
    await assertReason("ollama_http_5xx");
  });
});

test("phase3.9 model: missing model HTTP 404 is categorized without response content", async () => {
  await withFakeOllama(async () => new Response("private server detail",{status:404}), async () => {
    await assertReason("ollama_http_4xx");
  });
});

test("phase3.9 model: timeout is categorized without raw exception detail", async () => {
  await withFakeOllama(async () => { const error = new Error("private network path"); error.name = "TimeoutError"; throw error; }, async () => {
    await assertReason("ollama_timeout");
  });
});

test("phase3.9 model: network failure is sanitized", async () => {
  await withFakeOllama(async () => { throw new Error("ECONNREFUSED private-host:11434"); }, async () => {
    await assertReason("ollama_network_error");
  });
});

test("phase3.9 model: invalid JSON envelope is distinct", async () => {
  await withFakeOllama(async () => new Response("not-json",{status:200}), async () => {
    await assertReason("ollama_invalid_json");
  });
});

test("phase3.9 model: missing done:true is distinct", async () => {
  await withFakeOllama(async () => new Response(JSON.stringify({response:"{}",done:false}),{status:200}), async () => {
    await assertReason("ollama_invalid_envelope");
  });
});

test("phase3.9 model: oversized model response fails safely", async () => {
  await withFakeOllama(async () => new Response("x".repeat(70_000),{status:200}), async () => {
    await assertReason("ollama_response_oversize");
  });
});

test("phase3.9 model: prompt does not repeat entire excerpt alongside identical quote", () => {
  const excerpt = "This source contains a deliberately unusual one-off synthetic marker XYZWZZ along with facts.";
  const source = {
    chunkId:"123e4567-e89b-42d3-a456-426614174000",
    documentId:"test-id",documentVersionId:"test-ver",
    documentTitle:"Synthetic.txt",pageNumber:null,chunkIndex:0,score:0.9,excerpt,
  };
  const prompt = buildEvidencePrompt("What is in the source?", [source]);
  const json = JSON.parse(prompt.split("EVIDENCE JSON:")[1].trim()) as Array<{
    source: string; evidence_choices: Array<{quote:string}>; excerpt?: string;
  }>;
  assert.equal(json.length, 1);
  assert.equal(json[0].source, "S1");
  assert.equal(json[0].evidence_choices[0].quote, excerpt);
  assert.equal(json[0].excerpt, undefined);
  assert.equal(prompt.split("XYZWZZ").length - 1, 1);
});
