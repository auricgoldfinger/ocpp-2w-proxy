// Markup of the Links view: per charger the peer links, their queues, rates and 24 h history,
// then the monitor's own health. Pure: the GET /api/links body in, SafeHtml out.

import { formatCount, formatDateTime, formatDuration, formatRate, formatTime } from "../format.js";
import { html } from "../html.js";
import { downtimeText, HISTORY_SECONDS, linkSegments, outages, timelineBar } from "./link_history.js";

// A blip is a sliver on the 24 h bar, so the newest outages are listed in words as well.
const LISTED_OUTAGES = 10;

function state(up, since, now) {
  const age = since ? ` for ${formatDuration(now - since)}` : "";
  return up
    ? html`<span class="state state--up"><span aria-hidden="true">●</span> up${age}</span>`
    : html`<span class="state state--down"><span aria-hidden="true">✕</span> down${age}</span>`;
}

function outageList(segments) {
  const all = outages(segments);
  if (all.length === 0) return "";
  const items = all.slice(0, LISTED_OUTAGES).map((outage) => {
    const duration = outage.to - outage.from;
    const reason = outage.note ?? "reason not recorded";
    const recovery = outage.recovery ? html` · <span class="muted">${outage.recovery}</span>` : "";
    return html`<li><time>${formatDateTime(outage.from)}</time> · ${formatDuration(duration)} · ${reason}${recovery}</li>`;
  });
  const more = all.length > LISTED_OUTAGES ? html`<li class="muted">and ${all.length - LISTED_OUTAGES} earlier</li>` : "";
  return html`<ul class="outages">${items}${more}</ul>`;
}

function peerRow(chargerId, peer, history, now) {
  const range = { from: now - HISTORY_SECONDS, now };
  const changes = history.filter((change) => change.charger_id === chargerId && change.peer === peer.name);
  const segments = linkSegments(changes, { up: peer.up, ...range });
  const role = peer.role === "primary" ? html`<span class="role">PRIMARY</span> ` : "";
  const queued = peer.queued > 0 ? html`<span class="queue">⧗ ${peer.queued}</span>` : html`<span class="muted">0</span>`;
  return html`<tr><th scope="row">${role}${peer.name}</th><td>${state(peer.up, peer.since, now)}</td><td>${queued}</td><td>${formatRate(peer.messages_last_hour)}</td><td class="timeline-cell">${timelineBar(segments, range)}<span class="muted">${downtimeText(segments)}</span>${outageList(segments)}</td></tr>`;
}

function chargerSection(charger, history, now) {
  const drops = charger.monitor.dropped > 0
    ? html`<span class="state state--down">⚠ ${formatCount(charger.monitor.dropped)} monitor events dropped, last at ${formatTime(charger.monitor.last_drop_at)}</span>`
    : html`<span class="muted">no monitor events dropped</span>`;
  return html`<section class="link-card" aria-labelledby="charger-${charger.id}">
<h2 id="charger-${charger.id}">Charger ${charger.id} ${state(charger.connected, charger.since, now)}</h2>
<p>${drops}</p>
<div class="table-scroll"><table class="links-table"><thead><tr><th scope="col">peer</th><th scope="col">link</th><th scope="col">queue</th><th scope="col">messages</th><th scope="col">last 24 h</th></tr></thead>
<tbody>${charger.peers.map((peer) => peerRow(charger.id, peer, history, now))}</tbody></table></div>
</section>`;
}

function gapsTable(gaps) {
  if (gaps.length === 0) return html`<p class="muted">No monitor gaps in the last 24 h.</p>`;
  const rows = [...gaps]
    .sort((a, b) => b.from_at - a.from_at)
    .map((gap) => html`<tr><td>${gap.charger_id}</td><td>${formatDateTime(gap.from_at)}–${formatTime(gap.to_at)}</td><td>${formatCount(gap.dropped)}</td></tr>`);
  return html`<div class="table-scroll"><table><thead><tr><th scope="col">charger</th><th scope="col">period</th><th scope="col">events lost</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function count(label, value, help) {
  const tone = value > 0 ? "state--down" : "muted";
  return html`<div class="figure"><span class="figure__value ${tone}">${formatCount(value)}</span><span class="figure__label">${label}</span><span class="figure__help">${help}</span></div>`;
}

function monitorHealth(links) {
  return html`<section class="link-card" aria-labelledby="monitor-health">
<h2 id="monitor-health">Monitor health</h2>
<p class="muted">The monitor drops its own events rather than slow down relaying. Relaying is never affected by these numbers.</p>
<div class="figures">
${count("monitor events dropped", links.chargers.reduce((sum, charger) => sum + charger.monitor.dropped, 0), "queue full; shown as gaps")}
${count("monitor gaps (24 h)", links.gaps.length, "periods with lost events")}
${count("recorder failures", links.monitor.fanout_failures, "exceptions caught in the relay hook")}
${count("dropped write batches", links.monitor.dropped_batches, "database errors")}
</div>
<h3>Gaps</h3>
${gapsTable(links.gaps)}
</section>`;
}

/** The whole Links view body. */
export function linksPanel(links, now) {
  if (links.chargers.length === 0) return html`<p class="empty">No charger has connected yet.</p>${monitorHealth(links)}`;
  return html`${links.chargers.map((charger) => chargerSection(charger, links.history, now))}${monitorHealth(links)}`;
}
