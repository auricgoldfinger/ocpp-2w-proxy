// The "Reply" part of a card: what the charger-side reply said, or why there is none.

import { html } from "../html.js";
import { replyFrame } from "./reply.js";
import { responseView } from "./responses.js";

/** A missing reply says so; without the exchange `detail` (a card from the payload alone) the part is left out. */
export function replySection(detail) {
  if (!detail) return "";
  const frame = replyFrame(detail);
  const body = frame ? responseView(detail.action, frame) : html`<p class="muted">No reply recorded yet.</p>`;
  return html`<h4 class="card__subhead">Reply</h4>${body}`;
}
