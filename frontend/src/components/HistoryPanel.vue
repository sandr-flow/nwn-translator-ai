<script setup>
import { inject, onMounted, ref, computed, watch } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";
import { useI18n } from "../composables/useI18n.js";
import CustomSelect from "./CustomSelect.vue";

const { t, loadHistory, openHistoryTask, deleteHistoryTask, reset } =
  inject(TranslationStateKey);
const { t: i } = useI18n();

const loading = ref(true);
const deleting = ref("");

const searchQuery = ref("");
const sortBy = ref("date_desc");
const currentPage = ref(1);
const PAGE_SIZE = 10;

const sortOptions = computed(() => [
  { value: "date_desc", label: i("history.sort.dateDesc") },
  { value: "date_asc", label: i("history.sort.dateAsc") },
  { value: "name_asc", label: i("history.sort.nameAsc") },
  { value: "name_desc", label: i("history.sort.nameDesc") },
]);

onMounted(async () => {
  try {
    await loadHistory();
  } finally {
    loading.value = false;
  }
});

const filteredItems = computed(() => {
  const q = searchQuery.value.toLowerCase().trim();
  const items = t.historyItems ?? [];
  if (!q) return items.slice();
  return items.filter((item) =>
    (item.input_filename ?? "").toLowerCase().includes(q),
  );
});

const sortedItems = computed(() => {
  const arr = filteredItems.value.slice();
  switch (sortBy.value) {
    case "date_asc":
      return arr.sort((a, b) => (a.created_at ?? 0) - (b.created_at ?? 0));
    case "name_asc":
      return arr.sort((a, b) =>
        (a.input_filename ?? "").localeCompare(b.input_filename ?? ""),
      );
    case "name_desc":
      return arr.sort((a, b) =>
        (b.input_filename ?? "").localeCompare(a.input_filename ?? ""),
      );
    case "date_desc":
    default:
      return arr.sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));
  }
});

const totalPages = computed(() =>
  Math.max(1, Math.ceil(sortedItems.value.length / PAGE_SIZE)),
);

const pagedItems = computed(() => {
  const start = (currentPage.value - 1) * PAGE_SIZE;
  return sortedItems.value.slice(start, start + PAGE_SIZE);
});

watch([searchQuery, sortBy], () => {
  currentPage.value = 1;
});

watch(totalPages, (n) => {
  if (currentPage.value > n) currentPage.value = n;
});

function goPrev() {
  if (currentPage.value > 1) currentPage.value--;
}
function goNext() {
  if (currentPage.value < totalPages.value) currentPage.value++;
}

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

    <template v-else>
      <div class="flex flex-wrap gap-3 items-center mb-4">
        <input
          v-model="searchQuery"
          type="text"
          :placeholder="i('history.searchPlaceholder')"
          class="flex-1 min-w-[12rem] rounded-lg bg-nwn-dark border border-nwn-muted/30 px-3 py-2 text-sm text-gray-200 placeholder-nwn-muted/50 focus:border-nwn-accent focus:outline-none"
        />
        <div class="flex items-center gap-2">
          <span class="text-xs text-nwn-muted">{{ i("history.sortBy") }}</span>
          <CustomSelect v-model="sortBy" :options="sortOptions" width-class="w-56" />
        </div>
      </div>

      <div v-if="!sortedItems.length" class="text-center text-nwn-muted py-8">
        {{ i("history.noMatches") }}
      </div>

      <div v-else class="space-y-3">
        <div
          v-for="item in pagedItems"
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

      <div
        v-if="totalPages > 1"
        class="mt-4 flex items-center justify-between text-sm text-nwn-muted"
      >
        <button
          type="button"
          class="px-3 py-1.5 rounded-lg border border-nwn-muted/30 hover:border-nwn-muted/60 hover:text-gray-300 disabled:opacity-40 disabled:hover:border-nwn-muted/30 disabled:hover:text-nwn-muted transition-colors"
          :disabled="currentPage <= 1"
          @click="goPrev"
        >
          {{ i("history.prev") }}
        </button>
        <span>
          {{ i("history.page") }} {{ currentPage }} / {{ totalPages }}
        </span>
        <button
          type="button"
          class="px-3 py-1.5 rounded-lg border border-nwn-muted/30 hover:border-nwn-muted/60 hover:text-gray-300 disabled:opacity-40 disabled:hover:border-nwn-muted/30 disabled:hover:text-nwn-muted transition-colors"
          :disabled="currentPage >= totalPages"
          @click="goNext"
        >
          {{ i("history.next") }}
        </button>
      </div>
    </template>
  </div>
</template>
