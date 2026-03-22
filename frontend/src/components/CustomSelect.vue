<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from "vue";

const props = defineProps({
  modelValue: { type: String, required: true },
  options: { type: Array, required: true }, // [{ value, label }]
  widthClass: { type: String, default: "w-full" },
});
const emit = defineEmits(["update:modelValue"]);

const open = ref(false);
const dropUp = ref(false);
const root = ref(null);
const trigger = ref(null);

const selected = computed(
  () => props.options.find((o) => o.value === props.modelValue) || props.options[0]
);

function toggle() {
  if (!open.value) {
    // Determine direction before opening
    if (trigger.value) {
      const rect = trigger.value.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      // 220px ≈ max-h-52 (13rem) + margin
      dropUp.value = spaceBelow < 220 && spaceAbove > spaceBelow;
    }
  }
  open.value = !open.value;
}
function pick(opt) {
  emit("update:modelValue", opt.value);
  open.value = false;
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
  <div ref="root" class="relative" :class="widthClass">
    <button
      ref="trigger"
      type="button"
      class="flex items-center justify-between gap-2 w-full rounded-lg bg-nwn-dark border px-3 py-2 text-sm text-left focus:outline-none transition-colors"
      :class="open ? 'border-nwn-accent' : 'border-nwn-muted/30'"
      @click="toggle"
    >
      <span class="truncate">{{ selected.label }}</span>
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
    <Transition name="dropdown">
      <ul
        v-show="open"
        class="absolute z-50 w-full rounded-lg bg-nwn-panel border border-nwn-muted/30 py-1 shadow-lg max-h-52 overflow-y-auto custom-select-list"
        :class="dropUp ? 'bottom-full mb-1' : 'top-full mt-1'"
      >
        <li
          v-for="opt in options"
          :key="opt.value"
          class="px-3 py-1.5 text-sm cursor-pointer truncate transition-colors"
          :class="
            opt.value === modelValue
              ? 'bg-nwn-accent/20 text-nwn-accent'
              : 'text-gray-300 hover:bg-nwn-accent/10 hover:text-gray-200'
          "
          @mousedown.prevent="pick(opt)"
        >
          {{ opt.label }}
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

.custom-select-list {
  scrollbar-width: thin;
  scrollbar-color: rgba(255 255 255 / 0.15) transparent;
}
.custom-select-list::-webkit-scrollbar {
  width: 6px;
}
.custom-select-list::-webkit-scrollbar-thumb {
  background: rgba(255 255 255 / 0.15);
  border-radius: 3px;
}
</style>
