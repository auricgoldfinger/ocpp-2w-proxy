import assert from "node:assert/strict";
import { test } from "node:test";

import { exchangeStory } from "../components/exchange_story.js";
import { loadFixture } from "./fixture_loader.js";

const indexes = (items) => items.map((item) => item.index);

test("the request and the requester's reply are the origin's frames", () => {
  const story = exchangeStory(loadFixture("exchange_detail"));
  assert.equal(story.request.index, 0);
  assert.equal(story.request.frame.msg_type, "call");
  assert.equal(story.requesterReply.index, 1);
  assert.deepEqual(story.requesterReply.frame.payload, { idTagInfo: { status: "Accepted" } });
});

test("each leg has its call, replies and decisions, in leg order", () => {
  const story = exchangeStory(loadFixture("exchange_detail"));
  assert.deepEqual(story.legs.map((leg) => leg.peer), ["primary", "homeassistant", "stats"]);
  const [primary, homeassistant] = story.legs;
  assert.equal(primary.call.index, 6);
  assert.deepEqual(indexes(primary.replies), [7]);
  assert.deepEqual(primary.decisions.map((decision) => decision.reason), ["QUEUED"]);
  assert.equal(homeassistant.call.frame.wire_id, "7f3a09");
  assert.deepEqual(homeassistant.decisions, []);
  assert.equal(primary.leg.reason, "QUEUED");
});

test("a CallError, a late duplicate and a queued leg each keep their frames", () => {
  const story = exchangeStory(loadFixture("call_error_detail"));
  const [primary, homeassistant, backup] = story.legs;
  assert.equal(primary.replies[0].frame.error.code, "FormationViolation");
  assert.deepEqual(indexes(homeassistant.replies), [5, 6]);
  assert.equal(backup.call, null);
  assert.deepEqual(backup.replies, []);
  assert.equal(backup.decisions.length, 1);
  assert.deepEqual(story.notSent, ["stats"]);
  assert.equal(story.requesterReply.frame.msg_type, "error");
});

test("a backend origin's request and reply are that backend's frames", () => {
  const story = exchangeStory(loadFixture("change_configuration_detail"));
  assert.equal(story.request.frame.peer, "primary");
  assert.equal(story.requesterReply.index, 3);
  assert.equal(story.legs[0].peer, "charger");
  assert.deepEqual(indexes(story.legs[0].replies), [2]);
});

test("the story tells every frame of the fixtures", () => {
  for (const name of ["exchange_detail", "call_error_detail", "change_configuration_detail", "meter_values_fanout_detail"]) {
    const detail = loadFixture(name);
    assert.deepEqual([...exchangeStory(detail).told].sort((a, b) => a - b), detail.frames.map((_, index) => index), name);
  }
});

test("a frame the story does not tell is left out of `told`", () => {
  const detail = loadFixture("change_configuration_detail");
  detail.frames.push({ ...detail.frames[1], at: detail.frames[1].at + 1 });
  assert.ok(!exchangeStory(detail).told.has(4), "a second call to the same peer");
});

test("a proxy or debug origin has no requester", () => {
  for (const origin of ["proxy", "debug"]) {
    const story = exchangeStory({ ...loadFixture("change_configuration_detail"), origin });
    assert.equal(story.hasRequester, false);
    assert.equal(story.request, null);
    assert.equal(story.requesterReply, null);
  }
});

test("decisions about a peer without a leg are kept apart", () => {
  const detail = loadFixture("exchange_detail");
  assert.deepEqual(exchangeStory(detail).otherDecisions, []);
  detail.decisions.push({ at: detail.started_at, peer: "charger", reason: "SYNTHETIC_REPLY", text: "Answered locally", detail: null });
  assert.deepEqual(exchangeStory(detail).otherDecisions.map((decision) => decision.peer), ["charger"]);
});
