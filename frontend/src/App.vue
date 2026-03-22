<script setup>
import { onMounted, provide, ref } from "vue";
import {
  useTranslation,
  TranslationStateKey,
} from "./composables/useTranslation.js";
import FileUpload from "./components/FileUpload.vue";
import TranslationForm from "./components/TranslationForm.vue";
import ProgressTracker from "./components/ProgressTracker.vue";
import ResultPanel from "./components/ResultPanel.vue";
import TranslationEditor from "./components/TranslationEditor.vue";

const translation = useTranslation();
provide(TranslationStateKey, translation);

const { t, loadModels, startTranslation } = translation;
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
    <div :class="t.step === 'editing' ? 'max-w-6xl mx-auto' : 'max-w-2xl mx-auto'">
      <header class="text-center mb-10">
        <h1 class="text-3xl font-bold tracking-tight text-white mb-2">
          NWN Modules Translator
        </h1>
        <p class="text-nwn-muted text-sm max-w-md mx-auto">
          Перевод модулей Neverwinter Nights через OpenRouter. Загрузите .mod, укажите язык и
          ключ API.
        </p>
      </header>

      <div v-if="t.step === 'setup'" class="space-y-8">
        <FileUpload />
        <TranslationForm />
        <p v-if="formError" class="text-sm text-red-400">{{ formError }}</p>
        <button
          type="button"
          class="w-full py-3 rounded-xl bg-nwn-accent text-nwn-dark font-bold hover:opacity-95 disabled:opacity-40"
          :disabled="busy || !t.selectedFile || !t.apiKey?.trim()"
          @click="onSubmit"
        >
          {{ busy ? "Запуск…" : "Перевести модуль" }}
        </button>
      </div>

      <ProgressTracker v-else-if="t.step === 'running'" />

      <TranslationEditor v-else-if="t.step === 'editing'" />

      <ResultPanel v-else-if="t.step === 'done'" />
    </div>
  </div>
</template>
