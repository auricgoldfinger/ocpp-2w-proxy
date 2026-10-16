// The Tab stops of the modal drawer. A control inside a closed <details> is not rendered and
// cannot take focus, so it is left out; that details' own <summary> is a stop.

const FOCUSABLE = "a[href], button:not([disabled]), input, select, summary, [tabindex]:not([tabindex='-1'])";

function isRendered(element) {
  const closed = element.closest("details:not([open])");
  if (!closed) return true;
  if (element.tagName !== "SUMMARY" || element.parentElement !== closed) return false;
  return closed.parentElement ? isRendered(closed.parentElement) : true;
}

/** The focusable elements of `root` that can take focus now, in document order. */
export function tabStops(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter(isRendered);
}
