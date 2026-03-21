<script setup>
import { inject, computed } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";

const { t, reset, resultDownloadUrl, logDownloadUrl } = inject(TranslationStateKey);

const ok = computed(() => t.status === "completed");
const err = computed(() => t.error || (t.status === "failed" ? "Неизвестная ошибка" : ""));

const filesProcessed = computed(() => t.stats?.files_processed ?? "—");
const itemsTranslated = computed(() => t.stats?.items_translated ?? "—");
const errCount = computed(() => t.stats?.total_errors ?? t.stats?.errors?.length ?? 0);
</script>

<template>
  <div class="rounded-xl bg-nwn-panel/80 border border-nwn-muted/20 p-6">
    <template v-if="ok">
      <h2 class="text-lg font-semibold text-emerald-400 mb-4">Готово</h2>
      <p class="text-sm text-nwn-muted mb-4">
        Файл: <span class="text-gray-200">{{ t.resultFilename }}</span>
      </p>
      <div class="text-sm text-nwn-muted mb-4 space-y-1">
        <p>Обработано файлов: {{ filesProcessed }}</p>
        <p>Строк переведено: {{ itemsTranslated }}</p>
        <p v-if="errCount">Предупреждений/ошибок в логе: {{ errCount }}</p>
      </div>
      <div class="flex flex-wrap gap-3">
        <a
          :href="resultDownloadUrl()"
          download
          class="inline-flex items-center px-4 py-2 rounded-lg bg-nwn-accent text-nwn-dark font-semibold hover:opacity-90"
        >
          Скачать .mod
        </a>
        <a
          :href="logDownloadUrl()"
          download
          class="inline-flex items-center px-4 py-2 rounded-lg border border-nwn-muted/40 hover:border-nwn-accent text-sm"
        >
          Скачать лог (JSONL)
        </a>
        <button
          type="button"
          class="text-sm text-nwn-muted hover:text-gray-300"
          @click="reset"
        >
          Новый перевод
        </button>
      </div>
    </template>
    <template v-else>
      <h2 class="text-lg font-semibold text-red-400 mb-4">Ошибка</h2>
      <p class="text-sm text-nwn-muted mb-4 whitespace-pre-wrap">{{ err }}</p>
      <button
        type="button"
        class="px-4 py-2 rounded-lg border border-nwn-muted/40 hover:border-nwn-accent"
        @click="reset"
      >
        Попробовать снова
      </button>
    </template>
  </div>
</template>
