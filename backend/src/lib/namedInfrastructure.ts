/**
 * Verified-source-only named infrastructure extraction.
 *
 * Narrow "which named infrastructure stores X" intent, with SAME-SENTENCE
 * evidence, whitespace normalization, and explicit conflict detection.
 * No model, DB access, hardcoded product names or fixture IDs.
 * Use only AFTER organization/knowledge-base/READY verification.
 */
import type { VerifiedSource } from "./retrievalEligibility.js";

export type InfrastructureKind = "vector_database" | "object_store";
export type NamedInfrastructureResult =
  | Readonly<{ state: "not_applicable" }>
  | Readonly<{ state: "missing" }>
  | Readonly<{ state: "conflict" }>
  | Readonly<{ state: "found"; sourceIndex: number; name: string }>;

export function infrastructureQuestionKind(question: string): InfrastructureKind | null {
  const asksName = /\b(?:which|what)\b|কোন/iu.test(question);
  if (!asksName) return null;
  const vector = /\bvector\s+database\b|ভেক্টর\s*ডাটাবেস/iu.test(question) &&
    /\bembeddings?\b|এমবেডিং|এম্বেডিং/iu.test(question);
  const store = /\bobject\s+store\b|\bstorage\b|স্টোরেজ/iu.test(question) &&
    /\b(?:upload(?:ed)?|original|source|document(?:s)?|file(?:s)?)\b|আপলোড|মূল|ফাইল/iu.test(question);
  if (vector === store) return null;
  return vector ? "vector_database" : "object_store";
}

const NAME = String.raw`([A-Za-z][A-Za-z0-9_-]{1,39})`;
const QUALIFIER = String.raw`(?:[A-Za-z0-9_-]+\s+){0,4}`;
const VECTOR_PATTERNS = [
  // "DocMind-AI stores document embeddings in Qdrant, a vector database."
  new RegExp(String.raw`\b(?:stores?|keeps?)\s+(?:document\s+|text\s+)?embeddings?\s+(?:in|on|using)\s+` +
    NAME + String.raw`\s*,\s*(?:an?\s+|the\s+)?vector\s+database\b`, "giu"),
  // "Qdrant is a vector database that stores document embeddings."
  new RegExp(String.raw`\b` + NAME +
    String.raw`\s+(?:is|was)\s+(?:an?\s+|the\s+)?` + QUALIFIER +
    String.raw`vector\s+database\s+(?:that|which)\s+(?:stores?|keeps?)\s+(?:document\s+|text\s+)?embeddings?\b`, "giu"),
];
const STORE_PATTERNS = [
  // "The document platform keeps original uploaded PDF, DOCX and TXT files
  //  in RustFS, an S3-compatible object store."
  // Keep the storage verb, file object, named target, and object-store identity
  // in the SAME sentence. Formats/modifiers are limited to ordinary file terms.
  new RegExp(String.raw`\b(?:keeps?|stores?|holds?|preserves?)\s+(?:(?:the|original|uploaded|source|customer|user|binary|raw|document|documents|pdf|docx|txt|file|files|and|or)\s*[,]?\s+){0,14}(?:documents?|files?)\s+(?:in|on|using)\s+` +
    NAME + String.raw`\s*,\s*(?:an?\s+|the\s+)?` + QUALIFIER + String.raw`object\s+store\b`, "giu"),
  // "RustFS is the S3-compatible object store that holds uploaded source documents."
  new RegExp(String.raw`\b` + NAME +
    String.raw`\s+(?:is|was)\s+(?:an?\s+|the\s+)?` + QUALIFIER +
    String.raw`object\s+store\b(?=[^.!?।]{0,150}\b(?:holds?|stores?|contains?)\s+(?:the\s+)?(?:uploaded\s+|original\s+|source\s+){0,3}(?:documents?|files?)\b)`, "giu"),
  // "Uploaded source documents are stored in RustFS, an S3-compatible object store."
  new RegExp(String.raw`\b(?:uploaded\s+|source\s+|original\s+){1,3}(?:documents?|files?)\s+(?:are\s+)?(?:stored|kept|held)\s+(?:in|on)\s+` +
    NAME + String.raw`\s*,\s*(?:an?\s+|the\s+)?` + QUALIFIER + String.raw`object\s+store\b`, "giu"),
];

/** Avoid false positive from negated claims. */
function isNegated(sentence: string, start: number): boolean {
  const prior = sentence.slice(Math.max(0, start - 80), start);
  return /\b(?:not|never|does\s+not|is\s+not|are\s+not)\b[^.!?।]{0,36}$/iu.test(prior);
}

function sourceSentences(source: VerifiedSource): string[] {
  // Source excerpt already bounded at retrieval, no additional I/O.
  // Literal escaped separators or hard-wrapped lines are normalized; we
  // split only at terminal punctuation, not at newlines within sentences.
  const normalized = source.excerpt.slice(0, 800)
    .replace(/\\r\\n|\\n|\\r/gu, " ")
    .replace(/\s+/gu, " ");
  return normalized.split(/[.!?।]+/u).map(s => s.trim()).filter(s => s.length >= 10);
}

export function resolveNamedInfrastructure(
  question: string,
  sources: readonly VerifiedSource[],
): NamedInfrastructureResult {
  const kind = infrastructureQuestionKind(question);
  if (!kind) return { state: "not_applicable" };
  const expressions = kind === "vector_database" ? VECTOR_PATTERNS : STORE_PATTERNS;
  const candidates: Array<{ sourceIndex: number; name: string }> = [];
  for (const [sourceIndex, source] of sources.entries()) {
    for (const sentence of sourceSentences(source)) {
      for (const expression of expressions) {
        for (const match of sentence.matchAll(expression)) {
          if (isNegated(sentence, match.index ?? 0)) continue;
          candidates.push({ sourceIndex, name: match[1] });
        }
      }
    }
  }
  if (!candidates.length) return { state: "missing" };
  if (new Set(candidates.map(item => item.name.toLowerCase())).size !== 1) {
    return { state: "conflict" };
  }
  return { state: "found", ...candidates[0] };
}
