<script setup>
import { inject, computed } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";
import { useI18n } from "../composables/useI18n.js";

const { t, phaseLabel } = inject(TranslationStateKey);
const { t: i } = useI18n();

const pct = computed(() => Math.round(Math.min(1, Math.max(0, t.progress)) * 100));
</script>

<template>
  <div class="rounded-xl bg-nwn-panel/80 border border-nwn-muted/20 p-6">
    <h2 class="text-lg font-semibold text-nwn-accent mb-4">{{ i("progress.title") }}</h2>

    <div class="flex items-center gap-3 mb-4">
      <div class="flex-1 h-3 rounded-full bg-nwn-dark overflow-hidden">
        <div
          class="h-full bg-gradient-to-r from-amber-700 to-nwn-accent transition-all duration-500"
          :style="{ width: pct + '%' }"
        />
      </div>
      <span class="text-sm font-semibold text-nwn-accent tabular-nums w-10 text-right">{{ pct }}%</span>
    </div>

    <div class="text-sm space-y-2">
      <p>
        <span class="text-nwn-muted">{{ i("progress.phase") }}</span>
        {{ phaseLabel || t.status || "—" }}
      </p>
      <p v-if="t.currentFile">
        <span class="text-nwn-muted">{{ i("progress.file") }}</span>
        <span class="font-mono text-xs break-all">{{ t.currentFile }}</span>
      </p>
      <p class="text-nwn-muted text-xs">{{ i("progress.dontClose") }}</p>
    </div>
  </div>
</template>
