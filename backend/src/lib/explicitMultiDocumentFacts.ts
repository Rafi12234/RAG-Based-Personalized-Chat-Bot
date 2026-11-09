/** Conservative two-fact extraction from independently verified document excerpts.
 * This applies only to a narrow two-organization question; missing and
 * conflicting values fail closed with no citation.
 */
import { extractDirectPolicyFact } from "./directPolicyFacts.js";
import { resolveNamedInfrastructure } from "./namedInfrastructure.js";
import type { VerifiedSource } from "./retrievalEligibility.js";

export type MultiDocumentFact =
  | Readonly<{ state: "not_applicable" }>
  | Readonly<{ state: "insufficient" }>
  | Readonly<{ state: "found"; vectorName: string; vectorSourceIndex: number;
      retentionText: string; retentionSourceIndex: number }>;

const QUESTION = /^what\s+is\s+([a-z][a-z0-9_-]{1,49})['’]s\s+vector\s+database\s+and\s+([a-z][a-z0-9_-]{1,49})['’]s\s+resolved\s+(?:ticket\s+)?attachments?\s+retention\s+period\s*\??$/iu;

function belongsTo(source: VerifiedSource, organization: string): boolean {
  // An authorized DB document title or excerpt heading can scope facts,
  // but a title alone is NEVER treated as evidence for an answer.
  const words = (source.documentTitle + " " + source.excerpt.slice(0, 160))
    .toLowerCase().split(/[^a-z0-9]+/u);
  return words.includes(organization);
}

export function extractMultiDocumentFact(
  question: string, sources: readonly VerifiedSource[],
): MultiDocumentFact {
  const match = QUESTION.exec(question.trim());
  if (!match) return { state: "not_applicable" };
  const vectorOrganization = match[1].toLowerCase();
  const retentionOrganization = match[2].toLowerCase();
  if (vectorOrganization === retentionOrganization) return { state: "insufficient" };

  const vectors = sources.map((source, index) => ({ source, index }))
    .filter(({ source }) => belongsTo(source, vectorOrganization));
  const retentionSources = sources.map((source, index) => ({ source, index }))
    .filter(({ source }) => belongsTo(source, retentionOrganization));
  if (!vectors.length || !retentionSources.length) return { state: "insufficient" };

  // Both values are extracted from bounded, verified source sentences;
  // neither the organization name nor the document title provides a value.
  const storage = resolveNamedInfrastructure(
    "Where are document embedding vectors stored?",
    vectors.map(v => v.source),
  );
  const retention = extractDirectPolicyFact(
    "How long are resolved ticket attachments retained?",
    retentionSources.map(v => v.source),
  );
  if (storage.state !== "found" || !retention) return { state: "insufficient" };

  const first = vectors[storage.sourceIndex];
  const second = retentionSources[retention.index];
  if (!first || !second || first.source.documentId === second.source.documentId) {
    return { state: "insufficient" };
  }
  return { state: "found", vectorName: storage.name, vectorSourceIndex: first.index,
    retentionText: retention.text, retentionSourceIndex: second.index };
}
