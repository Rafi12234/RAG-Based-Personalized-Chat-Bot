/** Database-driver pool policy: local Windows-to-Docker development only.
 * A healthy initial connect followed by intermittent socket connect timeouts
 * indicates connection creation may exceed the default local deadline.
 *
 * Do NOT increase pool size or change production behavior as a workaround.
 * Timeouts are bounded. This does not cure an unreachable MySQL server.
 */
export type DatabasePoolTimeouts = Readonly<{
  connectTimeout: number;
  acquireTimeout: number;
  initializationTimeout: number;
  idleTimeout: number;
}>;

export function databasePoolTimeouts(
  environment: string, host: string, port: number,
): DatabasePoolTimeouts {
  if (environment === "development" && host === "127.0.0.1" && port === 3308) {
    return {
      connectTimeout: 30_000,
      acquireTimeout: 45_000,
      initializationTimeout: 40_000,
      idleTimeout: 300,
    };
  }
  return {
    connectTimeout: 10_000,
    acquireTimeout: 20_000,
    initializationTimeout: 19_000,
    idleTimeout: 300,
  };
}
