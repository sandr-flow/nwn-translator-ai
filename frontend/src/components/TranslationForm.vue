<script setup>
import { inject, ref } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";

const { t, testConnection } = inject(TranslationStateKey);
const testing = ref(false);
const testMsg = ref("");

const languages = [
  { value: "russian", label: "Русский" },
  { value: "english", label: "Английский" },
  { value: "spanish", label: "Испанский" },
  { value: "french", label: "Французский" },
  { value: "german", label: "Немецкий" },
  { value: "italian", label: "Итальянский" },
  { value: "polish", label: "Польский" },
  { value: "ukrainian", label: "Украинский" },
];

async function onTest() {
  testMsg.value = "";
  testing.value = true;
  try {
    const r = await testConnection();
    if (r.ok) {
      testMsg.value = `Ок: «${r.translated?.slice(0, 80) ?? ""}…»`;
    } else {
      testMsg.value = `Ошибка: ${r.error ?? "неизвестно"}`;
    }
  } catch (e) {
    testMsg.value = String(e.message ?? e);
  } finally {
    testing.value = false;
  }
}
</script>

<template>
  <div class="space-y-4">
    <div>
      <label class="block text-sm text-nwn-muted mb-1">API-ключ OpenRouter</label>
      <input
        v-model="t.apiKey"
        type="password"
        autocomplete="off"
        placeholder="sk-or-…"
        class="w-full rounded-lg bg-nwn-dark border border-nwn-muted/30 px-3 py-2 text-sm focus:border-nwn-accent focus:outline-none"
      />
      <p class="text-xs text-nwn-muted mt-1">
        Ключ не сохраняется на сервере — передаётся только на время перевода.
      </p>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div>
        <label class="block text-sm text-nwn-muted mb-1">Целевой язык</label>
        <select
          v-model="t.targetLang"
          class="w-full rounded-lg bg-nwn-dark border border-nwn-muted/30 px-3 py-2 text-sm focus:border-nwn-accent focus:outline-none"
        >
          <option v-for="opt in languages" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </div>
      <div>
        <label class="block text-sm text-nwn-muted mb-1">Исходный язык</label>
        <select
          v-model="t.sourceLang"
          class="w-full rounded-lg bg-nwn-dark border border-nwn-muted/30 px-3 py-2 text-sm focus:border-nwn-accent focus:outline-none"
        >
          <option value="auto">Авто</option>
          <option value="english">Английский</option>
          <option value="russian">Русский</option>
          <option value="german">Немецкий</option>
          <option value="french">Французский</option>
        </select>
      </div>
    </div>

    <div>
      <label class="block text-sm text-nwn-muted mb-1">Модель OpenRouter</label>
      <input
        v-model="t.model"
        type="text"
        list="nwn-openrouter-models"
        autocomplete="off"
        spellcheck="false"
        placeholder="slug модели, напр. deepseek/deepseek-v3.2"
        class="w-full rounded-lg bg-nwn-dark border border-nwn-muted/30 px-3 py-2 text-sm font-mono focus:border-nwn-accent focus:outline-none"
      />
      <datalist id="nwn-openrouter-models">
        <option v-for="m in t.defaultModels" :key="m" :value="m" />
      </datalist>
      <p class="text-xs text-nwn-muted mt-1">
        Можно ввести любой slug с
        <a
          href="https://openrouter.ai/models"
          target="_blank"
          rel="noopener noreferrer"
          class="text-nwn-accent hover:underline"
          >openrouter.ai/models</a
        >
        или выбрать из подсказок при вводе.
        <span v-if="t.defaultModelSlug"> По умолчанию: {{ t.defaultModelSlug }}</span>
      </p>
    </div>

    <div class="flex flex-wrap gap-4 text-sm">
      <label class="flex items-center gap-2 cursor-pointer">
        <input v-model="t.preserveTokens" type="checkbox" class="rounded border-nwn-muted/50" />
        Сохранять игровые токены (&lt;FirstName&gt; и т.д.)
      </label>
      <label class="flex items-center gap-2 cursor-pointer">
        <input v-model="t.useContext" type="checkbox" class="rounded border-nwn-muted/50" />
        Контекстный перевод диалогов
      </label>
    </div>

    <div class="flex flex-wrap items-center gap-3">
      <button
        type="button"
        class="text-sm text-nwn-accent hover:underline"
        :disabled="testing"
        @click="onTest"
      >
        {{ testing ? "Проверка…" : "Проверить ключ и модель" }}
      </button>
      <span v-if="testMsg" class="text-xs text-nwn-muted max-w-md">{{ testMsg }}</span>
    </div>
  </div>
</template>
