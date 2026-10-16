// The reply a card describes, taken from the exchange detail's frames. It is the reply on the
// charger side: what the charger got for its own call, or what it answered to a backend's call.

const REPLY_TYPES = new Set(["result", "error"]);

/** The charger-side reply frame of `detail`, else any reply frame, else null. */
export function replyFrame(detail) {
  const replies = (detail?.frames ?? []).filter((frame) => REPLY_TYPES.has(frame.msg_type));
  return replies.find((frame) => frame.peer === "charger") ?? replies[0] ?? null;
}
