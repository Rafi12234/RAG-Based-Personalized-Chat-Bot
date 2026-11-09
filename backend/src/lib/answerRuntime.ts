/** Public, non-sensitive answer protocol identifier.
 * Displayed by the authenticated frontend so an old Windows backend process
 * cannot be mistaken for newly pulled source code.
 * Increment whenever the answer pipeline's externally visible behavior changes.
 */
export const ANSWER_ENGINE_VERSION = "phase3.10-grounded-2026-11-10-r5" as const;

export type AnswerResolutionCode =
  | "no_verified_sources"
  | "explicit_evidence"
  | "named_infrastructure"
  | "named_fact_missing"
  | "named_fact_conflict"
  | "direct_policy"
  | "direct_policy_insufficient"
  | "duration_fact"
  | "duration_insufficient"
  | "model_path"
  | "multi_document_fact"
  | "multi_document_insufficient";
