// Shape checks for the JSON in docs/monitor_api.md, shared by the contract tests.

import assert from "node:assert/strict";

const SUMMARY_KEYS = [
  "action", "charger_id", "connector_id", "headline", "id", "id_tag", "incomplete", "last_at", "legs",
  "not_sent", "origin", "outcome", "problem", "started_at", "transaction_id",
].sort();
const LEG_KEYS = [
  "duplicate", "error_code", "late", "outcome", "peer", "replied_at", "replied_by", "reason", "reason_text",
  "reply", "rewritten", "sent_at", "wire_id",
].sort();
const OUTCOMES = [null, "delivered", "answered_by_proxy", "queued", "withheld", "dropped", "failed"];

export function assertSummary(summary) {
  const expected = summary.origin === "proxy" ? [...SUMMARY_KEYS, "proxy_kind"].sort() : SUMMARY_KEYS;
  assert.deepEqual(Object.keys(summary).sort(), expected, `summary ${summary.id}`);
  assert.equal(typeof summary.id, "string");
  assert.ok(OUTCOMES.includes(summary.outcome));
  if (summary.origin === "proxy") assert.ok(["replay", "synthetic"].includes(summary.proxy_kind));
  for (const leg of summary.legs) {
    assert.deepEqual(Object.keys(leg).sort(), LEG_KEYS, `leg ${summary.id}/${leg.peer}`);
    assert.ok(OUTCOMES.includes(leg.outcome));
    assert.ok([null, "result", "error"].includes(leg.reply));
    assert.ok([null, "peer", "proxy"].includes(leg.replied_by));
    for (const change of leg.rewritten) {
      assert.deepEqual(Object.keys(change).sort(), ["field", "from", "to"]);
      assert.ok(["id", "tx", "profile"].includes(change.field));
    }
  }
}
