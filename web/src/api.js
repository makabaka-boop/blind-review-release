// Minimal fetch wrapper. Tokens live in localStorage; the API itself sends
// Cache-Control: no-store on every response so no authenticated payload is
// ever served from a shared/browser cache.

const TOKEN_KEY = "pr_token";
const META_KEY = "pr_meta";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getMeta() {
  try {
    return JSON.parse(localStorage.getItem(META_KEY) || "null");
  } catch {
    return null;
  }
}

export function setSession({ access_token, role, username, display_name }) {
  localStorage.setItem(TOKEN_KEY, access_token);
  localStorage.setItem(
    META_KEY,
    JSON.stringify({ role, username, display_name })
  );
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(META_KEY);
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, { method = "GET", body, form } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let payload;
  if (form) {
    payload = new URLSearchParams(form);
    headers["Content-Type"] = "application/x-www-form-urlencoded";
  } else if (body !== undefined) {
    payload = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }

  const resp = await fetch(`/api${path}`, { method, headers, body: payload });
  if (resp.status === 401) {
    clearSession();
  }
  let data = null;
  const text = await resp.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!resp.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? data.detail : data;
    throw new ApiError(resp.status, detail);
  }
  return data;
}
