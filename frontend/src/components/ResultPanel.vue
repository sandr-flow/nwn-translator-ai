<script setup>
import { inject, computed } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";
import { useI18n } from "../composables/useI18n.js";

const { t, reset, resultDownloadUrl, logDownloadUrl, enterEditor } = inject(TranslationStateKey);
const { t: i } = useI18n();

const ok = computed(() => t.status === "completed");
const err = computed(() => t.error || (t.status === "failed" ? i("result.unknownError") : ""));

const filesProcessed = computed(() => t.stats?.files_processed ?? "—");
const textsTranslated = computed(() => t.stats?.texts_translated ?? "—");
const errCount = computed(() => t.stats?.total_errors ?? t.stats?.errors?.length ?? 0);
</script>

<template>
  <div class="rounded-xl bg-nwn-panel/80 border border-nwn-muted/20 p-6">
    <template v-if="ok">
      <h2 class="text-lg font-semibold text-emerald-400 mb-4">{{ i("result.done") }}</h2>
      <p class="text-sm text-nwn-muted mb-4">
        {{ i("result.file") }} <span class="text-gray-200">{{ t.resultFilename }}</span>
      </p>
      <div class="text-sm text-nwn-muted mb-4 space-y-1">
        <p>{{ i("result.filesProcessed") }} {{ filesProcessed }}</p>
        <p>{{ i("result.textsTranslated") }} {{ textsTranslated }}</p>
        <p v-if="errCount">{{ i("result.errors") }} {{ errCount }}</p>
      </div>
      <div class="flex flex-wrap gap-3">
        <a
          :href="resultDownloadUrl()"
          download
          class="inline-flex items-center px-4 py-2 rounded-lg bg-nwn-accent text-nwn-dark font-semibold hover:opacity-90"
        >
          {{ i("result.downloadMod") }}
        </a>
        <a
          :href="logDownloadUrl()"
          download
          class="inline-flex items-center px-4 py-2 rounded-lg border border-nwn-muted/40 hover:border-nwn-accent text-sm"
        >
          {{ i("result.downloadLog") }}
        </a>
        <button
          type="button"
          class="inline-flex items-center px-4 py-2 rounded-lg border border-nwn-accent/60 hover:border-nwn-accent text-sm text-nwn-accent"
          @click="enterEditor"
        >
          {{ i("result.editTranslation") }}
        </button>
        <button
          type="button"
          class="text-sm text-nwn-muted hover:text-gray-300"
          @click="reset"
        >
          {{ i("result.newTranslation") }}
        </button>
      </div>
    </template>
    <template v-else>
      <h2 class="text-lg font-semibold text-red-400 mb-4">{{ i("result.error") }}</h2>
      <p class="text-sm text-nwn-muted mb-4 whitespace-pre-wrap">{{ err }}</p>
      <button
        type="button"
        class="px-4 py-2 rounded-lg border border-nwn-muted/40 hover:border-nwn-accent"
        @click="reset"
      >
        {{ i("result.tryAgain") }}
      </button>
    </template>
  </div>
</template>
