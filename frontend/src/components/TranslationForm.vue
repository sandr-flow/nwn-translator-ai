<script setup>
import { inject, ref, computed } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";
import { useI18n } from "../composables/useI18n.js";
import CustomSelect from "./CustomSelect.vue";
import ModelSelect from "./ModelSelect.vue";

const { t, testConnection } = inject(TranslationStateKey);
const { t: i } = useI18n();
const testing = ref(false);
const testMsg = ref("");

const langKeys = [
  "russian", "english", "ukrainian", "polish", "german", "french",
  "spanish", "italian", "portuguese", "czech", "romanian", "hungarian",
  "dutch", "turkish", "chinese", "japanese", "korean",
];

const languages = computed(() =>
  langKeys.map((k) => ({ value: k, label: i(`lang.${k}`) }))
);

const sourceLanguages = computed(() => [
  { value: "auto", label: i("lang.auto") },
  ...languages.value,
]);

const genderOptions = computed(() => [
  { value: "male", label: i("form.genderMale") },
  { value: "female", label: i("form.genderFemale") },
]);

async function onTest() {
  testMsg.value = "";
  testing.value = true;
  try {
    const r = await testConnection();
    if (r.ok) {
      testMsg.value = `${i("form.testOk")}: «${r.translated?.slice(0, 80) ?? ""}…»`;
    } else {
      testMsg.value = `${i("form.testError")}: ${r.error ?? "—"}`;
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
      <label class="block text-sm text-nwn-muted mb-1">{{ i("form.model") }}</label>
      <ModelSelect v-model="t.model" />
    </div>

    <div>
      <label class="block text-sm text-nwn-muted mb-1">{{ i("form.apiKey") }}</label>
      <div class="flex items-center gap-3">
        <input
          v-model="t.apiKey"
          type="password"
          autocomplete="off"
          :placeholder="i('form.apiKeyPlaceholder')"
          class="flex-1 min-w-0 rounded-lg bg-nwn-dark border border-nwn-muted/30 px-3 py-2 text-sm focus:border-nwn-accent focus:outline-none"
        />
        <button
          type="button"
          class="shrink-0 text-sm text-nwn-accent hover:underline whitespace-nowrap"
          :disabled="testing"
          @click="onTest"
        >
          {{ testing ? i("form.checking") : i("form.checkKey") }}
        </button>
      </div>
      <p v-if="testMsg" class="text-xs text-nwn-muted mt-1">{{ testMsg }}</p>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <div>
        <label class="block text-sm text-nwn-muted mb-1">{{ i("form.sourceLang") }}</label>
        <CustomSelect v-model="t.sourceLang" :options="sourceLanguages" />
      </div>
      <div>
        <label class="block text-sm text-nwn-muted mb-1">{{ i("form.targetLang") }}</label>
        <CustomSelect v-model="t.targetLang" :options="languages" />
      </div>
      <div>
        <label class="block text-sm text-nwn-muted mb-1">{{ i("form.gender") }}</label>
        <CustomSelect v-model="t.playerGender" :options="genderOptions" />
      </div>
    </div>

  </div>
</template>
