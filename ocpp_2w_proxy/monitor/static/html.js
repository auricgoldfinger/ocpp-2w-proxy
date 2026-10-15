// Safe HTML building. Every interpolated value is escaped unless it is already SafeHtml,
// so payload text can never become markup. Views only assign SafeHtml to innerHTML.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export class SafeHtml {
  constructor(value) {
    this.value = value;
  }

  toString() {
    return this.value;
  }
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);
}

function render(value) {
  if (value instanceof SafeHtml) return value.value;
  if (Array.isArray(value)) return value.map(render).join("");
  if (value === null || value === undefined || value === false) return "";
  return escapeHtml(value);
}

/** Tagged template: html`<b>${text}</b>` escapes `text`; nested html`` and arrays pass through. */
export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((value, index) => {
    out += render(value) + strings[index + 1];
  });
  return new SafeHtml(out);
}

/** Replaces an element's content; refuses anything that did not come from html``. */
export function setHtml(element, safe) {
  if (!(safe instanceof SafeHtml)) throw new TypeError("setHtml needs SafeHtml");
  element.innerHTML = safe.value;
}
