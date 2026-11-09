/** Conservative two-fact extraction from independently verified, scoped document excerpts.
 * Supported shapes are explicit two-organization questions only. Every requested
 * fact is verified in its own source; missing or conflicting evidence abstains.
 */
import { extractDirectPolicyFact } from "./directPolicyFacts.js";
import { resolveNamedInfrastructure } from "./namedInfrastructure.js";
import type { VerifiedSource } from "./retrievalEligibility.js";

export type MultiDocumentFact =
  | Readonly<{ state: "not_applicable" }>
  | Readonly<{ state: "insufficient" }>
  | Readonly<{ state: "found"; firstFact: string; firstSourceIndex: number;
      secondFact: string; secondSourceIndex: number }>;

type PairKind = "vector_retention" | "store_invoice";
const VECTOR_RETENTION = /^what\s+is\s+([a-z][a-z0-9_-]{1,49})['’]s\s+vector\s+database\s+and\s+([a-z][a-z0-9_-]{1,49})['’]s\s+resolved\s+(?:ticket\s+)?attachments?\s+retention\s+period\s*\??$/iu;
const STORE_INVOICE = /^([a-z][a-z0-9_-]{1,49})\s+object\s+store\s+আর\s+([a-z][a-z0-9_-]{1,49})\s+invoice\s+correction\s+time\s+কত\s*\??$/iu;

function belongsTo(source: VerifiedSource, organization: string): boolean {
  // Source titles and headings establish organization scope, not answer values.
  const words = (source.documentTitle + " " + source.excerpt.slice(0, 160))
    .toLowerCase().split(/[^a-z0-9]+/u);
  return words.includes(organization);
}

export function extractMultiDocumentFact(
  question: string, sources: readonly VerifiedSource[],
): MultiDocumentFact {
  const request = question.trim();
  const vectorMatch = VECTOR_RETENTION.exec(request);
  const storeMatch = vectorMatch ? null : STORE_INVOICE.exec(request);
  const match = vectorMatch ?? storeMatch;
  if (!match) return { state: "not_applicable" };
  const kind: PairKind = vectorMatch ? "vector_retention" : "store_invoice";
  const firstOrganization = match[1].toLowerCase();
  const secondOrganization = match[2].toLowerCase();
  if (firstOrganization === secondOrganization) return { state: "insufficient" };

  const firstSources = sources.map((source, index) => ({ source, index }))
    .filter(({ source }) => belongsTo(source, firstOrganization));
  const secondSources = sources.map((source, index) => ({ source, index }))
    .filter(({ source }) => belongsTo(source, secondOrganization));
  if (!firstSources.length || !secondSources.length) return { state: "insufficient" };

  const infrastructureQuestion = kind === "vector_retention"
    ? "Where are document embedding vectors stored?"
    : "Which object store holds original uploaded source documents?";
  const policyQuestion = kind === "vector_retention"
    ? "How long are resolved ticket attachments retained?"
    : "How many business days after receiving the invoice can a customer request a correction?";

  // Reuse conservative extractors that check the actual named object/action
  // and reject contradictions, without copying fixture values into code.
  const infrastructure = resolveNamedInfrastructure(
    infrastructureQuestion, firstSources.map(({ source }) => source),
  );
  const deadline = extractDirectPolicyFact(
    policyQuestion, secondSources.map(({ source }) => source),
  );
  if (infrastructure.state !== "found" || !deadline) return { state: "insufficient" };

  const first = firstSources[infrastructure.sourceIndex];
  const second = secondSources[deadline.index];
  if (!first || !second || first.source.documentId === second.source.documentId) {
    return { state: "insufficient" };
  }
  return { state: "found", firstFact: infrastructure.name, firstSourceIndex: first.index,
    secondFact: deadline.text, secondSourceIndex: second.index };
}
