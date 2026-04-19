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
} from "../api/client.js";
import { useI18n } from "./useI18n.js";

/** provide/inject key for translation UI state */
export const TranslationStateKey = Symbol("TranslationState");

const PHASE_KEYS = {
  extracting: "phase.extracting",
  scanning: "phase.scanning",
  extracting_content: "phase.extracting",
  translating: "phase.translating",
  translating_item: "phase.translating",
  injecting: "phase.injecting",
  building: "phase.building",
  pending: "phase.pending",
};

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
    reasoningEffort: "",
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
  });

  let eventSource = null;
  let sseRetryCount = 0;
  let sseRetryTimer = null;
  const SSE_MAX_RETRIES = 5;

  const phaseLabel = computed(() => PHASE_KEYS[t.phase] ? i(PHASE_KEYS[t.phase]) : t.phase ?? "");

  function reset() {
    closeSse();
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

  function closeSse() {
    if (sseRetryTimer) {
      clearTimeout(sseRetryTimer);
      sseRetryTimer = null;
    }
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    sseRetryCount = 0;
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
    try {
      const status = await fetchJson(`/api/tasks/${id}/status`);
      if (status.status === "completed") {
        t.status = "completed";
        t.progress = 1;
        t.resultFilename = status.result_filename ?? "";
        t.stats = status.stats ?? null;
        t.step = "done";
      } else if (status.status === "failed") {
        t.status = "failed";
        t.error = status.error ?? i("error.default");
        t.step = "done";
      } else if (status.status === "cancelled") {
        reset();
      }
    } catch {
      /* backend unreachable — nothing to do */
    }
  }

  function openSse(id) {
    closeSse();
    const url = `/api/tasks/${id}/progress`;
    eventSource = new EventSource(url);

    eventSource.onmessage = (ev) => {
      try {
        sseRetryCount = 0;
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot") {
          applySnapshot(msg);
          return;
        }
        if (msg.type === "progress") {
          if (msg.phase) t.phase = msg.phase;
          if (typeof msg.progress === "number") t.progress = msg.progress;
          if (msg.file != null) t.currentFile = msg.file;
          if (typeof msg.current === "number") t.currentIndex = msg.current;
          if (typeof msg.total === "number") t.totalFiles = msg.total;
          return;
        }
        if (msg.type === "status") {
          if (msg.status) t.status = msg.status;
          return;
        }
        if (msg.type === "completed") {
          t.status = "completed";
          t.progress = 1;
          t.resultFilename = msg.result_filename ?? "";
          t.stats = msg.stats ?? null;
          t.step = "done";
          closeSse();
          return;
        }
        if (msg.type === "failed") {
          t.status = "failed";
          t.error = msg.error ?? i("error.default");
          t.step = "done";
          closeSse();
          return;
        }
        if (msg.type === "cancelled") {
          t.status = "cancelled";
          closeSse();
          reset();
          return;
        }
        if (msg.type === "done") {
          closeSse();
          if (t.status !== "completed" && t.status !== "failed") {
            pollTaskStatus(id);
          }
        }
      } catch {
        /* ignore */
      }
    };

    eventSource.onerror = () => {
      if (eventSource) eventSource.close();
      eventSource = null;
      if (
        t.status !== "completed" &&
        t.status !== "failed" &&
        t.status !== "cancelled" &&
        sseRetryCount < SSE_MAX_RETRIES
      ) {
        const delay = Math.min(1000 * 2 ** sseRetryCount, 16000);
        sseRetryCount++;
        sseRetryTimer = setTimeout(async () => {
          try {
            const res = await fetch(`/api/tasks/${id}/status`);
            if (res.status === 404) {
              t.status = "failed";
              t.error = i("error.taskNotFound");
              t.step = "done";
              return;
            }
            if (res.ok) {
              const status = await res.json();
              if (status.status === "completed") {
                t.status = "completed";
                t.progress = 1;
                t.resultFilename = status.result_filename ?? "";
                t.stats = status.stats ?? null;
                t.step = "done";
                return;
              }
              if (status.status === "failed") {
                t.status = "failed";
                t.error = status.error ?? i("error.default");
                t.step = "done";
                return;
              }
            }
          } catch {
            /* backend not reachable — reconnect SSE */
          }
          openSse(id);
        }, delay);
      } else if (
        t.status !== "completed" &&
        t.status !== "failed" &&
        t.status !== "cancelled"
      ) {
        pollTaskStatus(id);
      }
    };
  }

  async function loadModels() {
    try {
      const data = await fetchModels();
      t.defaultModelSlug = data.default_model ?? "";
      t.defaultModels = data.models ?? [];
      if (!t.model && data.default_model) {
        t.model = data.default_model;
      }
    } catch {
      t.defaultModels = [];
    }
  }

  async function loadConfig(retries = 5, delayMs = 1000) {
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

  async function startTranslation() {
    if (!t.selectedFile) {
      throw new Error(i("error.noFile"));
    }
    if (!t.apiKey?.trim()) {
      throw new Error(i("error.noKey"));
    }

    t.error = "";
    t.step = "running";
    t.progress = 0;
    t.phase = "pending";
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

    const { task_id } = await postTranslate(fd);
    t.taskId = task_id;
    openSse(task_id);
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

  async function rebuildWithEdits(editedTranslations) {
    if (!t.taskId) return;
    t.rebuilding = true;
    try {
      const data = await postRebuild(t.taskId, editedTranslations, t.targetLang);
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
    if (!t.taskId || t.cancelling) return;
    t.cancelling = true;
    try {
      await postCancelTask(t.taskId);
    } catch (e) {
      t.cancelling = false;
      throw e;
    }
  }

  async function deleteHistoryTask(taskId) {
    await deleteTask(taskId);
    t.historyItems = t.historyItems.filter((x) => x.task_id !== taskId);
  }

  return {
    t,
    phaseLabel,
    reset,
    loadModels,
    loadConfig,
    startTranslation,
    testConnection,
    resultDownloadUrl,
    logDownloadUrl,
    closeSse,
    loadTranslations,
    enterEditor,
    rebuildWithEdits,
    openHistory,
    loadHistory,
    openHistoryTask,
    deleteHistoryTask,
    cancelTranslation,
  };
}
