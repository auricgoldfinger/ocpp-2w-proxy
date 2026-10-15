// Swimlane markup for the Exchanges list. Pure: models in, SafeHtml out.
// Desktop shows one cell per lane; below 700 px CSS hides the lanes and shows the fan-out strip.

import { formatTime } from "../format.js";
import { html } from "../html.js";

export function badge({ label, icon, tone, title }) {
  return html`<span class="badge tone-${tone}" title="${title ?? label}"><span class="badge__icon" aria-hidden="true">${icon}</span><span class="badge__label">${label}</span></span>`;
}

/** A ⇄ rewrite chip; a ⇄tx chip links to its session when `txHref` is given. */
export function rewriteChip(chip, txHref = null) {
  if (chip.field === "tx" && txHref) return html`<a class="chip chip--link" href="${txHref}" title="${chip.title} · show this session">${chip.label}</a>`;
  return html`<span class="chip" title="${chip.title}">${chip.label}</span>`;
}

/** The transaction id as a link to its session (the ?tx= filter). */
export function txLink(transactionId, txHref) {
  return html`<a class="tx-link" href="${txHref}" title="Show every exchange of transaction ${transactionId}">tx ${transactionId}</a>`;
}

function tags(items) {
  return items.map((tag) => html`<span class="tag" title="${tag.title}">${tag.label}</span>`);
}

function legContent(leg, txHref) {
  return html`${badge(leg.view)}${leg.chips.map((chip) => rewriteChip(chip, txHref))}${tags(leg.tags)}`;
}

function originContent(cell, txHref) {
  const marker = cell.lane === "proxy"
    ? html`<span class="marker marker--proxy-origin" aria-hidden="true">◇</span>`
    : html`<span class="marker marker--origin" aria-hidden="true">●</span>`;
  const tag = cell.tag
    ? html`<span class="tag tag--origin" title="${cell.tag.title}">${cell.tag.icon} ${cell.tag.label}</span>`
    : html`<span class="sr-only">origin</span>`;
  return html`${marker}${tag}${cell.leg ? legContent(cell.leg, txHref) : ""}`;
}

const CELL_CONTENT = {
  origin: originContent,
  leg: (cell, txHref) => legContent(cell, txHref),
  "not-sent": (cell) => badge(cell.view),
  relay: () => html`<span class="marker marker--relay" aria-hidden="true">◆</span>`,
  empty: () => html`<span class="marker marker--empty" aria-hidden="true">·</span>`,
};

function cell(model, txHref) {
  return html`<span class="cell cell--${model.kind} path-${model.path}"><span class="sr-only">${model.laneLabel}:</span>${CELL_CONTENT[model.kind](model, txHref)}</span>`;
}

function strip(items) {
  const parts = items.map(
    (item) => html`<span class="strip__item tone-${item.tone}" title="${item.title ?? item.label}">${item.peer}<span aria-hidden="true">${item.icon}</span><span class="sr-only"> ${item.label}</span></span>`,
  );
  return html`<span class="strip">${parts}</span>`;
}

function rowFlags(row) {
  return html`${row.incomplete ? html`<span class="tag tag--warn" title="Open during a monitor gap; some events may be missing">⚠ incomplete</span>` : ""}${row.problem ? html`<span class="tag tag--problem" title="A leg failed or was dropped">problem</span>` : ""}`;
}

/** The sticky header row naming every lane. */
export function laneHeader(lanes) {
  const heads = lanes.map((lane) => {
    const role = lane.role === "primary" ? html`<span class="lane-head__role">PRIMARY</span>` : "";
    return html`<span class="lane-head lane-head--${lane.role}" title="${lane.label}">${role}${lane.label}</span>`;
  });
  return html`<div class="lanes-head" aria-hidden="true"><span>time</span><span>action / headline</span>${heads}</div>`;
}

/**
 * One exchange row; `href` opens its detail drawer and `txHref` (optional) its session. The link
 * wraps the action and headline only, so the row can hold the session links; the view makes the
 * rest of the row follow the detail link on click.
 */
export function exchangeRow(row, href, txHref = null) {
  const classes = `row row--exchange${row.problem ? " row--problem" : ""}${row.incomplete ? " row--incomplete" : ""}`;
  return html`<li class="${classes}" data-id="${row.id}"><span class="row__time">${formatTime(row.startedAt)}</span><span class="row__what"><a class="row__link" href="${href}"><span class="row__action">${row.action}</span> <span class="row__headline">${row.headline ?? ""}</span></a>${txHref && row.transactionId !== null ? html` ${txLink(row.transactionId, txHref)}` : ""}${rowFlags(row)}</span>${row.cells.map((model) => cell(model, txHref))}${strip(row.strip)}</li>`;
}

/** The separator for a monitor gap. */
export function gapRow(gap) {
  return html`<li class="row row--gap" role="separator"><span class="gap">⚠ ${gap.dropped} monitor events lost ${formatTime(gap.from_at)}–${formatTime(gap.to_at)} · relaying unaffected</span></li>`;
}
