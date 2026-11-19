export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function endpoint(origin, path) {
  const url = new URL(path, origin);
  if (url.origin !== origin || url.username || url.password) {
    throw new Error("The service returned an invalid URL.");
  }
  return url;
}

export function configuredOrigin(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash) {
    throw new Error("The game services are configured incorrectly.");
  }
  return url.origin;
}

export function api(origin, service) {
  return async (path, { method = "GET", body, token } = {}) => {
    const headers = { Accept: "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";

    let response;
    try {
      response = await fetch(endpoint(origin, path), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "omit",
        redirect: "error",
        cache: "no-store",
        signal: AbortSignal.timeout(5500),
      });
    } catch {
      throw new ApiError(`${service} is unavailable. Try again shortly.`, 0);
    }
    const data = await response.json().catch(() => null);
    if (response.ok && data !== null) return data;
    const validation = data?.detail?.find?.((item) => item.loc?.at(-1) === "nickname");
    throw new ApiError(
      data?.error?.message || (validation && "Enter a nickname from 1 to 24 characters.") ||
        `${service} could not complete the request. Try again.`,
      response.status,
      data?.error?.code,
    );
  };
}

export async function demoMedia(origin, path, signal) {
  const url = endpoint(origin, path);
  if (!url.pathname.startsWith("/static/demo/local/")) {
    throw new Error("The clip URL is outside the Demo catalog.");
  }
  const response = await fetch(url, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]),
    credentials: "omit",
    redirect: "error",
  });
  if (!response.ok) throw new Error("The clip could not load.");
  return response.arrayBuffer();
}

export function newRequestId() {
  return [...crypto.getRandomValues(new Uint8Array(16))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
