/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,js,ts}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["DM Sans", "system-ui", "sans-serif"],
      },
      colors: {
        nwn: {
          dark: "#0f1419",
          panel: "#1a2332",
          accent: "#c9a227",
          muted: "#8b9cb3",
        },
      },
    },
  },
  plugins: [],
};
