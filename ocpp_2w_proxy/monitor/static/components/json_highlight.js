// Syntax-coloured JSON. It walks the value rather than scanning text, so every token is escaped
// by html`` and the text reads exactly as JSON.stringify(value, null, 2).

import { html } from "../html.js";

const INDENT = "  ";

const token = (kind, text) => html`<span class="j-${kind}">${text}</span>`;
const punct = (text) => token("punct", text);

// JSON.stringify leaves these out of objects and writes them as null inside arrays.
const isSkipped = (value) => value === undefined || typeof value === "function" || typeof value === "symbol";

function scalar(value) {
  if (value === null) return token("null", "null");
  if (typeof value === "boolean") return token("bool", String(value));
  if (typeof value === "number") return token(Number.isFinite(value) ? "num" : "null", JSON.stringify(value));
  return token("str", JSON.stringify(String(value)));
}

function block(open, close, items, depth) {
  if (items.length === 0) return punct(`${open}${close}`);
  const inner = INDENT.repeat(depth + 1);
  const lines = items.map((item, at) => html`\n${inner}${item}${at < items.length - 1 ? punct(",") : ""}`);
  return html`${punct(open)}${lines}\n${INDENT.repeat(depth)}${punct(close)}`;
}

function walk(value, depth) {
  if (Array.isArray(value)) {
    return block("[", "]", value.map((item) => (isSkipped(item) ? scalar(null) : walk(item, depth + 1))), depth);
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value).filter(([, item]) => !isSkipped(item));
    const items = entries.map(([key, item]) => html`${token("key", JSON.stringify(key))}${punct(":")} ${walk(item, depth + 1)}`);
    return block("{", "}", items, depth);
  }
  return scalar(value);
}

/** `value` as indented JSON with a j-key / j-str / j-num / j-bool / j-null / j-punct span per token. */
export function highlightJson(value) {
  return walk(value, 0);
}
