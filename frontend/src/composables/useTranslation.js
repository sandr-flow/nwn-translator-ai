import { reactive, computed } from "vue";
import {
  postTranslate,
  postTestConnection,
  fetchModels,
  fetchConfig,
  fetchTranslations,
  postRebuild,
  downloadUrl,
  fetchHistory,
  deleteTask,
  fetchJson,
  postCancelTask,
  getClientToken,
} from "../api/client.js";
import { useI18n } from "./useI18n.js";

/** provide/inject key for translation UI state */
export const TranslationStateKey = Symbol("TranslationState");

/** Statuses a task can no longer leave (mirrors TERMINAL_STATUSES in database.py). */
const TERMINAL_STATUSES = ["completed", "failed", "cancelled", "interrupted"];

/** localStorage key for task ids the user left without waiting for the worker. */
const ABANDONED_TASKS_KEY = "nwn_abandoned_tasks";

const PHASE_KEYS = {
  extracting: "phase.extracting",
  scanning: "phase.scanning",
  extracting_content: "phase.extracting",
  translating: "phase.translating",
  translating_item: "phase.translating",
  injecting: "phase.injecting",
  building: "phase.building",
  pending: "phase.pending",
  uploading: "phase.uploading",
};

function readAbandonedTaskIds() {
  try {
    const raw = localStorage.getItem(ABANDONED_TASKS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writeAbandonedTaskIds(ids) {
  localStorage.setItem(ABANDONED_TASKS_KEY, JSON.stringify(ids.slice(-20)));
}

function markTaskAbandoned(taskId) {
  if (!taskId) return;
  const ids = readAbandonedTaskIds();
  if (!ids.includes(taskId)) {
    ids.push(taskId);
    writeAbandonedTaskIds(ids);
  }
}

function isTaskAbandoned(taskId) {
  return Boolean(taskId) && readAbandonedTaskIds().includes(taskId);
}

function clearAbandonedTask(taskId) {
  if (!taskId) return;
  writeAbandonedTaskIds(readAbandonedTaskIds().filter((id) => id !== taskId));
}

export function useTranslation() {
  const { t: i } = useI18n();
  const t = reactive({
    step: "setup",
    selectedFile: null,
    apiKey: "",
    targetLang: "russian",
    sourceLang: "auto",
    model: "",
    preserveTokens: true,
    useContext: true,
    playerGender: "male",
    reasoningEffort: "none",
    taskId: "",
    status: "",
    cancelling: false,
    progress: 0,
    phase: "",
    currentFile: "",
    error: "",
    resultFilename: "",
    stats: null,
    defaultModels: [],
    defaultModelSlug: "",
    currentIndex: 0,
    totalFiles: 0,
    translationFiles: [],
    rebuilding: false,
    historyItems: [],
    /** Background job the user left; shown as a soft banner on setup. */
    backgroundTaskId: "",
    backgroundStatus: "",
  });

  let pollTimer = null;
  let uploadAbort = null;
  // Progress is state, not a stream of events: the client reads the task's
  // current state on a timer. One second oversamples the real rate of change
  // (translation batches return every few seconds) and, unlike a long-lived
  // streaming response, survives any intermediary that buffers it.
  const POLL_INTERVAL_MS = 1000;

  const phaseLabel = computed(() => PHASE_KEYS[t.phase] ? i(PHASE_KEYS[t.phase]) : t.phase ?? "");

  function abortUpload() {
    if (uploadAbort) {
      uploadAbort.abort();
      uploadAbort = null;
    }
  }

  function reset() {
    abortUpload();
    stopPolling();
    t.step = "setup";
    t.taskId = "";
    t.status = "";
    t.cancelling = false;
    t.progress = 0;
    t.phase = "";
    t.currentFile = "";
    t.error = "";
    t.resultFilename = "";
    t.stats = null;
    t.currentIndex = 0;
    t.totalFiles = 0;
  }

  function clearBackgroundBanner() {
    t.backgroundTaskId = "";
    t.backgroundStatus = "";
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function applySnapshot(data) {
    if (data.status) t.status = data.status;
    if (typeof data.progress === "number") t.progress = data.progress;
    if (data.phase) t.phase = data.phase;
    if (data.file != null) t.currentFile = data.file;
    if (typeof data.current === "number") t.currentIndex = data.current;
    if (typeof data.total === "number") t.totalFiles = data.total;
  }

  async function pollTaskStatus(id) {
    let status;
    try {
      status = await fetchJson(`/api/tasks/${id}/status`);
    } catch {
      return; // backend unreachable — the running timer will retry
    }
    if (status.status === "completed") {
      stopPolling();
      clearAbandonedTask(id);
      clearBackgroundBanner();
      t.status = "completed";
      t.progress = 1;
      t.resultFilename = status.result_filename ?? "";
      t.stats = status.stats ?? null;
      t.step = "done";
    } else if (status.status === "failed" || status.status === "interrupted") {
      stopPolling();
      clearAbandonedTask(id);
      clearBackgroundBanner();
      t.status = "failed";
      t.error = status.error ?? i("error.default");
      t.step = "done";
    } else if (status.status === "cancelled") {
      clearAbandonedTask(id);
      clearBackgroundBanner();
      reset();
    } else {
      applySnapshot({ ...status, file: status.current_file });
    }
  }

  function startPolling(id) {
    stopPolling();
    pollTimer = setInterval(() => pollTaskStatus(id), POLL_INTERVAL_MS);
    pollTaskStatus(id);
  }

  // A cold backend start takes 10-15s to import and bind, so both loaders
  // keep retrying long enough to cover it instead of leaving the UI empty.
  async function loadModels(retries = 30, delayMs = 1000) {
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const data = await fetchModels();
        t.defaultModelSlug = data.default_model ?? "";
        t.defaultModels = data.models ?? [];
        if (!t.model && data.default_model) {
          t.model = data.default_model;
        }
        return;
      } catch {
        t.defaultModels = [];
        if (attempt < retries) {
          await new Promise((r) => setTimeout(r, delayMs));
        }
      }
    }
  }

  async function loadConfig(retries = 30, delayMs = 1000) {
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const data = await fetchConfig();
        if (data.api_key && !t.apiKey) {
          t.apiKey = data.api_key;
        }
        if (data.default_model && !t.model) {
          t.model = data.default_model;
        }
        return;
      } catch {
        if (attempt < retries) {
          await new Promise((r) => setTimeout(r, delayMs));
        }
      }
    }
  }

  /**
   * Reattach to a job that is still running on the server.
   *
   * A translation lives in the worker thread, not in the tab, so a reload used
   * to drop the user on the setup screen while the job kept burning their API
   * key invisibly.
   *
   * Tasks the user explicitly left (or that are already ``cancelling``) are
   * not auto-resumed — that would trap them on the progress screen again.
   */
  async function resumeActiveTask() {
    let items;
    try {
      const data = await fetchHistory();
      items = data.items ?? [];
    } catch {
      return; // no history (or backend not up yet) — nothing to resume
    }
    const active = items.find((x) => !TERMINAL_STATUSES.includes(x.status));
    if (!active) return;
    if (isTaskAbandoned(active.task_id) || active.status === "cancelling") {
      t.backgroundTaskId = active.task_id;
      t.backgroundStatus = active.status === "cancelling" ? "cancelling" : active.status;
      return;
    }
    try {
      const status = await fetchJson(`/api/tasks/${active.task_id}/status`);
      if (TERMINAL_STATUSES.includes(status.status)) return;
      if (status.status === "cancelling" || isTaskAbandoned(active.task_id)) {
        t.backgroundTaskId = active.task_id;
        t.backgroundStatus = status.status === "cancelling" ? "cancelling" : status.status;
        return;
      }
      clearAbandonedTask(active.task_id);
      clearBackgroundBanner();
      t.taskId = active.task_id;
      if (status.target_lang) t.targetLang = status.target_lang;
      applySnapshot({ ...status, file: status.current_file });
      t.step = "running";
      startPolling(active.task_id);
    } catch {
      // The row is unfinished but the worker is gone (process restarted before
      // it could be reconciled) — leave the user on the setup screen.
    }
  }

  /**
   * Leave the progress screen immediately without waiting for the worker.
   *
   * Marks the task abandoned so a refresh does not auto-resume onto progress,
   * signals cancel in the background, and returns to setup.
   */
  function leaveProgressScreen({ requestCancel = true } = {}) {
    const id = t.taskId;
    if (id) {
      markTaskAbandoned(id);
      t.backgroundTaskId = id;
      t.backgroundStatus = t.cancelling || requestCancel ? "cancelling" : t.status || "translating";
      if (requestCancel) {
        postCancelTask(id).catch(() => {});
      }
    }
    reset();
  }

  async function reopenBackgroundTask() {
    const id = t.backgroundTaskId;
    if (!id) return;
    try {
      const status = await fetchJson(`/api/tasks/${id}/status`);
      if (TERMINAL_STATUSES.includes(status.status)) {
        clearAbandonedTask(id);
        clearBackgroundBanner();
        if (status.status === "completed") {
          t.taskId = id;
          t.status = "completed";
          t.resultFilename = status.result_filename ?? "";
          t.stats = status.stats ?? null;
          if (status.target_lang) t.targetLang = status.target_lang;
          t.step = "done";
        }
        return;
      }
      // User explicitly asked to reopen — clear abandon so polling stays attached.
      clearAbandonedTask(id);
      clearBackgroundBanner();
      t.taskId = id;
      if (status.target_lang) t.targetLang = status.target_lang;
      applySnapshot({ ...status, file: status.current_file });
      t.cancelling = status.status === "cancelling";
      t.step = "running";
      startPolling(id);
    } catch (e) {
      clearBackgroundBanner();
      t.error = String(e.message ?? e);
    }
  }

  async function dismissBackgroundBanner() {
    const id = t.backgroundTaskId;
    if (id) {
      markTaskAbandoned(id);
      postCancelTask(id).catch(() => {});
    }
    clearBackgroundBanner();
  }

  async function startTranslation() {
    if (!t.selectedFile) {
      throw new Error(i("error.noFile"));
    }
    if (!t.apiKey?.trim()) {
      throw new Error(i("error.noKey"));
    }

    clearBackgroundBanner();
    t.error = "";
    t.step = "running";
    t.progress = 0;
    t.phase = "uploading";
    t.status = "pending";

    const fd = new FormData();
    fd.append("file", t.selectedFile);
    fd.append("api_key", t.apiKey.trim());
    fd.append("target_lang", t.targetLang);
    fd.append("source_lang", t.sourceLang || "auto");
    const modelSlug = typeof t.model === "string" ? t.model.trim() : "";
    if (modelSlug) {
      fd.append("model", modelSlug);
    }
    fd.append("preserve_tokens", t.preserveTokens ? "true" : "false");
    fd.append("use_context", t.useContext ? "true" : "false");
    fd.append("player_gender", t.playerGender);
    const reff = typeof t.reasoningEffort === "string" ? t.reasoningEffort.trim() : "";
    if (reff) {
      fd.append("reasoning_effort", reff);
    }

    const ac = new AbortController();
    uploadAbort = ac;
    let task_id;
    try {
      ({ task_id } = await postTranslate(fd, {
        signal: ac.signal,
        onProgress(loaded, total) {
          if (total > 0) t.progress = loaded / total;
        },
      }));
    } catch (e) {
      uploadAbort = null;
      if (e && e.name === "AbortError") return;
      throw e;
    }
    uploadAbort = null;
    t.phase = "pending";
    clearAbandonedTask(task_id);
    t.taskId = task_id;
    startPolling(task_id);
  }

  async function testConnection() {
    if (!t.apiKey?.trim()) {
      throw new Error(i("error.noKeyShort"));
    }
    const modelSlug = typeof t.model === "string" ? t.model.trim() : "";
    const reff = typeof t.reasoningEffort === "string" ? t.reasoningEffort.trim() : "";
    const body = {
      api_key: t.apiKey.trim(),
      target_lang: t.targetLang,
    };
    if (modelSlug) body.model = modelSlug;
    if (reff) body.reasoning_effort = reff;
    return postTestConnection(body);
  }

  function resultDownloadUrl() {
    if (!t.taskId) return "";
    return downloadUrl(t.taskId, "download");
  }

  function logDownloadUrl() {
    if (!t.taskId) return "";
    return downloadUrl(t.taskId, "log");
  }

  async function loadTranslations() {
    if (!t.taskId) return;
    const data = await fetchTranslations(t.taskId);
    t.translationFiles = data.files ?? [];
  }

  function enterEditor() {
    t.step = "editing";
  }

  async function rebuildWithEdits(edits) {
    if (!t.taskId) return;
    t.rebuilding = true;
    try {
      const data = await postRebuild(t.taskId, edits, t.targetLang);
      t.resultFilename = data.result_filename ?? t.resultFilename;
      t.step = "done";
    } finally {
      t.rebuilding = false;
    }
  }

  function openHistory() {
    t.step = "history";
  }

  async function loadHistory() {
    try {
      const data = await fetchHistory();
      t.historyItems = data.items ?? [];
    } catch {
      t.historyItems = [];
    }
  }

  async function openHistoryTask(taskId) {
    // Load task status and set up state as if it just completed
    try {
      const status = await fetchJson(`/api/tasks/${taskId}/status`);
      t.taskId = taskId;
      t.status = status.status;
      t.resultFilename = status.result_filename ?? "";
      t.stats = status.stats ?? null;
      t.error = status.error ?? "";
      if (status.target_lang) {
        t.targetLang = status.target_lang;
      }
      t.step = "done";
    } catch (e) {
      t.error = String(e.message ?? e);
    }
  }

  async function cancelTranslation() {
    if (t.cancelling) return;
    if (!t.taskId) {
      reset();
      return;
    }
    t.cancelling = true;
    try {
      await postCancelTask(t.taskId);
    } catch {
      // Still leave the progress screen — a hung cancel must not trap the user.
    }
    leaveProgressScreen({ requestCancel: false });
  }

  async function deleteHistoryTask(taskId) {
    await deleteTask(taskId);
    t.historyItems = t.historyItems.filter((x) => x.task_id !== taskId);
    if (t.backgroundTaskId === taskId) clearBackgroundBanner();
    clearAbandonedTask(taskId);
  }

  return {
    t,
    phaseLabel,
    reset,
    loadModels,
    loadConfig,
    resumeActiveTask,
    startTranslation,
    testConnection,
    resultDownloadUrl,
    logDownloadUrl,
    stopPolling,
    loadTranslations,
    enterEditor,
    rebuildWithEdits,
    openHistory,
    loadHistory,
    openHistoryTask,
    deleteHistoryTask,
    cancelTranslation,
    leaveProgressScreen,
    reopenBackgroundTask,
    dismissBackgroundBanner,
  };
}
