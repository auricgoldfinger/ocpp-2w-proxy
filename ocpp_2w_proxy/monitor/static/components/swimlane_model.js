// Pure swimlane row model: which lane shows what for one exchange summary.
// Lanes run left to right: Charger, Proxy, the primary, then each secondary.

import { notSentView, outcomeView } from "./badge.js";

const PROXY = "proxy";
const PROXY_ORIGINS = new Set(["proxy", "debug"]);

const ORIGIN_TAGS = {
  replay: { icon: "↻", label: "replay", title: "Replayed by the proxy" },
  synthetic: { icon: "✦", label: "synthetic", title: "Made up by the proxy" },
  debug: { icon: "⌘", label: "debug", title: "Sent from the debug endpoint" },
};

const REWRITE_LABELS = { id: "⇄id", tx: "⇄tx", profile: "⇄profile" };

/** Lanes from /api/config, plus any peer seen in `exchanges` that the config no longer lists. */
export function lanesFor(config, exchanges = []) {
  const lanes = [
    { key: "charger", label: "Charger", role: "charger" },
    { key: PROXY, label: "Proxy", role: "proxy" },
    { key: config.primary, label: config.primary, role: "primary" },
    ...config.secondaries.map(({ name }) => ({ key: name, label: name, role: "secondary" })),
  ];
  const known = new Set(lanes.map((lane) => lane.key));
  for (const exchange of exchanges) {
    for (const peer of [...exchange.legs.map((leg) => leg.peer), ...exchange.not_sent]) {
      if (known.has(peer)) continue;
      known.add(peer);
      lanes.push({ key: peer, label: peer, role: "secondary" });
    }
  }
  return lanes;
}

function originLane(summary) {
  return PROXY_ORIGINS.has(summary.origin) ? PROXY : summary.origin;
}

function originTag(summary) {
  if (summary.origin === "debug") return ORIGIN_TAGS.debug;
  if (summary.origin === "proxy") return ORIGIN_TAGS[summary.proxy_kind] ?? null;
  return null;
}

function showValue(value) {
  if (value === null || value === undefined) return "–";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** ⇄ chips for a leg; the title shows the change, "from → –" when the field was removed. */
export function rewriteChips(leg) {
  return leg.rewritten.map(({ field, from, to }) => ({
    field,
    label: REWRITE_LABELS[field] ?? `⇄${field}`,
    title: `${showValue(from)} → ${showValue(to)}`,
  }));
}

/** "late" and "dup" tags for a leg's reply. */
export function replyTags(leg) {
  const tags = [];
  if (leg.late) tags.push({ label: "late", title: "Late reply, discarded by the proxy" });
  if (leg.duplicate) tags.push({ label: "dup", title: "Duplicate reply to the same message id" });
  return tags;
}

// A leg whose call reached its peer (or is on its way) extends the drawn path to that lane.
function reachedPeer(leg) {
  return leg.outcome === "delivered" || leg.replied_by === "peer" || (leg.outcome === null && leg.sent_at !== null);
}

function pathSpan(lanes, summary) {
  const index = (key) => lanes.findIndex((lane) => lane.key === key);
  const ends = [index(originLane(summary)), index(PROXY)];
  for (const leg of summary.legs) if (reachedPeer(leg)) ends.push(index(leg.peer));
  const valid = ends.filter((at) => at >= 0);
  return [Math.min(...valid), Math.max(...valid)];
}

/** Where a lane sits on the drawn path: "start" and "end" are half lines from the lane centre. */
function pathPart(at, first, last) {
  if (at < first || at > last) return "none";
  if (at === first) return "start";
  return at === last ? "end" : "through";
}

function legCell(leg, context) {
  return { kind: "leg", view: outcomeView(leg, context), chips: rewriteChips(leg), tags: replyTags(leg) };
}

function laneCell(lane, summary, context) {
  const leg = summary.legs.find((candidate) => candidate.peer === lane.key);
  if (lane.key === originLane(summary)) {
    return { kind: "origin", tag: originTag(summary), leg: leg ? legCell(leg, context) : null };
  }
  if (leg) return legCell(leg, context);
  if (summary.not_sent.includes(lane.key)) return { kind: "not-sent", view: notSentView() };
  if (lane.key === PROXY) return { kind: "relay" };
  return { kind: "empty" };
}

function stripItems(lanes, cells) {
  return cells
    .map((cell, at) => ({ lane: lanes[at], cell: cell.kind === "origin" ? cell.leg : cell }))
    .filter(({ cell }) => cell && (cell.kind === "leg" || cell.kind === "not-sent"))
    .map(({ lane, cell }) => ({ peer: lane.label, ...cell.view }));
}

/** Everything the row of one exchange shows; `now` is epoch seconds. */
export function rowModel(summary, lanes, now) {
  const context = { startedAt: summary.started_at, incomplete: summary.incomplete, now };
  const [first, last] = pathSpan(lanes, summary);
  const cells = lanes.map((lane, at) => ({
    lane: lane.key,
    laneLabel: lane.label,
    path: pathPart(at, first, last),
    ...laneCell(lane, summary, context),
  }));
  return {
    id: summary.id,
    startedAt: summary.started_at,
    action: summary.action,
    headline: summary.headline,
    transactionId: summary.transaction_id ?? null,
    outcome: summary.outcome,
    problem: summary.problem,
    incomplete: summary.incomplete,
    cells,
    strip: stripItems(lanes, cells),
  };
}
