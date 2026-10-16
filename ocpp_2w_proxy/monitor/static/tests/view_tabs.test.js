import assert from "node:assert/strict";
import { test } from "node:test";

import { viewPanel, viewTabs } from "../components/view_tabs.js";
import { html } from "../html.js";
import { createViewMode, modeForKey } from "../views/view_mode.js";

test("the tablist marks the selected tab and gives only it the tab stop", () => {
  const markup = String(viewTabs("json"));
  assert.match(markup, /<div class="view-tabs" role="tablist" aria-label="Detail view">/);
  assert.match(markup, /id="drawer-tab-readable" data-view="readable" data-key="drawer-tab-readable" aria-selected="false" tabindex="-1">Readable</);
  assert.match(markup, /id="drawer-tab-json" data-view="json" data-key="drawer-tab-json" aria-selected="true" tabindex="0" aria-controls="drawer-panel-json">JSON</);
});

test("the panel is labelled by its tab", () => {
  assert.equal(String(viewPanel("readable", html`x`)), '<div role="tabpanel" class="view-panel" id="drawer-panel-readable" aria-labelledby="drawer-tab-readable">x</div>');
});

test("every exchange opens in Readable; the same one keeps its tab", () => {
  const mode = createViewMode();
  mode.open("1");
  assert.equal(mode.mode, "readable");
  assert.equal(mode.select("json"), true);
  assert.equal(mode.select("json"), false, "no change");
  mode.open("1");
  assert.equal(mode.mode, "json", "a live reload of the same exchange");
  mode.open("2");
  assert.equal(mode.mode, "readable");
  assert.equal(mode.select("nonsense"), false);
});

test("arrow keys wrap between tabs; Home and End jump", () => {
  assert.equal(modeForKey("ArrowRight", "readable"), "json");
  assert.equal(modeForKey("ArrowRight", "json"), "readable");
  assert.equal(modeForKey("ArrowLeft", "readable"), "json");
  assert.equal(modeForKey("Home", "json"), "readable");
  assert.equal(modeForKey("End", "readable"), "json");
  assert.equal(modeForKey("Enter", "readable"), null);
});
