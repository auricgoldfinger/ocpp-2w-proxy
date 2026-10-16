# Plan: OCPP Flow Monitor (live web UI built into the proxy)

## Context

Today the proxy only logs **wire frames** as text (`traffic_log.py`). You can see that a message went
somewhere, but not **why** a message did *not* go somewhere. Cases like these are invisible:
- a Command answered by the proxy (canned reply or policy refusal);
- a call withheld from the primary (unauthorized idTag);
- a message queued during an outage or dropped on overflow;
- a secondary that is not configured for an action;
- a transactionId that was rewritten.

Correlating legs by eye is also hard, because message ids are rewritten per leg.

**Goal:** an optional, read-only web Monitor inside the proxy. It shows each **Exchange** (one logical
message and all its legs) as a swimlane graph, makes it clear what was delivered, blocked, refused,
queued or not sent, and renders payloads in a readable form. **The monitor must never slow down relaying
between the charger and the backends.**

**Decisions made with the owner**
- Live, built into the proxy.
- SQLite store with 90-day retention and **no size cap**.
- No-build frontend: static HTML and vanilla ES modules, nothing loaded at runtime.
- Reachable on the LAN: listen address configurable, GET-only, masked data, warning when not on loopback.
- "Not sent" stored per exchange.
- **Relay latency has absolute priority over the monitor.** When there is a conflict, the monitor loses
  data; the relay never waits.

**How the plan was produced:**
1. The business analyst, functional analyst and UI/UX expert each proposed a design.
2. The senior developer critiqued it and designed the architecture.
3. The UI/UX expert and functional analyst answered the critique.
4. A **performance expert** reviewed the result against the relay code.
5. The senior developer and functional analyst revised the plan based on that review.

Outcomes:

| Dispute | Resolution |
|---|---|
| Topology graph in the MVP? | **Yes, a static SVG.** No animated pulses; those are Could. |
| 13 outcomes / 40 reason codes | **6 outcomes, 26 reason codes** (closed enum, each with outcome and human text). |
| Are policy refusals `failed`? | **No.** `answered_by_proxy`. The UI shows **blocked** for a canned CallResult and **refused** for a proxy CallError. Neither raises the problem flag. |
| Record "filtered" secondaries? | **Yes, compactly:** a `not_sent` column, **derived in the writer** from config. There is no hook on the relay path. |
| Separate Live / History / Sessions views | **Merged.** One Exchanges view. A Session is a `?tx=` filter with a server-side summary header. |
| Store every leg payload | Only when it differs from the origin (`is` fast path, `==` fallback), zlib-compressed in the worker thread from 200 bytes up (smaller ones gain nothing). |
| uuid vs counter exchange id (perf) | **Integer counter** with a random per-process base: about 50 ns, unique across restarts, opaque. |
| Monitor event loss under load (perf) | Allowed, but **made visible**: a `monitor_gaps` table, an `incomplete` flag and a gap row in the UI. |

## Performance budget (hard rules; tests enforce them)

The relay path is everything between reading a frame and `ws.send`. The hooks on it are `received`,
`sent`, `decided` and `link_changed`.
- **Nothing expensive on the relay path from monitor code:** no `await`, I/O, JSON, `dict`/`list`
  copy, f-string, `len()`, uuid, lock or redaction.
- **About 1 µs or less per hook when enabled:** one `time.time()`, one tuple and one
  `queue.put_nowait`.
- **Monitor disabled:** no monitor objects exist. The only remaining cost is the `exchange_id` counter
  in `Call` (about 50 ns).
- **Payloads are immutable once parsed.** The hot path only keeps references to them. Every rewrite
  already builds a new dict (`transactions.py:164`, `policy.py:205`), and that must stay so.
- **Monitor failures never reach the relay.** Every exception is caught in `FanOut`, counted, and logged
  at most once a minute.
- **No loop callback from monitor code may take longer than 5 ms.** The writer and the API slice their
  work, and run CPU-heavy work that releases the GIL (zlib, sqlite) in dedicated threads.
- **Relay first, monitor second.** When the queue is full, events are dropped and counted. The relay
  never waits.

**Measured on back-to-back round trips** (the loop never idle). The writer costs about 7 µs of loop
time per event. Its tail cost came from two places. First, it ran a slice of up to 50 events in every
loop iteration, and a round trip spans about ten iterations. Second, the write worker took the GIL back
about 1300 times per batch, once per `executemany` row and once per zlib call; that stretched each
write from about 2 ms to 30–50 ms, and the loop stalled behind those handoffs. Time-budgeted, paced
slices and multi-row writes with small payloads left uncompressed brought the p99 gap from about
+1.5 ms to under +0.5 ms.

The performance expert also found an existing stall unrelated to the monitor: `StateStore._write`
runs `json.dumps` and `fsync` on the loop (`state.py:170-179`), on the StartTransaction path. It goes on
the ROADMAP as a separate follow-up.

## Domain model (glossary additions)

- **Exchange**: one logical message from its origin (`charger | primary | <secondary> | proxy | debug`)
  until every leg has ended.
- **Leg**: the hop to one peer. It records the wire id, outcome, reason, reply kind and who replied
  (`peer | proxy`).
- **Flow Event**: an append-only fact: frame, decision or link change.
- **Decision**: why a leg ended the way it did.
- **Monitor gap**: a period in which the monitor lost events. Traffic was relayed normally during it.

**Outcomes** (closed): `delivered`, `answered_by_proxy`, `queued`, `withheld`, `dropped`, `failed`.

Derived, not stored as outcomes:
- *rewritten*: the leg payload is stored only when it differs from the origin. Chips come from a field
  diff: ⇄id, ⇄tx (`10216 → 1791822333`, or `10216 → –` when removed) and ⇄profile when a
  chargingProfile is stripped.
- *replayed* and *synthetic*: the origin is `proxy`.
- *delivered late*: queued, then delivered.
- *late reply*: a reply that arrives after CHARGER_NO_REPLY. It is shown as "late, discarded by proxy".
- *duplicate reply*: a second reply to the same wire id.
- *incomplete*: the exchange was open during a monitor gap. It does **not** raise the problem flag. A
  leg with no end after `call_timeout` plus a margin is shown as "unknown (monitor gap)".

**Problem flag:** set when any leg is `failed`, or `dropped` for any reason other than `SUPERSEDED`.

**Reason codes** (`ocpp_2w_proxy/reasons.py`, `Reason` enum → `(Outcome, template)`). These are
unchanged. Monitor gaps are not proxy decisions, so they get no code.

| Area | Codes |
|---|---|
| Session / commands | POLICY_ANSWERED, POLICY_REFUSED, UNKNOWN_TRANSACTION, CHARGER_DISCONNECTED, CHARGER_NO_REPLY, BACKEND_GONE_REPLY_LOST, MALFORMED_FRAME, DEBUG_COMMAND |
| Withholding | WITHHELD_UNAUTHORIZED, START_NOT_CONFIRMED |
| Primary | QUEUED, KEPT_LATEST, SUPERSEDED, HEARTBEAT_LOCAL, DROPPED_UNAVAILABLE, START_REFUSED_CATCHING_UP, BOOT_FROM_REPLAY, PRIMARY_UNAVAILABLE_CLOSE, QUEUED_REJECTED |
| Shared | QUEUE_OVERFLOW |
| Secondary | SKIPPED_OFFLINE, TRANSIENT_DROPPED, NO_LONGER_FORWARDED, NO_TRANSACTION_LINK, MAX_ATTEMPTS, BOOT_FROM_CACHE |

Site → code examples:
- `policy.py:190-193` → POLICY_ANSWERED / POLICY_REFUSED
- `withheld_calls.py` → WITHHELD_UNAUTHORIZED
- `session.py:204-226` → START_NOT_CONFIRMED
- `primary_channel.py:198-235` → QUEUED, KEPT_LATEST, HEARTBEAT_LOCAL, DROPPED_UNAVAILABLE,
  QUEUE_OVERFLOW
- `command_router.py:65-69` → CHARGER_NO_REPLY
- `secondary_channel.py:146-391` → secondary codes

**`not_sent` rule:**
- It holds this charger's secondaries (`ChargerConfig.secondary_ids`) whose `forward_actions` lack the
  action.
- It applies **only to exchanges whose origin is `charger`**. Backend commands, proxy-originated calls
  and debug calls always get `[]`.
- It is computed once, when the exchange is first written, from the running config, and is then frozen.
- It never contains a peer that has a leg or a decision. SKIPPED_OFFLINE, START_NOT_CONFIRMED,
  TRANSIENT_DROPPED and NO_LONGER_FORWARDED are still emitted at their own sites.

## Architecture

### 1. Emission: `TrafficLog` becomes one `FlowRecorder`

**`flow.py`** (new) holds the `FlowRecorder` Protocol and `FanOut(log, monitor)`.

| Method | Called when |
|---|---|
| `received(peer, msg)` / `sent(peer, msg)` | a frame is read or written (existing hook points) |
| `decided(peer, call, reason, detail=None)` | the proxy decides a leg's fate. `detail` is an object the call site already has (e.g. a tx id), never built just for the hook. No `**kwargs`. |
| `link_changed(peer, up)` | a connection comes up or goes down |

`FanOut` has two fixed fields and no loop. Each method:
1. calls `self._log.x(...)`;
2. calls `self._monitor.x(...)` inside `try/except Exception`, which increments `failures` and logs at
   most once a minute.

`TrafficLog` implements the Protocol, and its `decided` and `link_changed` do nothing, so log output is
unchanged. `ChargerContext.traffic` is retyped to `FlowRecorder`.

`server.py:99` builds `FanOut` only when `[monitor]` is enabled. Otherwise it is the plain `TrafficLog`.

Decision call sites get one line each next to the existing `logger` call, e.g.
`self._traffic.decided(PRIMARY, call, Reason.QUEUED)`. `link_changed` is called in `BackendLink.open`,
in the `serve` finally block, and on charger connect and disconnect in `session.run`.

### 2. Correlation

**Exchange id.** `ocpp.Call` gets `exchange_id: int = field(default_factory=next_exchange_id,
compare=False, repr=False)`.
- `next_exchange_id = itertools.count((secrets.randbits(30) << 32) + 1).__next__`. That costs about
  50 ns, fits SQLite's int64, and the random base keeps ids unique across restarts and over 90 days.
- Ids are **opaque**. Nothing sorts or pages by them; ordering uses `started_at` and the rowid.

`with_id` and `with_payload` pass `exchange_id=self.exchange_id` to the constructor explicitly (no
`dataclasses.replace`). `serialize` ignores it.

`state.to_dict` / `from_dict` persist it as an **optional** key, for both the primary outbox and the
secondary queues. `SCHEMA_VERSION` stays 3. An old entry without the key gets a new id and the label
"queued before monitor".

`command_router._Route` keeps the call's `exchange_id`, so the charger's reply resolves to the original
Command.

**`monitor/correlator.py`** (pure; runs in the writer, never on the relay path):
- **Map:** `dict[(charger, peer, wire_id)] → (exchange_id, t)`. Every `sent` frame registers, so a retry
  under a new wire id (`primary_channel.py:389`) gets its own entry.
- **On reply:** the entry is popped into a bounded tombstone ring. A second reply attaches to the same
  exchange marked *duplicate*.
- **On timeout (CHARGER_NO_REPLY):** the entry is *not* removed, so a late reply still attaches and is
  shown as *late*.
- **On link down:** all entries for that peer are cleared. OCPP-J replies never cross connections, and
  chargers reuse message ids after a reboot.
- **Expiry:** from the head in insertion order, at 1 h. The map is capped at 10 000 entries, oldest
  evicted first. There are no full scans.
- **Unmatched reply:** becomes an orphan exchange.

### 3. Non-blocking pipeline

**`MonitorRecorder`** (relay path):
- A hook builds the tuple `(kind, time.time(), charger_id, peer, msg, reason, detail)` and calls
  `put_nowait` on one shared `asyncio.Queue(2000 × chargers)`.
- On `QueueFull` it increments `dropped[charger_id]` and stores the time of the drop. There is no other
  work.

**`MonitorWriter`** (one task):
1. `await queue.get()`, then drain with `get_nowait` until it has 500 events or a 200 ms timer runs out.
   No `wait_for` per event.
2. Process the events in **slices of about 0.1 ms** (at most 50 events; `monitor/loop_slice.py`). After
   each slice the writer rests three times as long as the slice took, so it never takes more than a
   quarter of the loop. `sleep(0)` alone is not enough: the writer would then run a slice in every loop
   iteration, and one relay round trip spans about ten iterations. Per event, in this order:
   1. **Correlate.**
   2. **Redact.** Only actions in `redact.REDACT_ACTIONS`. Orphans, unknown actions and every
      CallError's `details` are always redacted, so redaction fails closed.
   3. **Derive `not_sent`** with `monitor/not_sent.py`, a frozen action → peers map built from config.
      This happens only on the first insert of an exchange whose origin is charger.
   4. **Serialize the leg payload** with `json.dumps`, but only when `leg.payload is not origin.payload`
      and the two are not equal (`!=`).
   5. **Project.**
3. **Gaps:** when `recorder.dropped` has grown since the last batch, the writer adds a `monitor_gaps`
   row and marks the exchanges open in that window, and the orphans resolved in it, as `incomplete`.
4. **Hand the rows to the write worker.** SQLite and zlib run there, because both release the GIL. The
   worker must also hold the GIL as little as possible, and take it back as rarely as possible: each
   time it does, the loop can stall behind it. So exchanges, frames and decisions go in as multi-row
   statements (not one `executemany` step per row), and payloads under 200 bytes are stored as plain
   JSON text, not zlib-compressed (`monitor/payload_blob.py`).
5. **Publish** the changed summaries to `LiveHub`, once per batch. Each summary is serialized once to SSE
   bytes and shared by all subscribers. Without subscribers no summary is built at all: the exchange row
   comes from the cheaper `ExchangeProjection.overview()`.

**`SqliteWorker`**: a dedicated single-thread executor per connection, one for writing and one for
reading (WAL mode). The default executor is never used, because it serves `getaddrinfo` for backend
reconnects.

**Failure handling:**
- A `sqlite3.Error` is logged at most once a minute, and the batch is counted in `dropped_batches`.
- If the database cannot be opened at startup, the proxy runs without the monitor and logs an error.

**Shutdown order:** `hub.close()`, then `wait_for(writer.close(), 2 s)`, then `server.close_clients()`.

**`LiveHub`:** at most 4 SSE subscribers, each with `Queue(256)`. On overflow it sends `event: reset`
and closes, and the client re-fetches.

### 4. SQLite (`monitor/db.py`, `<state_dir>/monitor.sqlite`)

| Table | Columns / content | Indexes |
|---|---|---|
| `exchanges` | rowid, exchange_id INTEGER UNIQUE (upsert: an existing row keeps `origin`, `started_at` and `not_sent`), charger_id, origin, action, started_at, last_at, outcome, problem, incomplete, transaction_id, connector_id, id_tag (masked), not_sent (JSON list), headline | (charger_id, started_at DESC), (transaction_id), partial (started_at) WHERE problem |
| `frames` | exchange_rowid, at, peer, dir, msg_type, wire_id, payload BLOB (zlib of redacted JSON; NULL when equal to the origin's) | exchange_rowid |
| `decisions` | exchange_rowid, at, peer, reason, detail JSON | |
| `links` | charger_id, peer, at, up | |
| `monitor_gaps` | charger_id, from_at, to_at, dropped | (charger_id, from_at) |

- **Pragmas:** WAL, `synchronous=NORMAL`, `foreign_keys=ON` with cascade delete. There is **no
  auto_vacuum**: with fixed retention, freed pages are reused and the file size levels off.
- **Migrations:** `PRAGMA user_version` plus an ordered tuple of SQL scripts.
- **Retention:** an hourly purge deletes rows with `last_at < now - retention_days`, in chunks of 1000.
  Each chunk is its own write job, so it never blocks the writer for long.
- **Reads:** each API call is one synchronous function in the reader thread. It uses one short
  transaction and never holds one across an `await`. A `set_progress_handler` aborts any query that runs
  longer than 250 ms.
- Legs are computed on read by the same `ExchangeProjection`. There is no legs table.

### 5. HTTP: reuse `minihttp`, separate `[monitor]` listener

Extensions to `minihttp.py`, each small and tested:
- `Request.query` via `parse_qsl(strict_parsing=True)`; duplicate keys are rejected with 400.
- `max_connections` becomes a `serve()` parameter. The debug listener keeps 8; the monitor uses 16.
- Two new response types alongside `Response` (JSON). `_write` dispatches on the type with `match`.
  - `Content(status, body: bytes, content_type)`;
  - `EventStream(AsyncIterator[bytes])`: SSE headers, `: ping` every 15 s, and `close_clients()` on
    shutdown.

**API handlers do no heavy work on the loop.** They `await reader.run(fn)`, where `fn` queries,
decompresses and `json.dumps` to bytes inside the reader thread and returns `Content`.

Monitor API rules:
- GET only. A request whose `Origin` differs from its `Host` is rejected.
- Routes use a dispatch table, like `debug_api`.

| Route | Returns |
|---|---|
| `/api/chargers` | the known chargers |
| `/api/exchanges` | Filters: charger, action, outcome, peer, problem, tx, q, exclude. Keyset paging on (started_at, rowid). `limit` defaults to 200 and is clamped to 200. |
| `/api/exchanges/{id}` | one exchange with its legs, frames and decisions |
| `/api/sessions/{tx}` | a server-side aggregate for the session header: energy, duration, counts and problems |
| `/api/links` | Live snapshot taken **directly from the channels**, not from `link_changed` events, which can be dropped: peer state, `queued_count`, drops per charger and the time of the last drop, `fanout.failures`, `writer.dropped_batches` |
| `/api/config` | peers and forward_actions, for lane headers |
| `/api/events` | the SSE stream |

**Static files** (`monitor/static_files.py`):
- a whitelist map built at startup: URL → (bytes, content type), so path traversal is impossible;
- response headers: CSP `default-src 'self'`, `nosniff`, `no-cache`.

Extract `_is_loopback` and the non-loopback warning from `debug_server.py` into a small shared helper.

### 6. Redaction (fixes a log leak too)

`redact.py` gains `_SECRET_CONFIG_KEYS = {"AuthorizationKey"}` and a `REDACT_ACTIONS` table. Logs and the
monitor store use the same functions.

| Call | CallResult |
|---|---|
| Authorize, StartTransaction, StopTransaction: `idTag` | Authorize, StartTransaction, StopTransaction: `idTagInfo.parentIdTag` |
| RemoteStartTransaction: `idTag` | GetConfiguration: `configurationKey[].value` where key = AuthorizationKey |
| ReserveNow: `idTag`, `parentIdTag` | DataTransfer: `data` |
| SendLocalList: `localAuthorizationList[].idTag`, `idTagInfo.parentIdTag` | |
| ChangeConfiguration: `value` when key = AuthorizationKey | |
| GetDiagnostics, UpdateFirmware: user and password in the `location` URL | |
| DataTransfer: generic pass over `data` | |
| BootNotification: `iccid`, `imsi` masked | |

- `transactionData` in StopTransaction holds only meter values and is not redacted.
- A test checks that every action with an idTag, config key or URL field is in `REDACT_ACTIONS`.

### 7. Config (`monitor_config.py`, wired in `config.py`)

```toml
[monitor]
enabled = false
listen = "127.0.0.1"   # "0.0.0.0" in Docker for LAN access (read-only, logs a warning)
port = 8323            # must differ from [proxy] and [debug] ports
# db_path = "<state_dir>/monitor.sqlite"
retention_days = 90
```

Also update `config.example.toml`, `compose.yaml` (publish the port) and the README.

### 8. Python layout (one responsibility per module)

| Module | Responsibility |
|---|---|
| `flow.py` | `FlowRecorder` Protocol and `FanOut` with exception isolation |
| `reasons.py` | `Outcome` and `Reason` enums with human templates |
| `redact.py` (extended) | `REDACT_ACTIONS`, secret config keys, URL credentials |
| `monitor_config.py` | `[monitor]` parsing and validation |
| `monitor/recorder.py` | non-blocking `put_nowait` and per-charger drop counters |
| `monitor/correlator.py` | reply → exchange (ordered expiry, tombstones, clear on link down) |
| `monitor/not_sent.py` | frozen action → non-forwarding peers map |
| `monitor/projection.py` | events → exchange summary and legs (pure) |
| `monitor/headline.py` | one-line headline per action (dispatch dict) |
| `monitor/gaps.py` | turns drop-counter changes into gap rows and incomplete marks |
| `monitor/sqlite_worker.py` | thread-bound connection executor |
| `monitor/db.py` | schema, migrations, writes, queries, purge, query time limit |
| `monitor/writer.py` | sliced loop: correlate → redact → not_sent → serialize → project → hand off → publish |
| `monitor/loop_slice.py` | how long the writer holds the loop per slice (about 0.1 ms) and how long it then rests |
| `monitor/payload_blob.py` | stored payload form: plain JSON text under 200 bytes, zlib from there |
| `monitor/compact_json.py` | one shared compact JSON encoder for rows, the live stream and the API |
| `monitor/live_hub.py` | SSE fan-out with bounded queues and shared bytes |
| `monitor/api.py` | GET routes (dispatch table); bytes built in the reader thread |
| `monitor/static_files.py` | whitelisted static assets |
| `monitor/service.py` | lifecycle: start, ordered close, `recorder_for()` |

## User interface (`ocpp_2w_proxy/monitor/static/`)

**Views (hash router):**
- **Exchanges**: default. Includes live follow, and a session header (from `/api/sessions/{tx}`) when
  `?tx=` is set.
- **Exchange detail**: a right drawer on desktop, a full-screen sheet on phone.
- **Links**: connection health per peer, plus monitor health: drops, gaps and failures.

```
┌ OCPP proxy · charger 99224881 ▾ ─── [Exchanges] [Links] ──────── ● live ⏸  ☾ ┐
│        ┌─────────┐ ══ 1.2k/h ═▶ ┌───────┐ ══ 1.2k/h ═▶ ┌ PRIMARY  ● 38 ms ┐   │
│        │ Charger │              │ PROXY │ ── 980/h ──▶ ┌ homeassistant ● ┐   │
│        └─────────┘              │ ⧗ 0   │ ┄┄ ✕ down ┄┄ ┌ stats  ○ 4m     ┐   │
├ [Heartbeats hidden (312)] [Backend ▾] [Action ▾] [Outcome ▾] [Problems only] [⌕ tx/idTag/id] [Last 1h ▾] ┤
│ time     action / headline      Charger Proxy  Primary    HA          stats         │
│ 14:02:11 MeterValues 7.4kW ⇄tx  ●──────▶◆─────▶✓─────────▶✓           — not sent    │
│ 14:02:05 SetChargingProfile     ·       ◆◀════ canned ════ ●HA ⛔ blocked            │
│ ─ ⚠ 37 monitor events lost 14:02:02–14:02:04 · relaying unaffected ─               │
│ 14:02:01 ChangeConfiguration    ·       ◆◀── ERR NotSupported ─ ●HA ✕ refused       │
│ 13:58:40 StartTransaction       ●──────▶◆──⊣ 🔒 withheld  ✓           ✓             │
│ 13:40:12 StopTransaction  ⇄tx   ●──────▶◆──▷ ⧗ queued → delivered +4m12s            │
│                       [ ▲ 23 new ]                [ load older ]                     │
└───────────────────────────────────────────────────────────────────────────────────┘
```

**Topology header**: a static inline SVG with fixed nodes for the charger, the proxy and each peer.
- Line style shows link state: solid = up, dashed = reconnecting, dotted ✕ = down. The state is also
  written as text.
- Each edge shows a messages-per-hour counter. Queue depth sits on the proxy node.
- The primary is drawn heavier and labelled PRIMARY.
- Clicking a node filters the list. Animated pulses are a later "Could".

**Swimlane encoding.** Colour is never the only signal: every cell has an icon and a word. The palette
is Okabe-Ito, defined as CSS variables for light and dark.

| Element | Line | Head / label |
|---|---|---|
| Call | solid | filled ▶ |
| CallResult | dashed | open ▷ |
| CallError | dashed | ✕ plus "ERR code" |

| Outcome label | Lane cell | Colour |
|---|---|---|
| delivered | ✓ | blue |
| **blocked** (canned CallResult from the proxy) | ⛔; the arrow stops at the Proxy | orange |
| **refused** (CallError from the proxy) | ✕ | vermillion |
| failed (peer error / no reply) | ✕ / ⏱ | vermillion |
| queued (later "delivered +Δ") | ⧗ | yellow |
| withheld | ⊣ 🔒 | purple |
| dropped (with reason) | ⌫ | grey, strikethrough |
| not sent | — | grey |
| unknown (monitor gap) | ? | grey, dotted |

Leg attributes:
- ⇄id, ⇄tx and ⇄profile chips; hovering shows the change;
- proxy-originated legs start from a hollow ◇ with ↻ replay or ✦ synthetic;
- late and duplicate replies get small "late" or "dup" tags;
- incomplete exchanges get a ⚠ tag.

**Detail drawer:** the facts line, then a **Readable | JSON** tab switch (an ARIA tablist; the selected tab is bold and underlined). Every exchange opens in Readable; the choice is not stored and only lasts while the same exchange stays open, so live reloads keep it. Only the active view is built. Live reloads also keep scroll, focus and open sections.

*Readable* shows no JSON:
1. Mini sequence diagram. Each arrow is a link (mouse or Enter) to the block of its frame.
2. **1 · Request**: the payload card of the request.
3. **2 · Forwarded to**: one block per leg, side by side when there is room. Each shows the wire id, outcome, latency, rewrite chips and late/dup tags; what the peer received ("same as the request" or the changed paths); what it answered (the confirmation per OCPP 1.6 action, a CallError's code and description, "answered by the proxy", "no reply" or "waiting"); and the proxy's decisions for that peer. Peers not configured for the action are listed below.
4. **3 · What the requester got back**, left out when the proxy or the debug endpoint made the call.

*JSON* has no graphics: every frame as its OCPP-J message array (`[2,id,action,payload]`, `[3,id,payload]`, `[4,id,code,description,details]`) under a text header (`n · from → to · type · wire id · +Δ`), syntax-coloured, never wrapped (long lines scroll sideways). Copy per frame or all frames, a note that values are masked by the proxy, and the full API record in a closed section.

**Payload cards** (`cards/index.js` maps action → card, with a generic fallback):

| Card | When | Content |
|---|---|---|
| Generic | MVP | key/value tree with friendly field labels (`cards/labels.js`; the raw key is the tooltip); numeric strings trimmed (`' 1.2'` → 1.2) |
| Start / Stop | MVP | kWh delivered and duration |
| MeterValues | Should | measurand × phase table with key figures in kW / kWh / A / V / °C |
| StatusNotification | Should | status pill; errorCode shown only when not NoError |
| BootNotification | Should | charger card with clock skew |
| GetConfiguration / ChangeConfiguration | Should | key/value/readonly table |
| SetChargingProfile | Could | SVG step chart |

Cards describe the request only. Replies are drawn per leg by `cards/responses.js`, a table with a renderer for every OCPP 1.6 confirmation: empty confirmations (MeterValues, StatusNotification, …) read "✓ Acknowledged — no data in reply" and flag unexpected fields; idTagInfo and status confirmations show a status pill; BootNotification, Heartbeat, GetConfiguration and the other data-carrying ones show their own fields.

**Interaction:**
- Heartbeats are hidden by default and show a counter.
- All filters live in the URL hash, so deep links like `#/exchange/<id>` work.
- Auto-follow pauses when you scroll and shows a "▲ N new" pill; ⏸ freezes the list.
- While following, the oldest rows are trimmed so memory stays bounded.
- When the stream drops, a banner appears and the data stays visible.
- Paging: 200-row pages with "load older".
- `prefers-reduced-motion` is respected.
- Below 700 px the lanes collapse into a CSS-only fan-out strip (`pri✓ ha✓ stats—`).

**Frontend files:**

| File | Purpose |
|---|---|
| `index.html`, `styles.css` | page shell and theme |
| `app.js` | router |
| `api.js` | REST calls |
| `live.js` | EventSource and reset handling |
| `filters.js`, `format.js` | pure helpers |
| `views/{exchanges,exchange_detail,links}.js` | the three views |
| `components/{topology,swimlane,badge}.js` | `badge.js` exports a pure `outcomeView(leg)` |
| `cards/*.js` | pure payload → HTML string |

Pure modules are tested with `node --test` through one pytest wrapper that is skipped when `node` is
missing. A Python test also checks that every asset referenced in `index.html` is served.

## Delivery plan (each step = one small green commit, tests included)

1. **Docs.**
   - Add FR-023…FR-028, NFR-013…NFR-016 (NFR-016: monitor adds no measurable relay latency), C-005 and
     UC-008 "Monitor Message Flow".
   - Add Exchange, Leg, Flow Event, Decision, Monitor and Monitor gap to the glossary.
   - Add to ROADMAP: the monitor section, and moving the `StateStore` fsync off the loop.
2. **`redact`.** Add `REDACT_ACTIONS`, AuthorizationKey and URL credentials, with a leak test across all
   listed actions. This also fixes the log leak.
3. **`Call.exchange_id`.** A counter id; `with_*` pass it explicitly. Tests: the id survives
   `command_router`, secondary `submit`, primary `_deliver`, `transactions` and `policy` rewrites, and
   equality is unaffected.
4. **`state`: persist `exchange_id`** as an optional key. Tests: round trip, and an old file without it.
5. **`flow.py`.** `FanOut` with exception isolation; `TrafficLog` implements the Protocol; retype
   `ChargerContext.traffic`; add `RecordingFlow` to `tests/fakes.py`. Test: a raising monitor still lets
   the frame be sent.
6. **`reasons.py`** plus the `decided` and `link_changed` sites in `session.py`, `command_router.py`
   and `withheld_calls.py`.
7. **Primary channel decision sites.**
8. **Secondary channel decision sites.**
9. **`monitor_config`** plus `config.py` wiring (defaults, port clashes, unknown keys).
10. **`correlator`, `not_sent`, `projection`, `headline`**, all pure, with table-driven tests for:
    forwarded, queued then delivered after restart (upsert), blocked, refused, withheld, rewrite (equal
    copy → no chip), late reply, duplicate reply, retry under a new wire id, link-down clearing, orphan,
    and `not_sent` only for charger origin.
11. **`sqlite_worker` + `db`**: schema, migrations, upsert, keyset query, purge in chunks, and the
    progress-handler timeout (on `tmp_path`, with an injected clock).
12. **`recorder` + `gaps` + `writer`**: slices of at most 50, `is`/`==` skip, correlate-before-redact,
    fail-closed redaction, overflow → gap row plus incomplete marks, survives a failing database.
13. **`minihttp`: query parsing, `max_connections`, `Content` response.**
14. **`minihttp` `EventStream` + `live_hub`**: keepalive, shared bytes, slow-subscriber reset, shutdown.
15. **`api` + `static_files` + `service`**, wired into `server.py`, with bytes built in the reader thread
    and 200-row pages. Integration test as in Verification.
16. **Latency guard** (`tests/test_monitor_latency.py`, see Verification), plus micro-benchmarks.
17. **Frontend shell**: topology header, Exchanges list with live follow, gap rows, filters (node tests).
18. **Frontend detail drawer**: sequence, legs, decisions, raw JSON, diff; generic and Start/Stop cards.
19. **Links view**: link history, queue depth, monitor health.
20. **Retention purge task.** This completes the MVP. Update README, `config.example.toml` and
    `compose.yaml`.
21. **Should**: the `?tx=` session header, then the MeterValues, Status, Boot and Configuration cards.
22. **Could**: SetChargingProfile step chart, topology pulses, JSON export of an exchange.

## Verification

- `uv run pytest` and `uvx ruff check . && uvx ruff format --check .` pass after every step.
- With the monitor disabled, the existing tests and log output are unchanged (step 5 proves this).
- Unit tests cover the pure correlator, not_sent, projection, gaps, db, minihttp extensions, live hub
  and redact.
- **Integration** (`tests/test_monitor_integration.py`) uses `conftest.start_proxy(monitor={})`,
  FakeCharger and FakeCsms:
  - charger MeterValues → a delivered primary leg, a rewritten-tx secondary leg, and `not_sent` for a
    secondary not configured for it;
  - secondary SetChargingProfile under an "answer" policy → blocked;
  - primary outage → queued, then delivered after reconnect;
  - SSE delivers the summary.
- **Latency guard** (`tests/test_monitor_latency.py`). Set up:
  - a fake write worker that sleeps 0.5 s per batch;
  - an SSE client that never reads;
  - a 1000-message primary outbox drain.

  Then check:
  - **Default suite:** with `loop.set_debug(True)` and `slow_callback_duration=0.005`, no "Executing …
    took" warning comes from monitor code. Overflow produces a gap row, and relaying completes.
  - **`-m perf`:** across 2000 relay round trips back to back (no idle time on the loop), p99 is at most
    the monitor-off p99 plus 1 ms. Monitor off and on run in alternating blocks of 500, so machine
    noise hits both alike.
  - **Micro-benchmarks:** each hook costs at most about 1 µs (median of 10⁵ calls), and `Call`
    construction costs at most 0.3 µs (measured about 0.21 µs; the default run guards 1 µs).
  - **Grep test:** no `json`, `copy`, `uuid` or `await` in `flow.py` or `monitor/recorder.py`.
- **Frontend:** `node --test` on the pure modules.
- **Manual:**
  - Run locally with `[monitor] enabled = true` and drive traffic with the Bruno collection and the debug
    endpoint.
  - Open `http://127.0.0.1:8323/` in light and dark mode at desktop and phone widths.
  - Check that the topology, the blocked / refused / queued / gap rows, the detail drawer and the diff
    render.
  - Check that no unmasked idTag, AuthorizationKey or URL password appears in the UI or in
    `monitor.sqlite`.
