// Pure mapping of one leg to what its lane cell shows. Colour (tone) is never the only
// signal: every view has an icon and a word.

import { formatDuration } from "../format.js";

// The UI does not know call_timeout (it is not in /api/config); an open leg older than this
// can no longer end normally, so its end was lost in a monitor gap.
export const UNKNOWN_AFTER_SECONDS = 90;

// Reasons that put a leg in a queue; a leg delivered with one of them was delivered late.
const QUEUE_REASONS = new Set(["QUEUED", "KEPT_LATEST"]);

const view = (label, icon, tone, title = null) => ({ label, icon, tone, title });

function openView(leg, context) {
  const since = leg.sent_at ?? context.startedAt;
  const stale = since !== undefined && since !== null && context.now - since > UNKNOWN_AFTER_SECONDS;
  if (context.incomplete || stale) return view("unknown (monitor gap)", "?", "unknown");
  return view("pending", "…", "pending");
}

function deliveredView(leg, context) {
  if (QUEUE_REASONS.has(leg.reason) && leg.sent_at !== null && context.startedAt !== undefined) {
    return view(`delivered +${formatDuration(leg.sent_at - context.startedAt)}`, "✓", "delivered", leg.reason_text);
  }
  return view("delivered", "✓", "delivered", leg.reason_text);
}

function answeredByProxyView(leg) {
  if (leg.reply === "error") return view("refused", "✕", "refused", errorTitle(leg));
  return view("blocked", "⛔", "blocked", leg.reason_text);
}

function failedView(leg) {
  const icon = leg.reason === "CHARGER_NO_REPLY" ? "⏱" : "✕";
  return view("failed", icon, "failed", leg.reason_text);
}

function errorTitle(leg) {
  const code = leg.error_code ? `ERR ${leg.error_code}` : "ERR";
  return leg.reason_text ? `${code} · ${leg.reason_text}` : code;
}

const VIEWS = {
  delivered: deliveredView,
  answered_by_proxy: answeredByProxyView,
  queued: (leg) => view("queued", "⧗", "queued", leg.reason_text),
  withheld: (leg) => view("withheld", "⊣🔒", "withheld", leg.reason_text),
  dropped: (leg) => view("dropped", "⌫", "dropped", leg.reason_text),
  failed: failedView,
};

/**
 * What a leg's lane cell shows: {label, icon, tone, title}.
 * `context` holds the exchange's `startedAt` and `incomplete`, and `now` in epoch seconds.
 */
export function outcomeView(leg, context = {}) {
  const ctx = { incomplete: false, now: Date.now() / 1000, ...context };
  if (leg.replied_by === "peer" && leg.reply === "error") return view("failed", "✕", "failed", errorTitle(leg));
  if (leg.outcome === null || leg.outcome === undefined) return openView(leg, ctx);
  const build = VIEWS[leg.outcome];
  return build ? build(leg, ctx) : view(leg.outcome, "?", "unknown", leg.reason_text);
}

/** The cell of a secondary that is not configured for the action. */
export function notSentView() {
  return view("not sent", "—", "muted", "Not configured to forward this action");
}
