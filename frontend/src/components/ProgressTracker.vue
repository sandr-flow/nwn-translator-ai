<script setup>
import { inject, computed } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";
import { useI18n } from "../composables/useI18n.js";

const { t, phaseLabel } = inject(TranslationStateKey);
const { t: i } = useI18n();

const pct = computed(() => Math.round(Math.min(1, Math.max(0, t.progress)) * 100));
const showFileCount = computed(() => t.totalFiles > 0 && (t.phase === "translating" || t.phase === "translating_item"));
</script>

<template>
  <div class="rounded-xl bg-nwn-panel/80 border border-nwn-muted/20 p-6">
    <h2 class="text-lg font-semibold text-nwn-accent mb-4">{{ i("progress.title") }}</h2>

    <div class="h-3 rounded-full bg-nwn-dark overflow-hidden mb-4">
      <div
        class="h-full bg-gradient-to-r from-amber-700 to-nwn-accent transition-all duration-300"
        :style="{ width: pct + '%' }"
      />
    </div>

    <div class="text-sm space-y-2">
      <p>
        <span class="text-nwn-muted">{{ i("progress.phase") }}</span>
        {{ phaseLabel || t.status || "—" }}
        <span v-if="showFileCount" class="ml-2 text-nwn-muted">
          ({{ t.currentIndex }} / {{ t.totalFiles }} {{ i("progress.files") }})
        </span>
      </p>
      <p v-if="t.currentFile">
        <span class="text-nwn-muted">{{ i("progress.file") }}</span>
        <span class="font-mono text-xs break-all">{{ t.currentFile }}</span>
      </p>
      <p class="text-nwn-muted text-xs">{{ i("progress.dontClose") }}</p>
    </div>
  </div>
</template>
