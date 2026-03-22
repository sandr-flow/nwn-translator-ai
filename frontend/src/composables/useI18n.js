import { ref } from "vue";
import messages from "../locales.js";

const locale = ref(localStorage.getItem("nwn-locale") || "ru");

export function useI18n() {
  function t(key) {
    return messages[locale.value]?.[key] ?? messages.ru[key] ?? key;
  }

  function setLocale(l) {
    locale.value = l;
    localStorage.setItem("nwn-locale", l);
  }

  return { locale, t, setLocale };
}
