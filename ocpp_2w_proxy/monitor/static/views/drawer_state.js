// What a live re-render of the drawer would otherwise reset: open <details>, scroll positions,
// focus and live-region text. Elements opt in with a stable `data-key`; a key that is gone
// after the re-render is skipped.

function keyed(root) {
  return new Map([...root.querySelectorAll("[data-key]")].map((element) => [element.dataset.key, element]));
}

function focusedKey(root) {
  const active = root.ownerDocument?.activeElement;
  if (!active || !root.contains(active)) return null;
  return active.closest("[data-key]")?.dataset.key ?? null;
}

/** {open, scroll, status, focus} of the keyed elements inside `root`. */
export function captureDrawerState(root) {
  const state = { open: {}, scroll: {}, status: {}, focus: focusedKey(root) };
  for (const [key, element] of keyed(root)) {
    if (element.tagName === "DETAILS") state.open[key] = element.open;
    if (element.scrollTop || element.scrollLeft) state.scroll[key] = { top: element.scrollTop, left: element.scrollLeft };
    if (element.getAttribute("role") === "status") state.status[key] = element.textContent;
  }
  return state;
}

/** Puts a captured state back onto the re-rendered `root`. */
export function restoreDrawerState(root, state) {
  const elements = keyed(root);
  const each = (part, apply) => {
    for (const [key, value] of Object.entries(part)) {
      const element = elements.get(key);
      if (element) apply(element, value);
    }
  };
  each(state.open, (element, open) => {
    element.open = open;
  });
  each(state.status, (element, text) => {
    element.textContent = text;
  });
  // After the details are open again, so their content has height to scroll through.
  each(state.scroll, (element, { top, left }) => {
    element.scrollTop = top;
    element.scrollLeft = left;
  });
  if (state.focus !== null) elements.get(state.focus)?.focus({ preventScroll: true });
}
