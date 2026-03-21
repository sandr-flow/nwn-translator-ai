<script setup>
import { inject, ref } from "vue";
import { TranslationStateKey } from "../composables/useTranslation.js";

const { t } = inject(TranslationStateKey);
const dragOver = ref(false);
const inputRef = ref(null);

function setFile(file) {
  if (!file) return;
  const name = file.name.toLowerCase();
  if (!name.endsWith(".mod") && !name.endsWith(".erf") && !name.endsWith(".hak")) {
    alert("Нужен файл .mod, .erf или .hak");
    return;
  }
  t.selectedFile = file;
}

function onDrop(e) {
  dragOver.value = false;
  const f = e.dataTransfer?.files?.[0];
  if (f) setFile(f);
}

function onInput(e) {
  const f = e.target.files?.[0];
  if (f) setFile(f);
}

function clear() {
  t.selectedFile = null;
  if (inputRef.value) inputRef.value.value = "";
}
</script>

<template>
  <div
    class="rounded-xl border-2 border-dashed transition-colors px-6 py-10 text-center"
    :class="
      dragOver
        ? 'border-nwn-accent bg-nwn-accent/10'
        : 'border-nwn-muted/40 bg-nwn-panel/50 hover:border-nwn-muted/60'
    "
    @dragover.prevent="dragOver = true"
    @dragleave.prevent="dragOver = false"
    @drop.prevent="onDrop"
  >
    <input
      ref="inputRef"
      type="file"
      accept=".mod,.erf,.hak"
      class="hidden"
      @change="onInput"
    />
    <p class="text-nwn-muted text-sm mb-3">Перетащите файл сюда или</p>
    <button
      type="button"
      class="px-4 py-2 rounded-lg bg-nwn-accent/20 text-nwn-accent font-semibold hover:bg-nwn-accent/30"
      @click="inputRef?.click()"
    >
      Выбрать файл
    </button>
    <div v-if="t.selectedFile" class="mt-4 text-sm">
      <span class="text-emerald-400">{{ t.selectedFile.name }}</span>
      <span class="text-nwn-muted ml-2">
        ({{ (t.selectedFile.size / (1024 * 1024)).toFixed(2) }} МБ)
      </span>
      <button type="button" class="ml-3 text-red-400 hover:underline text-xs" @click="clear">
        Убрать
      </button>
    </div>
    <p class="text-nwn-muted text-xs mt-3">Максимум 50 МБ</p>
  </div>
</template>
