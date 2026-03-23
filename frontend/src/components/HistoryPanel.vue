<script setup>
import { inject, onMounted, ref } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";
import { useI18n } from "../composables/useI18n.js";

const { t, loadHistory, openHistoryTask, deleteHistoryTask, reset } =
  inject(TranslationStateKey);
const { t: i } = useI18n();

const loading = ref(true);
const deleting = ref("");

onMounted(async () => {
  try {
    await loadHistory();
  } finally {
    loading.value = false;
  }
});

function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleString();
}

function langLabel(lang) {
  if (!lang) return "";
  return i(`lang.${lang}`) || lang;
}

function statsSummary(stats) {
  if (!stats) return "";
  const parts = [];
  if (stats.files_processed) parts.push(`${stats.files_processed} ${i("history.files")}`);
  if (stats.texts_translated) parts.push(`${stats.texts_translated} ${i("history.texts")}`);
  return parts.join(", ");
}

function modelShort(model) {
  if (!model) return "";
  // "google/gemini-2.5-flash" → "gemini-2.5-flash"
  const slash = model.lastIndexOf("/");
  return slash >= 0 ? model.slice(slash + 1) : model;
}

async function onDelete(taskId) {
  deleting.value = taskId;
  try {
    await deleteHistoryTask(taskId);
  } finally {
    deleting.value = "";
  }
}

function statusClass(status) {
  if (status === "completed") return "text-emerald-400";
  if (status === "failed") return "text-red-400";
  return "text-nwn-muted";
}

function statusLabel(status) {
  return i(`history.status.${status}`) || status;
}
</script>

<template>
  <div class="rounded-xl bg-nwn-panel/80 border border-nwn-muted/20 p-6">
    <div class="flex items-center justify-between mb-6">
      <h2 class="text-lg font-semibold text-white">{{ i("history.title") }}</h2>
      <button
        type="button"
        class="text-sm text-nwn-muted hover:text-gray-300"
        @click="reset"
      >
        {{ i("history.back") }}
      </button>
    </div>

    <div v-if="loading" class="text-center text-nwn-muted py-8">
      {{ i("history.loading") }}
    </div>

    <div v-else-if="!t.historyItems.length" class="text-center text-nwn-muted py-8">
      {{ i("history.empty") }}
    </div>

    <div v-else class="space-y-3">
      <div
        v-for="item in t.historyItems"
        :key="item.task_id"
        class="rounded-lg bg-nwn-dark/60 border border-nwn-muted/10 p-4 flex items-center gap-4 group"
      >
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 mb-1">
            <span class="text-sm font-medium text-gray-200 truncate">
              {{ item.input_filename }}
            </span>
            <span :class="statusClass(item.status)" class="text-xs font-medium">
              {{ statusLabel(item.status) }}
            </span>
          </div>
          <div class="text-xs text-nwn-muted flex flex-wrap gap-x-4 gap-y-0.5">
            <span>{{ fmtDate(item.created_at) }}</span>
            <span v-if="item.target_lang">{{ langLabel(item.target_lang) }}</span>
            <span v-if="item.model" class="text-nwn-muted/70">{{ modelShort(item.model) }}</span>
            <span v-if="item.stats">{{ statsSummary(item.stats) }}</span>
            <span v-if="item.updated_at" class="text-nwn-muted/50">{{ i("history.edited") }} {{ fmtDate(item.updated_at) }}</span>
          </div>
        </div>

        <div class="flex items-center gap-2 shrink-0">
          <button
            v-if="item.status === 'completed'"
            type="button"
            class="px-3 py-1.5 rounded-lg text-sm bg-nwn-accent text-nwn-dark font-semibold hover:opacity-90"
            @click="openHistoryTask(item.task_id)"
          >
            {{ i("history.open") }}
          </button>
          <button
            type="button"
            class="px-3 py-1.5 rounded-lg text-sm border border-nwn-muted/30 text-nwn-muted hover:text-red-400 hover:border-red-400/50 transition-colors"
            :disabled="deleting === item.task_id"
            @click="onDelete(item.task_id)"
          >
            {{ deleting === item.task_id ? "..." : i("history.delete") }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
