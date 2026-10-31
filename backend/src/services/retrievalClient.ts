/** Authenticated, bounded Node -> loopback Python retrieval channel. Never a browser API. */
import { createHmac, randomBytes } from "node:crypto";
import { z } from "zod";
import { HttpError } from "../lib/http.js";
import type { RetrievedPoint, TrustedRetrievalScope } from "../lib/retrievalEligibility.js";

const internalResponse = z.strictObject({
  points: z.array(z.object({ id: z.unknown(), score: z.unknown(), payload: z.unknown() })).max(40),
});

export async function fetchInternalPoints(
  scope: TrustedRetrievalScope,
  question: string,
  topK: number,
): Promise<RetrievedPoint[]> {
  // All values come from validated API input / Prisma, never directly from caller-supplied scope.
  const urlString = process.env.DOCMIND_INTERNAL_RETRIEVAL_URL ?? "http://127.0.0.1:8000/internal/v1/retrieve";
  const secret = process.env.DOCMIND_INTERNAL_RETRIEVAL_SECRET;
  if (!secret || secret.length < 32) throw new HttpError(503, "Retrieval service unavailable");
  let url: URL;
  try { url = new URL(urlString); }
  catch { throw new HttpError(503, "Retrieval service unavailable"); }
  // Phase 3.1 Windows local pilot: do not permit SSRF or remote plaintext secrets.
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ||
      url.pathname !== "/internal/v1/retrieve" || url.username || url.password || url.search || url.hash) {
    throw new HttpError(503, "Retrieval service unavailable");
  }
  const body = JSON.stringify({ organizationId: scope.organizationId, knowledgeBaseId: scope.knowledgeBaseId, question, topK });
  const timestamp = String(Date.now());
  const nonce = randomBytes(16).toString("hex");
  const signature = createHmac("sha256", secret).update(`${timestamp}.${nonce}.${body}`, "utf8").digest("hex");
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-docmind-internal-timestamp": timestamp,
        "x-docmind-internal-nonce": nonce,
        "x-docmind-internal-signature": signature,
      },
      body,
      signal: AbortSignal.timeout(20_000),
      redirect: "error",
    });
    if (!response.ok) throw new Error("Retrieval service returned unsuccessful status");
    const reader = response.body?.getReader();
    if (!reader) throw new Error("Retrieval service returned no response body");
    const chunks: Uint8Array[] = [];
    let totalBytes = 0;
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      totalBytes += next.value.byteLength;
      if (totalBytes > 512 * 1024) {
        await reader.cancel();
        throw new Error("Retrieval service response exceeded safe limit");
      }
      chunks.push(next.value);
    }
    const data = internalResponse.safeParse(JSON.parse(Buffer.concat(chunks).toString("utf8")));
    if (!data.success) throw new Error("Retrieval service returned invalid data");
    // Materialize explicit required properties; z.unknown() also admits undefined.
    return data.data.points.map(point => ({ id: point.id, score: point.score, payload: point.payload }));
  } catch {
    // No internal URLs, credentials, questions, source text or exception messages in API output.
    throw new HttpError(503, "Retrieval service unavailable");
  }
}
