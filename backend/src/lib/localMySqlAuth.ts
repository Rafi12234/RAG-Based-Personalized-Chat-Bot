/** Development-only MySQL SHA-2 authentication fallback.
 * Fetching an RSA key from an untrusted remote server risks impersonation.
 * Only permit this for the Docker database bound to localhost:3308.
 * Production must configure authenticated TLS or pin a trusted server key.
 */
export function allowLocalDevMySqlKeyRetrieval(
  environment: string,
  host: string,
  port: number,
): boolean {
  return environment === "development" && host === "127.0.0.1" && port === 3308;
}
