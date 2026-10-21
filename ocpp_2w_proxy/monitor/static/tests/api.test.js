import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ApiError, getJson } from "../api.js";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

const answering = (status, body) => {
  globalThis.fetch = async () => new Response(body, { status });
};

test("an error answer carries the API's error code", async () => {
  answering(503, JSON.stringify({ error: "query_too_slow", message: "narrow the filters" }));
  await assert.rejects(getJson("/api/exchanges"), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 503);
    assert.equal(error.code, "query_too_slow");
    return true;
  });
});

test("an error answer that is not JSON has no code", async () => {
  answering(502, "<html>Bad Gateway</html>");
  await assert.rejects(getJson("/api/exchanges"), { status: 502, code: null });
});
