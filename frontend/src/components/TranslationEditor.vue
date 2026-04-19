<script setup>
import { inject, ref, computed, onMounted, watch, nextTick } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";
import { useI18n } from "../composables/useI18n.js";

const { t, loadTranslations, rebuildWithEdits, reset } = inject(TranslationStateKey);
const { t: i } = useI18n();

const loading = ref(true);
const error = ref("");
const selectedFileIdx = ref(0);
const searchQuery = ref("");

// Deep-copy translations so edits are local until "rebuild"
const editableFiles = ref([]);

onMounted(async () => {
  try {
    await loadTranslations();
    editableFiles.value = t.translationFiles.map((f) => ({
      filename: f.filename,
      items: f.items.map((item) => ({ ...item })),
    }));
    const groups = fileGroups.value;
    const init = {};
    groups.forEach(([label], gi) => {
      init[label] = gi !== 0;
    });
    collapsedGroups.value = init;
    if (groups.length && groups[0][1].length) {
      selectedFileIdx.value = groups[0][1][0].idx;
    }
    loading.value = false;
    resizeAllTextareas();
  } catch (e) {
    error.value = String(e.message ?? e);
    loading.value = false;
  }
});

const selectedFile = computed(() => editableFiles.value[selectedFileIdx.value] ?? null);

// Group files by NWN type for sidebar navigation
const EXT_KEYS = {
  ".dlg": "type.dialogs",
  ".jrl": "type.journals",
  ".utc": "type.creatures",
  ".uti": "type.items",
  ".are": "type.areas",
  ".utt": "type.triggers",
  ".utp": "type.placeables",
  ".utd": "type.doors",
  ".utm": "type.stores",
  ".ifo": "type.module",
};

const fileGroups = computed(() => {
  const groups = {};
  const otherLabel = i("type.other");
  editableFiles.value.forEach((file, idx) => {
    const dot = file.filename.lastIndexOf(".");
    const ext = dot !== -1 ? file.filename.substring(dot).toLowerCase() : "";
    const label = EXT_KEYS[ext] ? i(EXT_KEYS[ext]) : ext || otherLabel;
    if (!groups[label]) groups[label] = [];
    groups[label].push({ file, idx });
  });
  return Object.entries(groups).sort((a, b) => {
    if (a[0] === otherLabel) return 1;
    if (b[0] === otherLabel) return -1;
    return a[0].localeCompare(b[0]);
  });
});

const visibleFileGroups = computed(() => {
  const q = searchQuery.value.toLowerCase().trim();
  if (!q) return fileGroups.value;
  const fileMatches = (file) =>
    file.items.some(
      (item) =>
        item.original.toLowerCase().includes(q) ||
        item.translated.toLowerCase().includes(q),
    );
  return fileGroups.value
    .map(([label, entries]) => [label, entries.filter(({ file }) => fileMatches(file))])
    .filter(([, entries]) => entries.length > 0);
});

function isGroupCollapsed(label) {
  if (searchQuery.value.trim()) return false;
  return !!collapsedGroups.value[label];
}

const collapsedGroups = ref({});
const syncShared = ref(true);

// Build a lookup: original text -> list of {fileIdx, itemIdx} across all files
const sharedItemIndex = computed(() => {
  const index = {};
  editableFiles.value.forEach((file, fi) => {
    file.items.forEach((item, ii) => {
      if (item.shared_with && item.shared_with.length) {
        if (!index[item.original]) index[item.original] = [];
        index[item.original].push({ fi, ii });
      }
    });
  });
  return index;
});

function onTranslationInput(event, item) {
  autoResize(event);
  item.translated = event.target.value;
  if (syncShared.value && item.shared_with && item.shared_with.length) {
    const peers = sharedItemIndex.value[item.original];
    if (peers) {
      for (const { fi, ii } of peers) {
        editableFiles.value[fi].items[ii].translated = item.translated;
      }
    }
  }
}

function navigateToFile(filename) {
  const idx = editableFiles.value.findIndex((f) => f.filename === filename);
  if (idx === -1) return;
  const dot = filename.lastIndexOf(".");
  const ext = dot !== -1 ? filename.substring(dot).toLowerCase() : "";
  const otherLabel = i("type.other");
  const label = EXT_KEYS[ext] ? i(EXT_KEYS[ext]) : ext || otherLabel;
  collapsedGroups.value[label] = false;
  selectedFileIdx.value = idx;
  nextTick(() => {
    const sidebar = document.querySelector(".editor-sidebar");
    const active = sidebar?.querySelector(".sidebar-file-btn-active");
    if (active) active.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}

const filteredItems = computed(() => {
  if (!selectedFile.value) return [];
  const q = searchQuery.value.toLowerCase().trim();
  if (!q) return selectedFile.value.items;
  return selectedFile.value.items.filter(
    (item) =>
      item.original.toLowerCase().includes(q) ||
      item.translated.toLowerCase().includes(q)
  );
});

const editedCount = computed(() => {
  let count = 0;
  const origFiles = t.translationFiles;
  for (let fi = 0; fi < editableFiles.value.length; fi++) {
    const ef = editableFiles.value[fi];
    const of_ = origFiles[fi];
    if (!of_) continue;
    for (let ii = 0; ii < ef.items.length; ii++) {
      if (ef.items[ii].translated !== of_.items[ii]?.translated) {
        count++;
      }
    }
  }
  return count;
});

const rebuildError = ref("");

async function onRebuild() {
  rebuildError.value = "";
  const edits = {};
  const origFiles = t.translationFiles;
  for (let fi = 0; fi < editableFiles.value.length; fi++) {
    const ef = editableFiles.value[fi];
    const of_ = origFiles[fi];
    if (!of_) continue;
    for (let ii = 0; ii < ef.items.length; ii++) {
      const edited = ef.items[ii];
      const orig = of_.items[ii];
      if (edited.translated !== orig?.translated) {
        edits[edited.original] = edited.translated;
      }
    }
  }
  try {
    await rebuildWithEdits(edits);
  } catch (e) {
    rebuildError.value = String(e.message ?? e);
  }
}

function autoResize(event) {
  const el = event.target;
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
}

function resizeAllTextareas() {
  nextTick(() => {
    const els = document.querySelectorAll(".translation-editor-area textarea");
    els.forEach((el) => {
      el.style.height = "auto";
      el.style.height = el.scrollHeight + "px";
    });
  });
}

watch(selectedFileIdx, resizeAllTextareas);
watch(searchQuery, resizeAllTextareas);

function goBack() {
  t.step = "done";
}
</script>

<template>
  <div class="rounded-xl bg-nwn-panel/80 border border-nwn-muted/20 p-6">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-semibold text-nwn-accent">{{ i("editor.title") }}</h2>
      <button
        type="button"
        class="text-sm text-nwn-muted hover:text-gray-300"
        @click="goBack"
      >
        {{ i("editor.back") }}
      </button>
    </div>

    <div v-if="loading" class="text-sm text-nwn-muted py-8 text-center">
      {{ i("editor.loading") }}
    </div>

    <div v-else-if="error" class="text-sm text-red-400 py-4">{{ error }}</div>

    <template v-else>
      <div class="flex gap-4" style="min-height: 500px; max-height: 75vh">
        <!-- File list sidebar grouped by type -->
        <div class="w-56 shrink-0 overflow-y-auto border-r border-nwn-muted/20 pr-3 editor-sidebar" style="max-height: 75vh">
          <p class="text-xs text-nwn-muted mb-2">
            {{ i("editor.files") }} ({{ editableFiles.length }})
          </p>
          <div v-for="[label, entries] in visibleFileGroups" :key="label" class="mb-1.5">
            <button
              type="button"
              class="flex items-center gap-1 w-full text-left px-1 py-1 text-xs font-semibold text-nwn-muted uppercase tracking-wide hover:text-gray-300"
              @click="collapsedGroups[label] = !collapsedGroups[label]"
            >
              <span class="transition-transform" :class="isGroupCollapsed(label) ? '-rotate-90' : ''">&#9662;</span>
              {{ label }}
              <span class="font-normal normal-case">({{ entries.length }})</span>
            </button>
            <div v-show="!isGroupCollapsed(label)">
              <button
                v-for="{ file, idx } in entries"
                :key="file.filename"
                type="button"
                class="block w-full text-left pl-6 pr-2 py-1 rounded text-sm font-mono truncate mb-0.5"
                :class="[
                  idx === selectedFileIdx
                    ? 'bg-nwn-accent/20 text-nwn-accent sidebar-file-btn-active'
                    : 'text-gray-300 hover:bg-nwn-dark/50',
                ]"
                @click="selectedFileIdx = idx"
              >
                {{ file.filename }}
                <span class="text-xs text-nwn-muted">({{ file.items.length }})</span>
              </button>
            </div>
          </div>
        </div>

        <!-- Main editor area -->
        <div class="flex-1 min-w-0 flex flex-col" style="max-height: 75vh">
          <div v-if="selectedFile" class="flex items-center gap-3 mb-3 flex-wrap shrink-0 pr-2">
            <h3 class="text-sm font-semibold text-gray-200">
              {{ selectedFile.filename }}
            </h3>
            <input
              v-model="searchQuery"
              type="text"
              :placeholder="i('editor.search')"
              class="px-2 py-1 rounded bg-nwn-dark border border-nwn-muted/30 text-sm text-gray-200 placeholder-nwn-muted/50 w-48"
            />
            <label class="flex items-center gap-1.5 text-xs text-nwn-muted cursor-pointer select-none ml-auto">
              <input
                type="checkbox"
                v-model="syncShared"
                class="accent-nwn-accent"
              />
              {{ i("editor.syncShared") }}
            </label>
          </div>
          <div class="overflow-y-auto translation-editor-area pr-2 flex-1 min-h-0">
          <div v-if="selectedFile" class="space-y-3">

            <div
              v-for="(item, idx) in filteredItems"
              :key="idx"
              class="rounded-lg bg-nwn-dark/40 border border-nwn-muted/10"
            >
              <div class="grid grid-cols-2 gap-3 p-3">
                <div>
                  <p class="text-xs text-nwn-muted mb-1">{{ i("editor.original") }}</p>
                  <p class="text-sm text-gray-300 whitespace-pre-wrap break-words">{{ item.original }}</p>
                </div>
                <div>
                  <p class="text-xs text-nwn-muted mb-1">{{ i("editor.translation") }}</p>
                  <textarea
                    :value="item.translated"
                    class="w-full px-2 py-1.5 rounded bg-nwn-dark border border-nwn-muted/30 text-sm text-gray-200 resize-none overflow-hidden"
                    rows="1"
                    @input="onTranslationInput($event, item)"
                    ref="textareas"
                  />
                </div>
              </div>
              <div
                v-if="item.shared_with && item.shared_with.length"
                class="px-3 pb-2 flex flex-wrap items-center gap-1.5"
              >
                <span class="text-xs text-nwn-muted">{{ i("editor.sharedWith") }}</span>
                <button
                  v-for="fname in item.shared_with"
                  :key="fname"
                  type="button"
                  class="text-xs font-mono px-1.5 py-0.5 rounded bg-nwn-accent/10 text-nwn-accent/70 hover:bg-nwn-accent/25 hover:text-nwn-accent cursor-pointer"
                  @click="navigateToFile(fname)"
                >{{ fname }}</button>
              </div>
            </div>

            <p
              v-if="filteredItems.length === 0"
              class="text-sm text-nwn-muted py-4 text-center"
            >
              {{ i("editor.noMatches") }}
            </p>
          </div>
          </div>
        </div>
      </div>

      <!-- Bottom actions -->
      <div class="mt-6 flex items-center gap-4 border-t border-nwn-muted/20 pt-4">
        <button
          type="button"
          class="px-5 py-2.5 rounded-xl bg-nwn-accent text-nwn-dark font-bold hover:opacity-95 disabled:opacity-40"
          :disabled="t.rebuilding"
          @click="onRebuild"
        >
          {{ t.rebuilding ? i("editor.rebuilding") : i("editor.rebuild") }}
        </button>
        <span v-if="editedCount > 0" class="text-sm text-nwn-accent">
          {{ i("editor.edited") }} {{ editedCount }}
        </span>
        <p v-if="rebuildError" class="text-sm text-red-400">{{ rebuildError }}</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* Dark scrollbars matching the panel theme */
.editor-sidebar,
.translation-editor-area {
  scrollbar-width: thin;
  scrollbar-color: rgba(255 255 255 / 0.15) transparent;
}
.editor-sidebar::-webkit-scrollbar,
.translation-editor-area::-webkit-scrollbar {
  width: 6px;
}
.editor-sidebar::-webkit-scrollbar-track,
.translation-editor-area::-webkit-scrollbar-track {
  background: transparent;
}
.editor-sidebar::-webkit-scrollbar-thumb,
.translation-editor-area::-webkit-scrollbar-thumb {
  background: rgba(255 255 255 / 0.15);
  border-radius: 3px;
}
.editor-sidebar::-webkit-scrollbar-thumb:hover,
.translation-editor-area::-webkit-scrollbar-thumb:hover {
  background: rgba(255 255 255 / 0.25);
}
</style>
