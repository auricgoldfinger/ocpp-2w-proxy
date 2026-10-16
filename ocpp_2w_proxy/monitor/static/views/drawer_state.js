// What a live re-render of the drawer would otherwise reset: open <details>, scroll positions
// and focus. Elements opt in with a stable `data-key`; a key that is gone after the re-render is
// skipped. Live-region text is not carried over: a status re-inserted on every reload would be
// announced again and go stale.

function keyed(root) {
  return new Map([...root.querySelectorAll("[data-key]")].map((element) => [element.dataset.key, element]));
}

function focusedKey(root) {
  const active = root.ownerDocument?.activeElement;
  if (!active || !root.contains(active)) return null;
  // Only the focused element's own key: focusing a keyed ancestor instead may not take focus.
  return active.dataset?.key ?? null;
}

/** {open, scroll, focus} of the keyed elements inside `root`. */
export function captureDrawerState(root) {
  const state = { open: {}, scroll: {}, focus: focusedKey(root) };
  for (const [key, element] of keyed(root)) {
    if (element.tagName === "DETAILS") state.open[key] = element.open;
    if (element.scrollTop || element.scrollLeft) state.scroll[key] = { top: element.scrollTop, left: element.scrollLeft };
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
  // After the details are open again, so their content has height to scroll through.
  each(state.scroll, (element, { top, left }) => {
    element.scrollTop = top;
    element.scrollLeft = left;
  });
  if (state.focus !== null) elements.get(state.focus)?.focus({ preventScroll: true });
}
