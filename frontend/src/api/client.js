/**
 * API base: same origin in production (nginx), Vite proxy in dev.
 */
export function apiUrl(path) {
  if (path.startsWith("http")) return path;
  const p = path.startsWith("/") ? path : `/${path}`;
  return p;
}

/**
 * UUID v4. crypto.randomUUID is only exposed in secure contexts (HTTPS or
 * localhost); self-hosted UIs opened over plain HTTP from a LAN address fall
 * back to building one from crypto.getRandomValues, which has no such limit.
 */
export function uuid4() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * Get or create a persistent anonymous client token (UUID v4 in localStorage).
 */
export function getClientToken() {
  const key = "nwn_client_token";
  let token = localStorage.getItem(key);
  if (!token) {
    token = uuid4();
    localStorage.setItem(key, token);
  }
  return token;
}

export async function fetchJson(path, options = {}) {
  const res = await fetch(apiUrl(path), {
    ...options,
    headers: {
      Accept: "application/json",
      "X-Client-Token": getClientToken(),
      ...options.headers,
    },
  });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!res.ok) {
    const msg = data?.detail ?? data?.message ?? res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export async function postTranslate(formData) {
  const res = await fetch(apiUrl("/api/translate"), {
    method: "POST",
    headers: {
      "X-Client-Token": getClientToken(),
    },
    body: formData,
  });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!res.ok) {
    const msg = data?.detail ?? res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export async function postTestConnection(body) {
  return fetchJson("/api/test-connection", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchModels() {
  return fetchJson("/api/models");
}

export async function fetchConfig() {
  return fetchJson("/api/config");
}

export function downloadUrl(taskId, kind) {
  // Plain <a href> navigation cannot send the X-Client-Token header, so the
  // token rides in the query string (the backend accepts it as a fallback).
  const token = encodeURIComponent(getClientToken());
  return apiUrl(`/api/tasks/${taskId}/${kind}?client_token=${token}`);
}

export async function fetchTranslations(taskId) {
  return fetchJson(`/api/tasks/${taskId}/translations`);
}

export async function postRebuild(taskId, edits, targetLang) {
  const payload = { edits };
  if (targetLang != null && String(targetLang).trim()) {
    payload.target_lang = String(targetLang).trim();
  }
  return fetchJson(`/api/tasks/${taskId}/rebuild`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function fetchHistory() {
  return fetchJson("/api/history");
}

export async function deleteTask(taskId) {
  return fetchJson(`/api/tasks/${taskId}`, { method: "DELETE" });
}

export async function postCancelTask(taskId) {
  return fetchJson(`/api/tasks/${taskId}/cancel`, { method: "POST" });
}
