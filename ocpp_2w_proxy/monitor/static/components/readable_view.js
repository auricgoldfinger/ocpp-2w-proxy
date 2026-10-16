// The drawer's Readable view: the exchange as a story without any JSON. The request, then one
// block per leg with what its peer received and answered, then what the requester got back.
// Blocks carry `data-frame-target` (the frame index) so a sequence arrow can jump to them.

import { renderCard } from "../cards/index.js";
import { responseView } from "../cards/responses.js";
import { formatLatency, formatTime } from "../format.js";
import { html } from "../html.js";
import { notSentView, outcomeView } from "./badge.js";
import { exchangeStory } from "./exchange_story.js";
import { legDiff } from "./leg_diff.js";
import { sequenceDiagram } from "./sequence.js";
import { badge, rewriteChip } from "./swimlane.js";
import { replyTags, rewriteChips } from "./swimlane_model.js";

const PROXY_ORIGINS = new Set(["proxy", "debug"]);

function section(id, title, body) {
  return html`<section class="detail-section story" aria-labelledby="${id}"><h3 id="${id}">${title}</h3>${body}</section>`;
}

/** A focusable target for the sequence arrow of frame `index`; no target without a frame. */
function frameTarget(entry) {
  return entry ? html` data-frame-target="${entry.index}" tabindex="-1"` : "";
}

function decisionItems(decisions) {
  if (decisions.length === 0) return "";
  const items = decisions.map(
    (decision) => html`<li><span aria-hidden="true">◆</span> <span class="tag">${decision.reason}</span> ${decision.text} <span class="mono muted">${formatTime(decision.at)}</span></li>`,
  );
  return html`<ul class="story-decisions" aria-label="Proxy decisions">${items}</ul>`;
}

function received(story, detail) {
  if (!story.call) return html`<p class="muted">Nothing was sent to ${story.peer}.</p>`;
  return legDiff(story.call.frame, detail.payload);
}

function replyBlock(reply, at, count, action) {
  const label = count > 1 ? html`<p class="leg-reply__label">Reply ${at + 1} of ${count} · ${formatTime(reply.frame.at)}</p>` : "";
  return html`<div class="leg-reply"${frameTarget(reply)}>${label}${responseView(action, reply.frame)}</div>`;
}

/** What the peer answered, or why there is no answer; "" when nothing was sent to answer. */
function answered(story, action) {
  const { leg, replies } = story;
  if (replies.length > 0) return html`${replies.map((reply, at) => replyBlock(reply, at, replies.length, action))}`;
  if (leg.replied_by === "proxy") {
    return html`<p><span aria-hidden="true">⚙</span> Answered by the proxy${leg.reason_text ? html`: ${leg.reason_text}` : "."}</p>`;
  }
  if (!story.call) return "";
  if (leg.outcome === null) return html`<p class="muted"><span aria-hidden="true">…</span> Waiting for a reply.</p>`;
  return html`<p><span aria-hidden="true">⏱</span> No reply${leg.reason_text ? html`: ${leg.reason_text}` : "."}</p>`;
}

function legMeta(leg, context, txHref) {
  const latency = leg.replied_at === null ? "" : html` <span class="muted">answered in ${formatLatency(leg.sent_at, leg.replied_at)}</span>`;
  const chips = rewriteChips(leg).map((chip) => rewriteChip(chip, txHref));
  const tags = replyTags(leg).map((tag) => html`<span class="tag" title="${tag.title}">${tag.label}</span>`);
  return html`<p class="leg-block__meta">${badge(outcomeView(leg, context))}${latency}${chips}${tags}</p>`;
}

function legBlock(story, at, detail, context, txHref) {
  const id = `r-leg-${at}`;
  const answer = answered(story, detail.action);
  return html`<article class="leg-block" aria-labelledby="${id}"><h4 id="${id}"${frameTarget(story.call)}><span aria-hidden="true">→</span> ${story.peer} <span class="mono muted">${story.leg.wire_id ?? ""}</span></h4>${legMeta(story.leg, context, txHref)}<h5>Received</h5>${received(story, detail)}${answer ? html`<h5>Answered</h5>${answer}` : ""}${decisionItems(story.decisions)}</article>`;
}

function notSentList(peers, action) {
  if (peers.length === 0) return "";
  return html`<p class="story-not-sent">${badge(notSentView())} ${peers.join(", ")}: not configured to forward ${action}.</p>`;
}

function legsBody(story, detail, context, txHref) {
  const blocks = story.legs.length === 0
    ? html`<p class="muted">No peer was sent this message.</p>`
    : html`<div class="leg-blocks">${story.legs.map((leg, at) => legBlock(leg, at, detail, context, txHref))}</div>`;
  return html`${blocks}${notSentList(story.notSent, detail.action)}${decisionItems(story.otherDecisions)}`;
}

function requestBody(story, detail, session, now) {
  return html`<div${frameTarget(story.request)}>${renderCard(detail.action, detail.payload, { session, now })}</div>`;
}

function requesterBody(story, action) {
  if (!story.requesterReply) return html`<p class="muted">Nothing was sent back yet.</p>`;
  return html`<div class="leg-reply"${frameTarget(story.requesterReply)}>${responseView(action, story.requesterReply.frame)}</div>`;
}

/** The Readable panel's content; `txHref` (optional) links the ⇄tx chips to the session. */
export function readableView({ detail, lanes, session = null, now, txHref = null }) {
  const story = exchangeStory(detail);
  const context = { startedAt: detail.started_at, incomplete: detail.incomplete, now };
  const fromProxy = PROXY_ORIGINS.has(detail.origin);
  const requester = fromProxy ? "" : section("r-back", `3 · What ${detail.origin} got back`, requesterBody(story, detail.action));
  return html`${section("r-sequence", "Sequence", html`<div class="sequence-scroll" data-key="sequence">${sequenceDiagram(detail, lanes)}</div>`)}
${section("r-request", fromProxy ? `1 · Request from the ${detail.origin}` : `1 · Request from ${detail.origin}`, requestBody(story, detail, session, now))}
${section("r-legs", fromProxy ? "2 · Sent to" : "2 · Forwarded to", legsBody(story, detail, context, txHref))}
${requester}`;
}
