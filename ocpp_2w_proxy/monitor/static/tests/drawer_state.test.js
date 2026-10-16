import assert from "node:assert/strict";
import { test } from "node:test";

import { captureDrawerState, restoreDrawerState } from "../views/drawer_state.js";

// Just enough of the DOM for drawer_state: keyed elements under one root.
function element(key, { tagName = "DIV", ...props } = {}) {
  return {
    tagName,
    dataset: key === null ? {} : { key },
    scrollTop: 0,
    scrollLeft: 0,
    focused: null,
    ...props,
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
  const button = element("copy-record", { tagName: "BUTTON" });
  const unkeyed = element(null, { tagName: "A" });
  return { body, raw, button, unkeyed, all: [body, raw, button, unkeyed] };
}

test("a re-render gets back open details, scroll and focus", () => {
  const before = rendered();
  before.raw.open = true;
  before.body.scrollTop = 420;
  const state = captureDrawerState(drawer(before.all, before.button));
  assert.deepEqual(state, { open: { raw: true }, scroll: { body: { top: 420, left: 0 } }, focus: "copy-record" });

  const after = rendered();
  restoreDrawerState(drawer(after.all), state);
  assert.equal(after.raw.open, true);
  assert.equal(after.body.scrollTop, 420);
  assert.deepEqual(after.button.focused, { preventScroll: true });
});

test("focus on an element without its own key is not moved to a keyed ancestor", () => {
  const view = rendered();
  assert.equal(captureDrawerState(drawer(view.all, view.unkeyed)).focus, null);
});

test("unscrolled elements and focus outside the drawer are not kept", () => {
  const view = rendered();
  const state = captureDrawerState(drawer(view.all, element("outside")));
  assert.deepEqual(state.scroll, {});
  assert.equal(state.focus, null);
});

test("keys that are gone after the re-render are skipped", () => {
  const state = { open: { gone: true }, scroll: { gone: { top: 5, left: 0 } }, focus: "gone" };
  const view = rendered();
  assert.doesNotThrow(() => restoreDrawerState(drawer(view.all), state));
  assert.equal(view.raw.open, false);
});
