<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from "vue";
import { useI18n } from "../composables/useI18n.js";

const { t: i } = useI18n();

const presetDefs = [
  { value: "google/gemini-3-flash-preview", label: "Gemini Flash", descKey: "model.geminiFlash.desc" },
  { value: "google/gemini-3.1-flash-lite-preview", label: "Gemini Flash Lite", descKey: "model.geminiFlashLite.desc" },
  { value: "deepseek/deepseek-v3.2", label: "DeepSeek V3", descKey: "model.deepseek.desc" },
  { value: "openai/gpt-5.4-nano", label: "GPT-5.4 Nano", descKey: "model.gptNano.desc" },
];

const CUSTOM_KEY = "__custom__";

const props = defineProps({
  modelValue: { type: String, required: true },
});
const emit = defineEmits(["update:modelValue"]);

const open = ref(false);
const dropUp = ref(false);
const root = ref(null);
const trigger = ref(null);
const customInput = ref(null);

const isCustom = computed(
  () => !presetDefs.some((p) => p.value === props.modelValue)
);

const displayLabel = computed(() => {
  const preset = presetDefs.find((p) => p.value === props.modelValue);
  if (preset) return preset.label;
  if (props.modelValue) return props.modelValue;
  return i("model.choose");
});

function toggle() {
  if (!open.value) {
    const el = trigger.value || root.value;
    if (el) {
      const rect = el.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      dropUp.value = spaceBelow < 280 && spaceAbove > spaceBelow;
    }
  }
  open.value = !open.value;
}

function pick(opt) {
  if (opt === CUSTOM_KEY) {
    if (!isCustom.value) emit("update:modelValue", "");
    open.value = false;
    nextTick(() => customInput.value?.focus());
    return;
  }
  emit("update:modelValue", opt.value);
  open.value = false;
}

function onCustomInput(e) {
  emit("update:modelValue", e.target.value);
}

function onClickOutside(e) {
  if (root.value && !root.value.contains(e.target)) {
    open.value = false;
  }
}

onMounted(() => document.addEventListener("mousedown", onClickOutside));
onBeforeUnmount(() => document.removeEventListener("mousedown", onClickOutside));
</script>

<template>
  <div ref="root" class="relative w-full">
    <!-- Custom input mode -->
    <div v-if="isCustom" class="flex gap-2">
      <div class="relative flex-1 min-w-0">
        <input
          ref="customInput"
          :value="modelValue"
          type="text"
          spellcheck="false"
          autocomplete="off"
          placeholder="provider/model-slug"
          class="w-full rounded-lg bg-nwn-dark border border-nwn-muted/30 px-3 py-2 pr-8 text-sm font-mono focus:border-nwn-accent focus:outline-none"
          @input="onCustomInput"
        />
        <button
          type="button"
          class="absolute right-2 top-1/2 -translate-y-1/2 text-nwn-muted hover:text-gray-300"
          :title="i('model.chooseFromList')"
          @click="toggle"
        >
          <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
      </div>
    </div>

    <!-- Preset selector mode -->
    <button
      v-else
      ref="trigger"
      type="button"
      class="flex items-center justify-between gap-2 w-full rounded-lg bg-nwn-dark border px-3 py-2 text-sm text-left focus:outline-none transition-colors"
      :class="open ? 'border-nwn-accent' : 'border-nwn-muted/30'"
      @click="toggle"
    >
      <span class="truncate">{{ displayLabel }}</span>
      <svg
        class="w-3 h-3 shrink-0 text-nwn-muted transition-transform"
        :class="open ? 'rotate-180' : ''"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>

    <!-- Dropdown -->
    <Transition name="dropdown">
      <ul
        v-show="open"
        class="absolute z-50 w-full rounded-lg bg-nwn-panel border border-nwn-muted/30 py-1 shadow-lg overflow-y-auto model-select-list"
        :class="dropUp ? 'bottom-full mb-1' : 'top-full mt-1'"
      >
        <li
          v-for="opt in presetDefs"
          :key="opt.value"
          class="px-3 py-2 cursor-pointer transition-colors"
          :class="
            opt.value === modelValue
              ? 'bg-nwn-accent/20'
              : 'hover:bg-nwn-accent/10'
          "
          @mousedown.prevent="pick(opt)"
        >
          <div class="text-sm" :class="opt.value === modelValue ? 'text-nwn-accent' : 'text-gray-200'">
            {{ opt.label }}
          </div>
          <div class="text-xs text-nwn-muted">{{ i(opt.descKey) }}</div>
        </li>
        <li class="border-t border-nwn-muted/20 mt-1 pt-1">
          <div
            class="px-3 py-2 cursor-pointer transition-colors text-sm text-nwn-muted hover:bg-nwn-accent/10 hover:text-gray-200"
            :class="isCustom ? 'bg-nwn-accent/20 text-nwn-accent' : ''"
            @mousedown.prevent="pick(CUSTOM_KEY)"
          >
            {{ i("model.custom") }}
          </div>
        </li>
      </ul>
    </Transition>
  </div>
</template>

<style scoped>
.dropdown-enter-active,
.dropdown-leave-active {
  transition: opacity 0.1s ease, transform 0.1s ease;
}
.dropdown-enter-from,
.dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

.model-select-list {
  scrollbar-width: thin;
  scrollbar-color: rgba(255 255 255 / 0.15) transparent;
}
.model-select-list::-webkit-scrollbar {
  width: 6px;
}
.model-select-list::-webkit-scrollbar-thumb {
  background: rgba(255 255 255 / 0.15);
  border-radius: 3px;
}
</style>
