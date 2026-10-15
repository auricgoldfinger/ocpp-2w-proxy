// Light / dark theme. Follows prefers-color-scheme until the ☾ toggle picks one explicitly;
// that choice is remembered per browser.

const KEY = "flow-monitor-theme";

function stored() {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function store(theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Private mode or blocked storage: the choice lasts for this page only.
  }
}

function effectiveTheme() {
  const explicit = document.documentElement.dataset.theme;
  if (explicit) return explicit;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function apply(theme, button) {
  document.documentElement.dataset.theme = theme;
  button.setAttribute("aria-pressed", String(theme === "dark"));
  button.textContent = theme === "dark" ? "☀" : "☾";
  button.title = theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme";
}

export function initTheme(button) {
  const saved = stored();
  if (saved === "light" || saved === "dark") apply(saved, button);
  else button.setAttribute("aria-pressed", String(effectiveTheme() === "dark"));
  button.addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    apply(next, button);
    store(next);
  });
}
