/**
 * Phase 3.1: validated public retrieval request.
 * Do not accept organizationId or client-supplied document identity here.
 * Tokenizer-specific token limits are enforced again by the internal E5 service.
 */
import { z } from "zod";
import { parseInput } from "./validate.js";

export const RETRIEVAL_MAX_QUESTION_CHARS = 1000;
export const RETRIEVAL_MAX_TOP_K = 10;

export const retrievalRequestSchema = z.strictObject({
  question: z.string()
    .trim()
    .min(1)
    .max(RETRIEVAL_MAX_QUESTION_CHARS)
    // Exclude non-printing controls; preserve ordinary newlines and tabs.
    .refine(value => !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(value)),
  topK: z.number().int().min(1).max(RETRIEVAL_MAX_TOP_K).default(5),
});

export type RetrievalRequest = z.infer<typeof retrievalRequestSchema>;

export function parseRetrievalRequest(data: unknown): RetrievalRequest {
  return parseInput(retrievalRequestSchema, data);
}
