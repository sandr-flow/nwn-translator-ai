import { reactive, computed } from "vue";
import {
  postTranslate,
  postTestConnection,
  fetchModels,
  downloadUrl,
} from "../api/client.js";

/** provide/inject key for translation UI state */
export const TranslationStateKey = Symbol("TranslationState");

const PHASE_LABELS = {
  extracting: "Распаковка модуля",
  translating: "Перевод ресурсов",
  building: "Сборка .mod",
  pending: "Ожидание",
};

export function useTranslation() {
  const t = reactive({
    step: "setup",
    selectedFile: null,
    apiKey: "",
    targetLang: "russian",
    sourceLang: "auto",
    model: "",
    preserveTokens: true,
    useContext: true,
    taskId: "",
    status: "",
    progress: 0,
    phase: "",
    currentFile: "",
    error: "",
    resultFilename: "",
    stats: null,
    defaultModels: [],
    defaultModelSlug: "",
  });

  let eventSource = null;

  const phaseLabel = computed(() => PHASE_LABELS[t.phase] ?? t.phase ?? "");

  function reset() {
    closeSse();
    t.step = "setup";
    t.taskId = "";
    t.status = "";
    t.progress = 0;
    t.phase = "";
    t.currentFile = "";
    t.error = "";
    t.resultFilename = "";
    t.stats = null;
  }

  function closeSse() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  function applySnapshot(data) {
    if (data.status) t.status = data.status;
    if (typeof data.progress === "number") t.progress = data.progress;
    if (data.phase) t.phase = data.phase;
    if (data.file != null) t.currentFile = data.file;
  }

  function openSse(id) {
    closeSse();
    const url = `/api/tasks/${id}/progress`;
    eventSource = new EventSource(url);

    eventSource.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot") {
          applySnapshot(msg);
          return;
        }
        if (msg.type === "progress") {
          if (msg.phase) t.phase = msg.phase;
          if (typeof msg.progress === "number") t.progress = msg.progress;
          if (msg.file != null) t.currentFile = msg.file;
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
          t.error = msg.error ?? "Ошибка";
          t.step = "done";
          closeSse();
          return;
        }
        if (msg.type === "done") {
          closeSse();
        }
      } catch {
        /* ignore */
      }
    };

    eventSource.onerror = () => {
      closeSse();
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

  async function startTranslation() {
    if (!t.selectedFile) {
      throw new Error("Выберите файл .mod");
    }
    if (!t.apiKey?.trim()) {
      throw new Error("Укажите API-ключ OpenRouter");
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
    if (t.model) {
      fd.append("model", t.model);
    }
    fd.append("preserve_tokens", t.preserveTokens ? "true" : "false");
    fd.append("use_context", t.useContext ? "true" : "false");

    const { task_id } = await postTranslate(fd);
    t.taskId = task_id;
    openSse(task_id);
  }

  async function testConnection() {
    if (!t.apiKey?.trim()) {
      throw new Error("Укажите API-ключ");
    }
    return postTestConnection({
      api_key: t.apiKey.trim(),
      model: t.model || undefined,
      target_lang: t.targetLang,
    });
  }

  function resultDownloadUrl() {
    if (!t.taskId) return "";
    return downloadUrl(t.taskId, "download");
  }

  function logDownloadUrl() {
    if (!t.taskId) return "";
    return downloadUrl(t.taskId, "log");
  }

  return {
    t,
    phaseLabel,
    reset,
    loadModels,
    startTranslation,
    testConnection,
    resultDownloadUrl,
    logDownloadUrl,
    closeSse,
  };
}
