/** @type {import('tailwindcss').Config} */
//
// Color tokens are defined as CSS variables (rgb-triplet form, no commas) so
// alpha-value utilities like `bg-signal-flag/20` keep working. The actual
// values live in src/index.css under :root (dark, default) and html.light
// (light mode override).
//
function rgbVar(name) {
  return `rgb(var(--color-${name}) / <alpha-value>)`;
}

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          base: rgbVar("bg-base"),
          panel: rgbVar("bg-panel"),
          elevated: rgbVar("bg-elevated"),
          border: rgbVar("bg-border"),
          rule: rgbVar("bg-rule"),
        },
        text: {
          primary: rgbVar("text-primary"),
          secondary: rgbVar("text-secondary"),
          muted: rgbVar("text-muted"),
        },
        signal: {
          bull: rgbVar("signal-bull"),
          bear: rgbVar("signal-bear"),
          flag: rgbVar("signal-flag"),
          info: rgbVar("signal-info"),
          violet: rgbVar("signal-violet"),
          paper: rgbVar("signal-paper"),
        },
      },
      fontFamily: {
        sans: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
        display: ['"VT323"', '"JetBrains Mono"', "ui-monospace", "monospace"],
        stamp: ['"Major Mono Display"', '"JetBrains Mono"', "monospace"],
      },
      letterSpacing: {
        widest: "0.25em",
      },
    },
  },
  plugins: [],
};
