import assert from "node:assert/strict";
import { test } from "node:test";

import { tabStops } from "../views/focus_trap.js";

// Just enough of the DOM: elements with a parent chain; `closest` knows the one selector used.
function element(tagName, parentElement = null, { open = false } = {}) {
  return {
    tagName,
    parentElement,
    open,
    closest(selector) {
      assert.equal(selector, "details:not([open])");
      for (let at = this; at; at = at.parentElement) if (at.tagName === "DETAILS" && !at.open) return at;
      return null;
    },
  };
}

const root = (elements) => ({ querySelectorAll: () => elements });

test("controls inside a closed details are skipped; its summary stays a stop", () => {
  const button = element("BUTTON");
  const closed = element("DETAILS");
  const summary = element("SUMMARY", closed);
  const hidden = element("BUTTON", closed);
  assert.deepEqual(tabStops(root([button, summary, hidden])), [button, summary]);
});

test("an open details shows its controls", () => {
  const open = element("DETAILS", null, { open: true });
  const inside = element("BUTTON", open);
  assert.deepEqual(tabStops(root([inside])), [inside]);
});

test("the summary of a details nested in a closed one is not a stop", () => {
  const outer = element("DETAILS");
  const inner = element("DETAILS", outer);
  const summary = element("SUMMARY", inner);
  assert.deepEqual(tabStops(root([summary])), []);
});
