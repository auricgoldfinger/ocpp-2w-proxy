// The "Reply" part of a card: what the charger-side reply said, or why there is none.

import { badge } from "../components/swimlane.js";
import { html } from "../html.js";
import { errorView, replyFrame } from "./reply.js";

/**
 * `renderResult(frame)` draws a CallResult. A CallError shows its code, a missing reply says so,
 * and without the exchange `detail` (a card rendered from the payload alone) the part is left out.
 */
export function replySection(detail, renderResult) {
  if (!detail) return "";
  const frame = replyFrame(detail);
  let body;
  if (!frame) body = html`<p class="muted">No reply recorded yet.</p>`;
  else if (frame.msg_type === "error") body = html`<p>${badge(errorView(frame))}</p>`;
  else body = renderResult(frame);
  return html`<h4 class="card__subhead">Reply</h4>${body}`;
}
