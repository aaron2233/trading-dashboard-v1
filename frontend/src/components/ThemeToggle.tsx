import { useEffect, useState } from "react";

type Theme = "dark" | "light";

const STORAGE_KEY = "td-theme";

function readStoredTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const v = window.localStorage.getItem(STORAGE_KEY);
  return v === "light" ? "light" : "dark";
}

function applyTheme(t: Theme): void {
  const root = document.documentElement;
  if (t === "light") {
    root.classList.add("light");
    root.classList.remove("dark");
  } else {
    root.classList.add("dark");
    root.classList.remove("light");
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => readStoredTheme());

  // On mount, sync the html class with stored preference. The inline
  // pre-paint script in index.html does the initial application; this
  // effect keeps state and DOM in sync after React boots.
  useEffect(() => {
    applyTheme(theme);
    window.localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  function toggle() {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }

  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      className="theme-toggle"
      title={`Switch to ${isDark ? "light" : "dark"} mode`}
      aria-label={`Switch to ${isDark ? "light" : "dark"} mode`}
    >
      <span aria-hidden="true">{isDark ? "☀" : "☾"}</span>
      <span>{isDark ? "Light" : "Dark"}</span>
    </button>
  );
}
