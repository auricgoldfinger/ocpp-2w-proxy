import assert from "node:assert/strict";
import { test } from "node:test";

import { sequenceDiagram } from "../components/sequence.js";
import { lanesFor } from "../components/swimlane_model.js";
import { loadFixture } from "./fixture_loader.js";

const lanes = lanesFor(loadFixture("config"));
const detail = () => loadFixture("exchange_detail");

const actors = (svg) => [...svg.matchAll(/class="seq-actor"[^>]*>([^<]+)</g)].map((match) => match[1]);

test("participants follow the lane order", () => {
  assert.deepEqual(actors(String(sequenceDiagram(detail(), lanes))), ["charger", "proxy", "primary", "homeassistant", "stats"]);
});

test("every frame is an arrow and every decision a note, in time order", () => {
  const svg = String(sequenceDiagram(detail(), lanes));
  assert.equal(svg.match(/class="seq-msg /g).length, 8);
  assert.equal(svg.match(/class="seq-head seq-head--filled"/g).length, 4, "calls have filled heads");
  assert.equal(svg.match(/class="seq-head seq-head--open"/g).length, 4, "results have open heads");
  assert.match(svg, /◆ QUEUED → primary/);
  assert.match(svg, /<title>Queued for primary during an outage; acknowledged locally<\/title>/);
  assert.ok(svg.indexOf("QUEUED") < svg.indexOf("wire id 7f3a09"), "the decision precedes the later frames");
  assert.match(svg, /\+4m12s/);
});

test("a CallError shows a cross and its code", () => {
  const withError = detail();
  withError.frames[5] = { ...withError.frames[5], msg_type: "error", error: { code: "GenericError", description: "x" } };
  const svg = String(sequenceDiagram(withError, lanes));
  assert.match(svg, /class="seq-cross"/);
  assert.match(svg, />ERR GenericError</);
});

test("only participants that took part are drawn", () => {
  const blocked = { ...detail(), origin: "homeassistant", frames: [], decisions: [] };
  const svg = String(sequenceDiagram(blocked, lanes));
  assert.deepEqual(actors(svg), ["proxy", "homeassistant"]);
  assert.match(svg, /No frames recorded/);
});

test("each linked arrow is a keyboard link to its frame; others are plain", () => {
  const svg = String(sequenceDiagram(detail(), lanes, new Set([0, 1, 2, 3, 4, 5, 6, 7])));
  const frames = [...svg.matchAll(/<g class="seq-msg [^"]*" data-frame="(\d+)" data-key="seq-\d+" tabindex="0" role="link">/g)].map((match) => Number(match[1]));
  assert.deepEqual([...frames].sort((a, b) => a - b), [0, 1, 2, 3, 4, 5, 6, 7]);
  assert.match(svg, /<svg class="sequence"[^>]*role="group"/, "a group, so its links are not hidden as an image");
  const plain = String(sequenceDiagram(detail(), lanes, new Set([0])));
  assert.equal(plain.match(/role="link"/g).length, 1);
});
