// Markup for the filter chip bar above the Exchanges list. Rendered once; the view keeps the
// controls in sync with the URL hash afterwards, so typing never loses focus.

import { html } from "../html.js";

export const OUTCOME_OPTIONS = [
  ["", "Any outcome"],
  ["delivered", "delivered"],
  ["answered_by_proxy", "blocked / refused"],
  ["queued", "queued"],
  ["withheld", "withheld"],
  ["dropped", "dropped"],
  ["failed", "failed"],
];

export const RANGE_OPTIONS = [
  ["", "Any time"],
  ["15m", "Last 15 min"],
  ["1h", "Last 1 h"],
  ["24h", "Last 24 h"],
  ["7d", "Last 7 days"],
];

function options(pairs) {
  return pairs.map(([value, label]) => html`<option value="${value}">${label}</option>`);
}

/** Label of the heartbeat chip, with the count of hidden heartbeats. */
export function heartbeatLabel(shown, hidden) {
  return shown ? "Heartbeats shown" : `Heartbeats hidden (${hidden})`;
}

/** `peers` are the backend names, for the Backend filter. */
export function filterBar(peers) {
  const peerOptions = [["", "Any peer"], ["charger", "charger"], ...peers.map((peer) => [peer, peer])];
  return html`<form class="filter-bar" role="search" aria-label="Filter exchanges">
<button type="button" class="chip-toggle" name="heartbeats" aria-pressed="false">${heartbeatLabel(false, 0)}</button>
<label class="field"><span class="field__label">Peer</span><select name="peer">${options(peerOptions)}</select></label>
<label class="field"><span class="field__label">Action</span><input name="action" list="action-list" placeholder="any action" autocomplete="off" spellcheck="false"><datalist id="action-list"></datalist></label>
<label class="field"><span class="field__label">Outcome</span><select name="outcome">${options(OUTCOME_OPTIONS)}</select></label>
<button type="button" class="chip-toggle" name="problem" aria-pressed="false">Problems only</button>
<label class="field field--search"><span class="field__label">Search</span><input name="q" type="search" placeholder="⌕ tx / idTag / id" autocomplete="off" spellcheck="false"></label>
<label class="field"><span class="field__label">Time</span><select name="range">${options(RANGE_OPTIONS)}</select></label>
<button type="button" class="link-button" name="clear" hidden>Clear filters</button>
<span class="filter-bar__spacer"></span>
<button type="button" class="chip-toggle" name="freeze" aria-pressed="false" title="Freeze the list">⏸ Freeze</button>
</form>`;
}

/** <option>s for the action datalist. */
export function actionOptions(actions) {
  return html`${[...actions].sort().map((action) => html`<option value="${action}"></option>`)}`;
}
