import assert from "node:assert/strict";
import { test } from "node:test";

import { escapeHtml, html, SafeHtml } from "../html.js";

test("escapeHtml escapes markup characters and quotes", () => {
  assert.equal(escapeHtml(`<img src=x onerror="a('b')">&`), "&lt;img src=x onerror=&quot;a(&#39;b&#39;)&quot;&gt;&amp;");
});

test("escapeHtml renders null and undefined as empty", () => {
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(undefined), "");
  assert.equal(escapeHtml(0), "0");
});

test("html escapes interpolated values", () => {
  const out = html`<b title="${'"x"'}">${"<script>"}</b>`;
  assert.ok(out instanceof SafeHtml);
  assert.equal(String(out), "<b title=\"&quot;x&quot;\">&lt;script&gt;</b>");
});

test("html passes nested SafeHtml and arrays through", () => {
  const items = ["a<", "b"].map((x) => html`<li>${x}</li>`);
  assert.equal(String(html`<ul>${items}</ul>`), "<ul><li>a&lt;</li><li>b</li></ul>");
});

test("html drops null, undefined and false", () => {
  assert.equal(String(html`${null}${undefined}${false}|${0}`), "|0");
});
