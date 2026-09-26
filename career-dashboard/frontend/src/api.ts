/**
 * Every profile is its own app on the server, under /p/<id>/. A page opened at
 * /p/<id>/ only ever talks to that profile's API, so one browser tab can never
 * show or change another profile's jobs, resumes or chats.
 */
const PROFILE_MATCH =
  typeof location === "undefined" ? null : location.pathname.match(/^\/p\/([a-z0-9-]{1,40})(?:\/|$)/);
export const PROFILE_ID: string | null = PROFILE_MATCH ? PROFILE_MATCH[1] : null;
export const API_BASE = PROFILE_ID ? `/p/${PROFILE_ID}/api` : "/api";

async function request<T>(
  url: string,
  method: string,
  body: unknown,
  options: { timeout?: number; raw?: Blob },
): Promise<T> {
  // A poll that never answers (a dropped connection, a sleeping laptop) must fail,
  // not hang the loop that would retry it.
  const control = options.timeout ? new AbortController() : undefined;
  const timer = control ? window.setTimeout(() => control.abort(), options.timeout) : 0;
  let r: Response;
  try {
    r = await fetch(url, {
      method,
      headers: options.raw
        ? { "Content-Type": "application/octet-stream" }
        : body === undefined
          ? {}
          : { "Content-Type": "application/json" },
      body: options.raw ?? (body === undefined ? undefined : JSON.stringify(body)),
      signal: control?.signal,
    });
  } catch (e) {
    throw new Error(control?.signal.aborted ? "The app did not answer in time. Retrying…" : "Cannot reach the app: " + (e as Error).message);
  } finally {
    window.clearTimeout(timer);
  }
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || data),
    );
  return data;
}

/** This profile's own API. */
export function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
  options: { timeout?: number } = {},
): Promise<T> {
  return request<T>(API_BASE + path, method, body, options);
}

/** The shell: the list of profiles, creating, resetting and deleting them, and the intake. */
export function shellApi<T = any>(path: string, method = "GET", body?: unknown): Promise<T> {
  return request<T>("/api" + path, method, body, {});
}

/** Send one file as the request body (no multipart); `url` is a full path. */
export function uploadFile<T = any>(url: string, file: Blob, name: string): Promise<T> {
  const joiner = url.includes("?") ? "&" : "?";
  return request<T>(url + joiner + "name=" + encodeURIComponent(name), "POST", undefined, { raw: file });
}

export const safeUrl = (value: string) => {
  try {
    const u = new URL(value);
    return ["https:", "http:"].includes(u.protocol) &&
      !u.username &&
      !u.password
      ? u.href
      : "#";
  } catch {
    return "#";
  }
};
export const fileUrl = (path: string) =>
  API_BASE + "/files/" + path.split("/").map(encodeURIComponent).join("/");
