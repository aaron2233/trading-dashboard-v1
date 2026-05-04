/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Trading-terminal palette: dark surface + signal colors
        bg: {
          base: "#0b0d10",
          panel: "#13171c",
          elevated: "#1a1f26",
          border: "#252b33",
        },
        text: {
          primary: "#e6e9ef",
          secondary: "#9aa3b2",
          muted: "#5a6373",
        },
        signal: {
          bull: "#22c55e",     // green — bullish / pass
          bear: "#ef4444",     // red — bearish / kill
          flag: "#f59e0b",     // amber — caution
          info: "#3b82f6",     // blue — neutral / info
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
