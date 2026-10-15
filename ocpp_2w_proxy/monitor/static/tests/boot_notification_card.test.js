import assert from "node:assert/strict";
import { test } from "node:test";

import { clockSkew, skewText } from "../cards/boot_notification.js";
import { renderCard } from "../cards/index.js";
import { replyFrame } from "../cards/reply.js";
import { loadFixture } from "./fixture_loader.js";

const detail = () => loadFixture("boot_notification_detail");
const card = (body, context) => String(renderCard("BootNotification", body.payload, context));

test("the reply is the one sent to the charger", () => {
  const frame = replyFrame(detail());
  assert.equal(frame.peer, "charger");
  assert.equal(frame.at, 1760537100.4);
  assert.equal(replyFrame({ frames: [] }), null);
  assert.equal(replyFrame(null), null);
});

test("clock skew is the reply's currentTime against the proxy's clock", () => {
  assert.ok(Math.abs(clockSkew(replyFrame(detail())) - 3.6) < 1e-6);
  assert.equal(clockSkew({ at: 1, payload: {} }), null);
  assert.equal(clockSkew({ at: 1, payload: { currentTime: "soon" } }), null);
  assert.equal(skewText(3.6), "4s ahead");
  assert.equal(skewText(-130), "2m10s behind");
  assert.equal(skewText(0.4), "in sync");
});

test("the card shows the charger identity, registration, interval and skew", () => {
  const markup = card(detail(), { detail: detail() });
  assert.match(markup, /<p class="boot-card__name">KEBA KC-P30<\/p>/);
  assert.match(markup, /<dt>serial number<\/dt><dd>99224881<\/dd>/);
  assert.match(markup, /<dt>firmware<\/dt><dd>1.11.2<\/dd>/);
  assert.match(markup, /<dt>ICCID<\/dt><dd>\*\*\*<\/dd>/, "masked values stay masked");
  assert.match(markup, /badge__label">Accepted<\/span><\/span><\/span><span class="figure__label">registration/);
  assert.match(markup, />5m00s<\/span><span class="figure__label">heartbeat interval/);
  assert.match(markup, />4s ahead<\/span><span class="figure__label">backend clock vs proxy/);
});

test("without a currentTime the skew is omitted", () => {
  const noClock = detail();
  for (const frame of noClock.frames) delete frame.payload.currentTime;
  const markup = card(noClock, { detail: noClock });
  assert.ok(!markup.includes("clock"));
  assert.match(markup, /registration/);
});

test("a CallError reply shows its code; a missing reply says so", () => {
  const failed = detail();
  failed.frames = [{ ...failed.frames[0] }, { at: 1, peer: "charger", dir: "out", msg_type: "error", wire_id: "a1c5", payload: {}, same_as_origin: false, error: { code: "InternalError", description: "backend down" } }];
  assert.match(card(failed, { detail: failed }), /title="backend down".*ERR InternalError/);
  const open = { ...detail(), frames: [] };
  assert.match(card(open, { detail: open }), /No reply recorded yet/);
  assert.ok(!card(detail(), {}).includes("Reply"), "no reply part without the detail");
});

test("identity fields are escaped", () => {
  const markup = String(renderCard("BootNotification", { chargePointVendor: "<v>", firmwareVersion: "<f>" }));
  assert.ok(!/<[vf]>/.test(markup));
});
