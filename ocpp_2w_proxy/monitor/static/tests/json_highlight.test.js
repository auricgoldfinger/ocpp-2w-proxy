import assert from "node:assert/strict";
import { test } from "node:test";

import { highlightJson } from "../components/json_highlight.js";
import { loadFixture } from "./fixture_loader.js";

const ENTITIES = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };

/** The text a browser shows for the markup. */
function text(markup) {
  return String(markup).replace(/<[^>]*>/g, "").replace(/&(amp|lt|gt|quot|#39);/g, (entity) => ENTITIES[entity]);
}

test("the text is exactly JSON.stringify with a 2-space indent", () => {
  const values = [
    loadFixture("exchange_detail"),
    { a: [1, "two", null, true, false, { b: [] }], c: {}, "d e": -0.5 },
    [],
    {},
    "plain",
    42,
    null,
    { skipped: undefined, kept: [undefined] },
  ];
  for (const value of values) assert.equal(text(highlightJson(value)), JSON.stringify(value, null, 2));
});

test("every token kind has its span", () => {
  const markup = String(highlightJson({ k: "s", n: 1, b: true, z: null }));
  assert.match(markup, /<span class="j-key">&quot;k&quot;<\/span><span class="j-punct">:<\/span> <span class="j-str">&quot;s&quot;<\/span>/);
  assert.match(markup, /<span class="j-num">1<\/span>/);
  assert.match(markup, /<span class="j-bool">true<\/span>/);
  assert.match(markup, /<span class="j-null">null<\/span>/);
});

test("nested values are indented one step per level", () => {
  const markup = String(highlightJson({ outer: { inner: [1] } }));
  assert.match(text(markup), /\n {2}"outer": \{\n {4}"inner": \[\n {6}1\n {4}\]\n {2}\}\n\}/);
});

test("empty containers stay on one line", () => {
  assert.equal(String(highlightJson({})), '<span class="j-punct">{}</span>');
  assert.equal(String(highlightJson([])), '<span class="j-punct">[]</span>');
  assert.equal(text(highlightJson({ a: {}, b: [] })), '{\n  "a": {},\n  "b": []\n}');
});

test("markup in keys and strings is escaped", () => {
  const markup = String(highlightJson({ "<script>k</script>": "</code><script>alert(1)</script>" }));
  assert.ok(!markup.includes("<script>"));
  assert.ok(!markup.includes("</code>"));
  assert.match(markup, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
});
