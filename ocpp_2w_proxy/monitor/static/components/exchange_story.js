// Pure story model of one exchange detail: the request, what each leg's peer received and
// answered, and what the requester got back. Frames keep their index in `detail.frames` so the
// views can link a block to its arrow in the sequence diagram.

import { PROXY_ORIGINS } from "./swimlane_model.js";

const REPLY_TYPES = new Set(["result", "error"]);

function indexed(frames) {
  return frames.map((frame, index) => ({ frame, index }));
}

function legStory(leg, frames, decisions) {
  const own = frames.filter(({ frame }) => frame.peer === leg.peer);
  return {
    peer: leg.peer,
    leg,
    call: own.find(({ frame }) => frame.dir === "out" && frame.msg_type === "call") ?? null,
    replies: own.filter(({ frame }) => frame.dir === "in" && REPLY_TYPES.has(frame.msg_type)),
    decisions: decisions.filter((decision) => decision.peer === leg.peer),
  };
}

// The indexes of the frames the story tells, so only their arrows link to a block.
function toldFrames({ request, legs, requesterReply }) {
  const entries = [request, requesterReply, ...legs.flatMap((leg) => [leg.call, ...leg.replies])];
  return new Set(entries.filter(Boolean).map((entry) => entry.index));
}

/**
 * {hasRequester, request, legs: [{peer, leg, call, replies, decisions}], notSent, requesterReply,
 * otherDecisions, told}; `request`, `call` and each reply are {frame, index} or null. A proxy or
 * debug origin has no requester. `otherDecisions` are those about a peer without a leg (the
 * requester, say); `told` is the set of frame indexes the story shows.
 */
export function exchangeStory(detail) {
  const frames = indexed(detail.frames);
  const legPeers = new Set(detail.legs.map((leg) => leg.peer));
  const hasRequester = !PROXY_ORIGINS.has(detail.origin);
  const fromRequester = hasRequester ? frames.filter(({ frame }) => frame.peer === detail.origin) : [];
  const story = {
    hasRequester,
    request: fromRequester.find(({ frame }) => frame.dir === "in" && frame.msg_type === "call") ?? null,
    legs: detail.legs.map((leg) => legStory(leg, frames, detail.decisions)),
    notSent: detail.not_sent,
    requesterReply: fromRequester.find(({ frame }) => frame.dir === "out" && REPLY_TYPES.has(frame.msg_type)) ?? null,
    otherDecisions: detail.decisions.filter((decision) => !legPeers.has(decision.peer)),
  };
  return { ...story, told: toldFrames(story) };
}
