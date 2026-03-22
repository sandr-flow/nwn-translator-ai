/**
 * API base: same origin in production (nginx), Vite proxy in dev.
 */
export function apiUrl(path) {
  if (path.startsWith("http")) return path;
  const p = path.startsWith("/") ? path : `/${path}`;
  return p;
}

export async function fetchJson(path, options = {}) {
  const res = await fetch(apiUrl(path), {
    ...options,
    headers: {
      Accept: "application/json",
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

export function downloadUrl(taskId, kind) {
  const base = apiUrl(`/api/tasks/${taskId}/${kind}`);
  return base;
}

export async function fetchTranslations(taskId) {
  return fetchJson(`/api/tasks/${taskId}/translations`);
}

export async function postRebuild(taskId, translations) {
  return fetchJson(`/api/tasks/${taskId}/rebuild`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ translations }),
  });
}
