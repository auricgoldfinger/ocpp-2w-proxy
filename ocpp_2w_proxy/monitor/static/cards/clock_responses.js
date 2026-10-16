// BootNotification and Heartbeat confirmations: both carry the backend's currentTime. The clock
// skew compares it with when the proxy handled that reply; it is left out when either is unknown.

import { formatDuration, formatIsoDateTime } from "../format.js";
import { figure, figures } from "./figure.js";
import { statusPill } from "./reply_status.js";

// Below this the two clocks agree as far as the frame timestamps can tell.
const IN_SYNC_SECONDS = 1;

/** Backend clock minus proxy clock in seconds, from the reply frame; null when unknown. */
export function clockSkew(frame) {
  const sent = typeof frame?.payload?.currentTime === "string" ? Date.parse(frame.payload.currentTime) : Number.NaN;
  if (Number.isNaN(sent) || typeof frame.at !== "number") return null;
  return sent / 1000 - frame.at;
}

/** "in sync", "4s ahead" or "2m10s behind". */
export function skewText(seconds) {
  if (Math.abs(seconds) < IN_SYNC_SECONDS) return "in sync";
  return `${formatDuration(Math.abs(seconds))} ${seconds > 0 ? "ahead" : "behind"}`;
}

function skewFigure(frame) {
  const skew = clockSkew(frame);
  return skew !== null && figure("backend clock vs proxy", skewText(skew));
}

export function registration(frame) {
  const reply = frame.payload ?? {};
  return figures([
    figure("registration", statusPill(reply.status)),
    typeof reply.interval === "number" && figure("heartbeat interval", formatDuration(reply.interval)),
    skewFigure(frame),
  ]);
}

export function heartbeat(frame) {
  return figures([figure("backend time", formatIsoDateTime(frame.payload?.currentTime)), skewFigure(frame)]);
}
