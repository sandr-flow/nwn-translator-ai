<script setup>
import { inject, ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";
import { useI18n } from "../composables/useI18n.js";
import { fetchModelLookup } from "../api/client.js";
import CustomSelect from "./CustomSelect.vue";
import ModelSelect from "./ModelSelect.vue";

const { t, testConnection } = inject(TranslationStateKey);
const { t: i } = useI18n();
const testing = ref(false);
const testMsg = ref("");

// Source and target use the same set: in-game strings use single-byte Windows
// code pages (cp1250/cp1251/cp1252); CJK and Turkish (cp1254) are not offered.
const gameSupportedLangKeys = [
  "russian", "english", "ukrainian", "polish", "german", "french",
  "spanish", "italian", "portuguese", "czech", "romanian", "hungarian",
  "dutch",
];

const targetLanguages = computed(() =>
  gameSupportedLangKeys.map((k) => ({ value: k, label: i(`lang.${k}`) }))
);

const sourceLanguages = computed(() => [
  { value: "auto", label: i("lang.auto") },
  ...targetLanguages.value,
]);

const genderOptions = computed(() => [
  { value: "male", label: i("form.genderMale") },
  { value: "female", label: i("form.genderFemale") },
]);

const ALL_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];

const reasoningState = ref("unknown"); // ready | loading | unknown | unsupported
const allowedEfforts = ref([...ALL_EFFORTS]);

const reasoningOptions = computed(() =>
  allowedEfforts.value.map((v) => ({
    value: v,
    label: i(`form.reasoning.${v}`),
  }))
);

const hintOpen = ref(false);
const hintRoot = ref(null);

const reasoningHint = computed(() => {
  if (reasoningState.value === "unknown") return i("form.reasoningUnknown");
  if (reasoningState.value === "unsupported") return i("form.reasoningUnsupported");
  if (reasoningState.value === "ready" && !allowedEfforts.value.includes("none")) {
    return i("form.reasoningMandatory");
  }
  return "";
});

function toggleHint() {
  if (!reasoningHint.value) return;
  hintOpen.value = !hintOpen.value;
}

function onHintClickOutside(e) {
  if (hintRoot.value && !hintRoot.value.contains(e.target)) {
    hintOpen.value = false;
  }
}

function popularReasoning(slug) {
  const list = t.defaultModels;
  if (!Array.isArray(list)) return null;
  for (const item of list) {
    if (item && typeof item === "object" && item.id === slug && item.reasoning) {
      return item.reasoning;
    }
  }
  return null;
}

function lowestEffort(efforts) {
  for (const level of ALL_EFFORTS) {
    if (efforts.includes(level)) return level;
  }
  return efforts[0] || "";
}

function applyReasoningInfo(info, found) {
  const prevMin = lowestEffort(allowedEfforts.value);
  const keepMin = Boolean(prevMin) && t.reasoningEffort === prevMin;
  if (!found) {
    reasoningState.value = "unknown";
    allowedEfforts.value = [...ALL_EFFORTS];
    clampReasoning(keepMin);
    return;
  }
  if (!info?.supported) {
    reasoningState.value = "unsupported";
    allowedEfforts.value = ["none"];
    t.reasoningEffort = "none";
    return;
  }
  reasoningState.value = "ready";
  const efforts = Array.isArray(info.supported_efforts) && info.supported_efforts.length
    ? info.supported_efforts
    : [...ALL_EFFORTS];
  allowedEfforts.value = efforts;
  clampReasoning(keepMin);
}

function clampReasoning(preferMin = false) {
  const efforts = allowedEfforts.value;
  if (!efforts.length) {
    t.reasoningEffort = "";
    return;
  }
  if (preferMin || !efforts.includes(t.reasoningEffort)) {
    t.reasoningEffort = lowestEffort(efforts);
  }
}

let lookupTimer = null;
let lookupSeq = 0;

watch(
  () => [t.model, t.defaultModels],
  () => {
    const slug = typeof t.model === "string" ? t.model.trim() : "";
    const popular = popularReasoning(slug);
    if (popular) {
      lookupSeq += 1;
      applyReasoningInfo(popular, true);
      return;
    }
    if (!slug.includes("/")) {
      lookupSeq += 1;
      applyReasoningInfo(null, false);
      return;
    }
    reasoningState.value = "loading";
    const seq = ++lookupSeq;
    if (lookupTimer) clearTimeout(lookupTimer);
    lookupTimer = setTimeout(async () => {
      try {
        const data = await fetchModelLookup(slug);
        if (seq !== lookupSeq) return;
        applyReasoningInfo(data.reasoning, Boolean(data.found));
      } catch {
        if (seq !== lookupSeq) return;
        applyReasoningInfo(null, false);
      }
    }, 400);
  },
  { immediate: true, deep: true },
);

watch(reasoningHint, () => {
  hintOpen.value = false;
});

onMounted(() => document.addEventListener("mousedown", onHintClickOutside));
onBeforeUnmount(() => {
  document.removeEventListener("mousedown", onHintClickOutside);
  if (lookupTimer) clearTimeout(lookupTimer);
});

// Provider auto-detection from the API key prefix.
// Must match the server-side rules in ai_providers/__init__.py.
const PROVIDER_PREFIXES = [
  { prefix: "sk-or-", provider: "openrouter" },
  { prefix: "pza",    provider: "polza" },
];

function detectProvider(key) {
  const k = (key ?? "").trim();
  if (!k) return "";
  for (const { prefix, provider } of PROVIDER_PREFIXES) {
    if (k.startsWith(prefix)) return provider;
  }
  return "unknown";
}

const activeProvider = computed(() => detectProvider(t.apiKey));

const activeProviderLabel = computed(() => {
  switch (activeProvider.value) {
    case "openrouter": return i("form.providerOpenrouter");
    case "polza":      return i("form.providerPolza");
    case "unknown":    return i("form.providerUnknown");
    default:           return "";
  }
});

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
    <div class="grid grid-cols-1 sm:grid-cols-5 gap-4 items-end">
      <div class="sm:col-span-3">
        <label class="block text-sm text-nwn-muted mb-1">{{ i("form.model") }}</label>
        <ModelSelect v-model="t.model" />
      </div>
      <div class="sm:col-span-2">
        <label class="flex items-center gap-1 text-sm text-nwn-muted mb-1">
          <span>{{ i("form.reasoningEffort") }}</span>
          <span v-if="reasoningHint" ref="hintRoot" class="relative inline-flex">
            <button
              type="button"
              class="inline-flex items-center justify-center w-4 h-4 rounded-full text-nwn-muted hover:text-gray-200 transition-colors"
              :aria-label="i('form.reasoningHelp')"
              :aria-expanded="hintOpen"
              @click.stop="toggleHint"
            >
              <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <circle cx="12" cy="12" r="9" />
                <path d="M12 11v6" />
                <circle cx="12" cy="7.5" r="1" fill="currentColor" stroke="none" />
              </svg>
            </button>
            <span
              v-if="hintOpen"
              class="absolute z-50 left-0 top-full mt-1.5 w-56 rounded-md bg-nwn-panel border border-nwn-muted/30 px-2.5 py-2 text-xs text-gray-300 shadow-lg"
            >
              {{ reasoningHint }}
            </span>
          </span>
        </label>
        <CustomSelect
          v-model="t.reasoningEffort"
          :options="reasoningOptions"
          :disabled="reasoningState === 'loading'"
        />
      </div>
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
      <p v-if="activeProvider" class="text-xs mt-1">
        <span class="text-nwn-muted">{{ i("form.providerActive") }}</span>
        <span
          class="ml-1 font-medium"
          :class="activeProvider === 'unknown' ? 'text-nwn-muted/80' : 'text-nwn-accent'"
        >{{ activeProviderLabel }}</span>
      </p>
      <p v-if="testMsg" class="text-xs text-nwn-muted mt-1">{{ testMsg }}</p>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <div>
        <label class="block text-sm text-nwn-muted mb-1">{{ i("form.sourceLang") }}</label>
        <CustomSelect v-model="t.sourceLang" :options="sourceLanguages" />
      </div>
      <div>
        <label class="block text-sm text-nwn-muted mb-1">{{ i("form.targetLang") }}</label>
        <CustomSelect v-model="t.targetLang" :options="targetLanguages" />
      </div>
      <div>
        <label class="block text-sm text-nwn-muted mb-1">{{ i("form.gender") }}</label>
        <CustomSelect v-model="t.playerGender" :options="genderOptions" />
      </div>
    </div>

  </div>
</template>
