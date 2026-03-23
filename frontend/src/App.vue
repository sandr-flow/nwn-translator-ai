<script setup>
import { onMounted, provide, ref } from "vue";
import {
  useTranslation,
  TranslationStateKey,
} from "./composables/useTranslation.js";
import { useI18n } from "./composables/useI18n.js";
import FileUpload from "./components/FileUpload.vue";
import TranslationForm from "./components/TranslationForm.vue";
import ProgressTracker from "./components/ProgressTracker.vue";
import ResultPanel from "./components/ResultPanel.vue";
import TranslationEditor from "./components/TranslationEditor.vue";
import HistoryPanel from "./components/HistoryPanel.vue";

const { t: i, locale, setLocale } = useI18n();
const translation = useTranslation();
provide(TranslationStateKey, translation);

const { t, loadModels, startTranslation, openHistory } = translation;
const busy = ref(false);
const formError = ref("");

onMounted(() => {
  loadModels();
});

async function onSubmit() {
  formError.value = "";
  busy.value = true;
  try {
    await startTranslation();
  } catch (e) {
    formError.value = String(e.message ?? e);
    t.step = "setup";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="min-h-screen py-10 px-4">
    <div :class="t.step === 'editing' ? 'max-w-6xl mx-auto' : 'max-w-3xl mx-auto'">
      <header class="text-center mb-10 relative">
        <div class="absolute right-0 top-0 flex gap-1 text-sm">
          <button
            type="button"
            class="px-1.5 py-0.5 rounded transition-colors"
            :class="locale === 'ru' ? 'text-nwn-accent font-semibold' : 'text-nwn-muted hover:text-gray-300'"
            @click="setLocale('ru')"
          >RU</button>
          <span class="text-nwn-muted/40">|</span>
          <button
            type="button"
            class="px-1.5 py-0.5 rounded transition-colors"
            :class="locale === 'en' ? 'text-nwn-accent font-semibold' : 'text-nwn-muted hover:text-gray-300'"
            @click="setLocale('en')"
          >EN</button>
        </div>
        <h1 class="text-3xl font-bold tracking-tight text-white mb-2">
          NWN Modules Translator
        </h1>
        <p class="text-nwn-muted text-sm max-w-md mx-auto">
          {{ i("app.desc") }}
        </p>
      </header>

      <div v-if="t.step === 'setup'" class="space-y-6">
        <FileUpload />
        <TranslationForm />
        <p v-if="formError" class="text-sm text-red-400">{{ formError }}</p>
        <button
          type="button"
          class="w-full py-3 rounded-xl bg-nwn-accent text-nwn-dark font-bold hover:opacity-95 disabled:opacity-40"
          :disabled="busy || !t.selectedFile || !t.apiKey?.trim()"
          @click="onSubmit"
        >
          {{ busy ? i("app.translating") : i("app.translate") }}
        </button>
        <button
          type="button"
          class="w-full py-2.5 rounded-xl border border-nwn-muted/30 text-sm text-nwn-muted hover:text-gray-300 hover:border-nwn-muted/50 transition-colors"
          @click="openHistory"
        >
          {{ i("history.title") }}
        </button>
      </div>

      <ProgressTracker v-else-if="t.step === 'running'" />

      <TranslationEditor v-else-if="t.step === 'editing'" />

      <ResultPanel v-else-if="t.step === 'done'" />

      <HistoryPanel v-else-if="t.step === 'history'" />
    </div>

    <footer v-if="t.step === 'setup'" class="text-center text-xs text-nwn-muted/60 mt-10 pb-4">
      Open-source · MIT License
      <br />
      <a href="https://github.com/sandr-flow/nwn-translator-ai" target="_blank" rel="noopener noreferrer" class="text-nwn-muted/80 hover:text-nwn-accent">&#11088; {{ i("footer.star") }}</a>
    </footer>
  </div>
</template>
