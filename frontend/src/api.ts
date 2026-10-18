const API_BASE = (import.meta.env.VITE_API_URL || "http://localhost:4000").replace(/\/$/, "");

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const isMultipart = options.body instanceof FormData;
  const headers: Record<string, string> = { "X-DocMind-Request": "1" };
  if (options.body && !isMultipart) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API_BASE}/api/v1${path}`, {
    ...options,
    credentials: "include",
    headers: { ...headers, ...options.headers },
  });
  let data: unknown;
  try { data = await response.json(); }
  catch { throw new Error(`Unexpected server response (HTTP ${response.status})`); }
  if (!response.ok) {
    const message = data && typeof data === "object" && "message" in data &&
      typeof data.message === "string" ? data.message : `Request failed (HTTP ${response.status})`;
    throw new Error(message);
  }
  return data as T;
}

export function jsonBody(body: object): Pick<RequestInit, "body"> {
  return { body: JSON.stringify(body) };
}
