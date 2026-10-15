// Markup of the exchange detail drawer. Pure: the GET /api/exchanges/{id} body in, SafeHtml out.

import { renderCard } from "../cards/index.js";
import { diffPaths } from "../diff.js";
import { formatDateTime, formatLatency, formatTime } from "../format.js";
import { html } from "../html.js";
import { notSentView, outcomeView } from "./badge.js";
import { sequenceDiagram } from "./sequence.js";
import { badge, rewriteChip, txLink } from "./swimlane.js";
import { replyTags, rewriteChips } from "./swimlane_model.js";

function originText(detail) {
  if (detail.origin === "proxy") return `proxy (${detail.proxy_kind})`;
  return detail.origin;
}

function header(detail, txHref) {
  const transaction = detail.transaction_id !== null && txHref ? txLink(detail.transaction_id, txHref) : detail.transaction_id;
  const facts = [
    ["charger", detail.charger_id],
    ["origin", originText(detail)],
    ["started", formatDateTime(detail.started_at)],
    ["transaction", transaction],
    ["connector", detail.connector_id],
    ["idTag", detail.id_tag],
    ["exchange id", detail.id],
  ].filter(([, value]) => value !== null && value !== undefined);
  const incomplete = detail.incomplete
    ? html`<p class="notice" role="note">⚠ This exchange was open during a monitor gap; some of its events may be missing. Relaying was not affected.</p>`
    : "";
  return html`<dl class="facts">${facts.map(([label, value]) => html`<div><dt>${label}</dt><dd>${value}</dd></div>`)}</dl>${incomplete}`;
}

function replyText(leg) {
  if (leg.reply === null) return "–";
  const by = leg.replied_by === "proxy" ? " by proxy" : "";
  return leg.reply === "error" ? `ERR ${leg.error_code ?? ""}${by}` : `result${by}`;
}

function legRow(leg, context, txHref) {
  const extras = [
    ...rewriteChips(leg).map((chip) => rewriteChip(chip, txHref)),
    ...replyTags(leg).map((tag) => html`<span class="tag" title="${tag.title}">${tag.label}</span>`),
  ];
  return html`<tr><th scope="row">${leg.peer}</th><td class="mono">${leg.wire_id ?? "–"}</td><td>${formatTime(leg.sent_at)}</td><td>${replyText(leg)}</td><td>${formatLatency(leg.sent_at, leg.replied_at)}</td><td>${badge(outcomeView(leg, context))}</td><td>${leg.reason_text ?? ""}</td><td>${extras}</td></tr>`;
}

/** The legs table: one row per leg, then the peers the action is not forwarded to. */
export function legsTable(detail, now, txHref = null) {
  const context = { startedAt: detail.started_at, incomplete: detail.incomplete, now };
  const notSent = detail.not_sent.map(
    (peer) => html`<tr><th scope="row">${peer}</th><td colspan="4"></td><td>${badge(notSentView())}</td><td>Not configured to forward ${detail.action}</td><td></td></tr>`,
  );
  return html`<div class="table-scroll"><table class="legs"><thead><tr><th scope="col">peer</th><th scope="col">wire id</th><th scope="col">sent</th><th scope="col">reply</th><th scope="col">latency</th><th scope="col">outcome</th><th scope="col">reason</th><th scope="col">changes</th></tr></thead><tbody>${detail.legs.map((leg) => legRow(leg, context, txHref))}${notSent}</tbody></table></div>`;
}

/** Proxy decisions with their human text. */
export function decisionsList(decisions) {
  if (decisions.length === 0) return html`<p class="muted">The proxy made no decisions for this exchange.</p>`;
  const items = decisions.map((decision) => {
    const detailText = decision.detail === null || decision.detail === undefined ? "" : html` <span class="muted">(${JSON.stringify(decision.detail)})</span>`;
    return html`<li><span class="mono muted">${formatTime(decision.at)}</span> <span class="tag">${decision.reason}</span> ${decision.text}${detailText}</li>`;
  });
  return html`<ol class="decisions">${items}</ol>`;
}

function showJson(value) {
  return value === undefined ? "–" : JSON.stringify(value);
}

function legDiff(frame, origin) {
  const title = html`<h4>→ ${frame.peer} <span class="mono muted">${frame.wire_id ?? ""}</span></h4>`;
  if (frame.same_as_origin) return html`${title}<p class="muted">Identical to the origin.</p>`;
  const changes = diffPaths(origin, frame.payload);
  if (changes.length === 0) return html`${title}<p class="muted">Equal to the origin.</p>`;
  const rows = changes.map(
    (change) => html`<tr class="diff--${change.kind}"><th scope="row" class="mono">${change.path}</th><td>${change.kind}</td><td class="mono diff__from">${showJson(change.from)}</td><td class="mono diff__to">${showJson(change.to)}</td></tr>`,
  );
  return html`${title}<div class="table-scroll"><table class="diff"><thead><tr><th scope="col">path</th><th scope="col">change</th><th scope="col">origin</th><th scope="col">this leg</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

/** Each outgoing call compared with the origin payload. */
export function legDiffs(detail) {
  const calls = detail.frames.filter((frame) => frame.dir === "out" && frame.msg_type === "call");
  if (calls.length === 0) return html`<p class="muted">No calls were sent on.</p>`;
  return html`${calls.map((frame) => legDiff(frame, detail.payload))}`;
}

/** Pretty JSON of the whole detail, for the copy button. */
export function rawJsonText(detail) {
  return JSON.stringify(detail, null, 2);
}

function section(id, title, body) {
  return html`<section class="detail-section" aria-labelledby="${id}"><h3 id="${id}">${title}</h3>${body}</section>`;
}

/** Everything inside the drawer below its title bar; `txHref` (optional) links to the session. */
export function exchangeDetail({ detail, lanes, session = null, now, txHref = null }) {
  return html`${header(detail, txHref)}
${section("d-sequence", "Sequence", html`<div class="sequence-scroll">${sequenceDiagram(detail, lanes)}</div>`)}
${section("d-payload", "Payload", renderCard(detail.action, detail.payload, { detail, session, now }))}
${section("d-legs", "Legs", legsTable(detail, now, txHref))}
${section("d-decisions", "Decisions", decisionsList(detail.decisions))}
${section("d-diff", "Legs compared with the origin", legDiffs(detail))}
${section("d-raw", "Raw JSON", html`<button type="button" class="button" data-action="copy">Copy JSON</button> <span class="muted" data-part="copy-status" role="status"></span><pre class="raw-json"><code>${rawJsonText(detail)}</code></pre>`)}`;
}
