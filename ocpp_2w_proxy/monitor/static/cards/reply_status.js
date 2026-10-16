// The status pill of an OCPP 1.6 confirmation. One table covers every status enum of the
// confirmations, because a word means the same in each (Accepted, Rejected, NotSupported, …).

import { badge } from "../components/swimlane.js";
import { statusView } from "./status_view.js";

const REPLY_STATUSES = {
  Accepted: { icon: "✓", tone: "up" },
  Unlocked: { icon: "✓", tone: "up" },
  Pending: { icon: "⧗", tone: "queued" },
  Scheduled: { icon: "⧗", tone: "queued" },
  RebootRequired: { icon: "↻", tone: "queued" },
  Rejected: { icon: "✕", tone: "failed" },
  Failed: { icon: "✕", tone: "failed" },
  Faulted: { icon: "✕", tone: "failed" },
  UnlockFailed: { icon: "✕", tone: "failed" },
  Invalid: { icon: "✕", tone: "failed" },
  Blocked: { icon: "⛔", tone: "blocked" },
  Expired: { icon: "⌛", tone: "blocked" },
  ConcurrentTx: { icon: "⇉", tone: "blocked" },
  Occupied: { icon: "⊘", tone: "blocked" },
  Unavailable: { icon: "⊘", tone: "muted" },
  NotSupported: { icon: "⊘", tone: "refused" },
  NotImplemented: { icon: "⊘", tone: "refused" },
  VersionMismatch: { icon: "≠", tone: "refused" },
  UnknownMessageId: { icon: "?", tone: "refused" },
  UnknownVendorId: { icon: "?", tone: "refused" },
  Unknown: { icon: "?", tone: "muted" },
};

/** The pill of a confirmation status: icon and word, never colour alone. */
export function statusPill(status) {
  return badge(statusView(status, REPLY_STATUSES));
}
