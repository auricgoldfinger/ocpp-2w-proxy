import assert from "node:assert/strict";
import { test } from "node:test";

import { captureDrawerState, restoreDrawerState } from "../views/drawer_state.js";

// Just enough of the DOM for drawer_state: keyed elements under one root.
function element(key, { tagName = "DIV", role = null, parent = null, ...props } = {}) {
  return {
    tagName,
    dataset: key === null ? {} : { key },
    parent,
    scrollTop: 0,
    scrollLeft: 0,
    textContent: "",
    focused: null,
    ...props,
    getAttribute: (name) => (name === "role" ? role : null),
    closest(selector) {
      assert.equal(selector, "[data-key]");
      for (let at = this; at; at = at.parent) if (at.dataset.key !== undefined) return at;
      return null;
    },
    focus(options) {
      this.focused = options;
    },
  };
}

function drawer(elements, activeElement = null) {
  const document = { activeElement };
  return {
    ownerDocument: document,
    querySelectorAll: (selector) => {
      assert.equal(selector, "[data-key]");
      return elements.filter((item) => item.dataset.key !== undefined);
    },
    contains: (item) => elements.includes(item),
  };
}

function rendered() {
  const body = element("body");
  const raw = element("raw", { tagName: "DETAILS", open: false });
  const status = element("copy-status", { role: "status" });
  const button = element(null, { tagName: "BUTTON", parent: raw });
  return { body, raw, status, button, all: [body, raw, status, button] };
}

test("a re-render gets back open details, scroll, live-region text and focus", () => {
  const before = rendered();
  before.raw.open = true;
  before.body.scrollTop = 420;
  before.status.textContent = "Copied.";
  const state = captureDrawerState(drawer(before.all, before.button));
  assert.deepEqual(state, { open: { raw: true }, scroll: { body: { top: 420, left: 0 } }, status: { "copy-status": "Copied." }, focus: "raw" });

  const after = rendered();
  restoreDrawerState(drawer(after.all), state);
  assert.equal(after.raw.open, true);
  assert.equal(after.body.scrollTop, 420);
  assert.equal(after.status.textContent, "Copied.");
  assert.deepEqual(after.raw.focused, { preventScroll: true });
});

test("unscrolled elements and focus outside the drawer are not kept", () => {
  const view = rendered();
  const state = captureDrawerState(drawer(view.all, element("outside")));
  assert.deepEqual(state.scroll, {});
  assert.equal(state.focus, null);
});

test("keys that are gone after the re-render are skipped", () => {
  const state = { open: { gone: true }, scroll: { gone: { top: 5, left: 0 } }, status: { gone: "x" }, focus: "gone" };
  const view = rendered();
  assert.doesNotThrow(() => restoreDrawerState(drawer(view.all), state));
  assert.equal(view.raw.open, false);
});
