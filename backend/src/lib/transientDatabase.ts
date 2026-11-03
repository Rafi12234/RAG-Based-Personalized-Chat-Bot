/** Transient Prisma/MariaDB connection-pool errors.
 * Only idempotent database reads may be retried. Never retry writes, token
 * issuance, ingestion publication, authorization decisions or transactions.
 */
type RecordValue = Record<string, unknown>;
const record = (value: unknown): value is RecordValue =>
  value !== null && typeof value === "object";

export function isPrismaPoolUnavailable(error: unknown): boolean {
  if (!record(error) || error.code !== "P2039" || !record(error.meta)) return false;
  const driver = error.meta.driverAdapterError;
  return record(driver) && typeof driver.message === "string" &&
    driver.message.includes("pool failed to retrieve a connection from pool");
}

/** Only a small allowlist of labels is logged; never emit arbitrary database
 * exception messages because they may contain credentials or internal paths.
 */
export function poolFailureCauseCategory(error: unknown): string {
  if (!isPrismaPoolUnavailable(error)) return "not_pool_failure";
  const meta = (error as RecordValue).meta as RecordValue;
  const driver = meta.driverAdapterError;
  const snippets: string[] = [];
  const seen = new Set<unknown>();
  const visit = (value: unknown, depth: number): void => {
    if (depth > 5 || seen.has(value)) return;
    if (typeof value === "string") {
      snippets.push(value.slice(0, 2000));
      return;
    }
    if (!record(value)) return;
    seen.add(value);
    for (const key of ["code", "message", "errno", "cause", "originalCode", "originalMessage", "sqlMessage"]) {
      if (key in value) visit(value[key], depth + 1);
    }
  };
  visit(driver, 0);
  const reason = snippets.join(" ");
  if (/rsa public key is not available|public key retrieval/iu.test(reason)) return "rsa_key_unavailable";
  if (/too many connections|\b1040\b/u.test(reason)) return "too_many_connections";
  if (/ECONNREFUSED|connection refused/iu.test(reason)) return "connection_refused";
  if (/ETIMEDOUT|socket timeout|connection timeout|failed to create socket/iu.test(reason)) return "socket_timeout";
  if (/access denied|\b1045\b/iu.test(reason)) return "access_denied";
  return "cause_unknown";
}

/** No retry of unrelated database errors; wait a short bounded interval
 * before a fresh, re-authorized SELECT query.
 */
export async function retryTransientPoolRead<T>(
  query: () => Promise<T>,
  pause: () => Promise<void> = () => new Promise(resolve => setTimeout(resolve, 250)),
): Promise<T> {
  try {
    return await query();
  } catch (error) {
    if (!isPrismaPoolUnavailable(error)) throw error;
    if (process.env.DOCMIND_ANSWER_DIAGNOSTICS === "1") {
      console.error("DocMind database diagnostic: pool_read_retry_once; cause=" +
        poolFailureCauseCategory(error));
    }
    await pause();
    return query();
  }
}
