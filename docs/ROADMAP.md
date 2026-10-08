# Roadmap

Follow-up work for ocpp-2w-proxy after the 0.2.0 rewrite. Each item lists the problem, the
intended design, where it goes, and what "done" means, so it can be picked up cold.

Status legend: `[ ]` open, `[x]` done.

## Context for a new session

**Goal.** Any OCPP 1.6J Charger connects to this vendor-neutral proxy. The proxy connects
to one Primary Backend and zero or more Secondary Backends with independent forwarding and
command policies. All Billing Messages reach the Primary Backend; permitted messages also
reach each configured Secondary Backend. Only primary replies reach the Charger.
Secondary Backends must not disturb charging or override authorization or another backend's
Exclusive Commands. Deployment: Docker on TrueNAS SCALE, LAN only, image loaded via `docker load`.

The requirements catalog and use case specifications define the intended behavior. Implementation
references below describe follow-up work, not completed support for those requirements.

**Module map** (`ocpp_2w_proxy/`, one responsibility each):

| Module | Responsibility |
|---|---|
| `ocpp.py` | OCPP-J frame model, parse/validate/serialize |
| `config.py` | TOML config → frozen dataclasses; secrets from env vars |
| `policy.py` | Per-backend command policy (forward / answer / error) + canned answers |
| `charger_auth.py` | Handshake auth: path → charger id, allowlist, Basic auth |
| `backend_auth.py` | Upstream URL + Authorization header per backend auth mode |
| `backend_link.py` | One WebSocket to a backend; `call()` with reply correlation, `serve()` read loop |
| `primary_channel.py` | Reconnecting Primary Backend link and durable outbox for selected charger messages |
| `secondary_channel.py` | Reconnecting store-and-forward link to each secondary backend (boot replay, durable per-backend queue) |
| `transactions.py` | primary ↔ secondary transactionId mapping and payload rewriting |
| `command_router.py` | Proxy-unique ids for backend→charger commands; routes replies back |
| `state.py` | Atomic JSON persistence per charger (`<state_dir>/<id>.json`) |
| `session.py` | One charger session wiring the primary and secondary channels |
| `server.py` | WebSocket server, handshake hook, one session per charger id, persistent outbox workers |
| `redact.py`, `traffic_log.py` | Safe logging |

**Conventions.**
- Dispatch on action names uses dicts of functions (see `_FORWARD_TRANSFORMS`,
  `_TO_SECONDARY`, `_RESULT_HANDLERS`), not if/elif chains. New concerns get new modules.
- Tests: `uv run pytest`. `tests/fakes.py` has `FakeCsms` (scriptable backend, `.call()` to
  send commands, `.wait_for_call()`) and `FakeCharger`. `conftest.start_proxy` starts a proxy on
  a random port with a shared `tmp_path` state dir; `fast_reconnect` shrinks retry delays.
- Lint/format: `uvx ruff check . && uvx ruff format --check .` (line length 120).
- Commits: small, each green on its own, dependency order (types → logic → wiring → docs).

---

## Phase 0: Generic interoperability validation

Use OCPP test peers for repeatable validation; optionally repeat with real Chargers and
Backends whose connection details are available. An unavailable vendor endpoint does not
block the project.

- [ ] **0.1 Select test peers.** Use a Charger simulator and configurable Primary and
  Secondary Backends with known connection addresses and authentication requirements.
- [ ] **0.2 Capture the charger handshake.** Run the proxy (`[logging] level = "INFO"`), point
  the charger at it and record the `handshake from …` line: path, username (= charger id?),
  password present/length, subprotocol, User-Agent. Decide the primary `auth` mode:
  - password present and the Primary Backend needs it → `auth = "forward"`;
  - no password → `auth = "none"`;
  - if a selected backend needs a client certificate (Security Profile 3), document the
    unsupported authentication requirement rather than treating that vendor as mandatory.
- [ ] **0.3 Certificate acceptance.**
  1. Quick test: let the proxy terminate TLS with a self-signed cert (`tls_cert`/`tls_key`). If
     the charger connects, no public cert is needed.
  2. Otherwise: DuckDNS name → LAN IP, Nginx Proxy Manager with a Let's Encrypt DNS-01 cert, and
     a router DNS-rebind whitelist (see README).
- [ ] **0.4 Primary-only run.** Configure only a Primary Backend. Verify Card Authorization,
  transaction starts/stops, meter readings and permitted commands. Record a redacted DEBUG
  log of one complete Transaction.
- [ ] **0.5 Add Secondary Backends.** Configure independently named Secondary Backends.
  Do one full Transaction and record:
  - whether each Secondary Backend accepts the proxy's BootNotification;
  - every command each sends and what the proxy answers (look for `answered by proxy (policy)`).
    Feeds item 6.1;
  - that each configured backend records correct energy and Transaction start/stop times.
- [ ] **0.6 Outage drill.** During a Transaction, interrupt one Secondary Backend for
  10+ minutes, then restore it. Verify ordered Billing Message delivery with complete meter
  data and no interruption to other backends. Restart the container and verify retention again.

---

## Phase 4: Robustness (known limitations of 0.2.0)

- [ ] **4.1 Retain undelivered durable Billing Messages.**
  *Problem:* `SecondaryChannel._send_head` drops any call after 3 timeouts, including
  `StartTransaction`/`StopTransaction`, so the session is lost for billing.
  *Design:* for durable items, a timeout ends the current connection (raise
  `BackendUnavailable`) so the reconnect/backoff loop retries later. Retain durable items
  after backend rejections and missing transaction mappings as well, with no attempt limit.
  Preserve order per backend; only queue overflow or forwarding-policy exclusion permits
  discarding an undelivered durable item.
  *Done when:* a test with a secondary that stays silent for `StartTransaction` and then
  recovers shows eventual delivery and no loss. Also cover explicit rejection, missing
  mappings, overflow and policy exclusion. Do not infer exactly-once receipt from retries:
  a timed-out answer can follow a message the backend already received.

- [ ] **4.2 Retry a Pending/Rejected live BootNotification.**
  *Problem:* `_boot` only retries the *cached* boot. When the charger's own boot is first in the
  queue and the Secondary Backend answers `Pending`, the proxy only logs it and continues draining; it may then
  reject everything.
  *Design:* treat a BootNotification at the head of the queue as the handshake: move the
  accept/retry loop from `_boot` into a helper used for both cached and live boots. Use the
  `interval` from the reply. Inject `sleep` through `ChargerSession` → `SecondaryChannel`, so
  tests don't wait 10 s. Add an optional `sleep` param to `ChargerSession`, or a
  `boot_retry_min` config value.
  *Done when:* an integration test with a secondary that answers `Pending` once, then
  `Accepted`, sees two BootNotifications before any StartTransaction.

- [ ] **4.3 De-duplicate StartTransaction replays.**
  *Problem:* if the Primary Backend drops after a Secondary Backend already got a `StartTransaction`, the Charger replays
  that start on reconnect and the Secondary Backend records the Transaction twice.
  *Design:* in `TransactionMap`, keep a small persisted map
  `start_fingerprints: {fingerprint: secondary_tx}`. The fingerprint is a hash of `connectorId`,
  `idTag`, `meterStart` and `timestamp`. `SecondaryChannel.submit` checks it for
  `StartTransaction`: on a hit, don't enqueue; pair the new `start_ref` with the known
  secondary tx (`secondary_started(start_ref, tx)`). Cap it at ~100 entries like the pending
  maps.
  *Done when:* a test where the primary drops after the secondary answered StartTransaction,
  followed by the Charger replaying the identical start, shows the Secondary Backend gets exactly one
  StartTransaction, and MeterValues/StopTransaction carry its transaction id.

- [ ] **4.4 Send cached StatusNotifications after the durable backlog.**
  *Problem:* on reconnect, current statuses are sent *before* replaying hours-old
  `StartTransaction`/`StopTransaction`, so the Secondary Backend's final view of the connector can be stale.
  *Design:* in `_sync_and_drain`: boot → drain only the durable items present at connect time
  → send cached statuses → normal draining.
  *Done when:* the outage test in `test_proxy_integration.py` asserts StatusNotification comes
  after StopTransaction.

- [ ] **4.5 Log failed relay tasks.**
  *Problem:* an unexpected exception in `ChargerSession._relay_charger_call` only surfaces as
  asyncio's "Task exception was never retrieved".
  *Design:* in `_spawn`, add a done-callback that logs `task.exception()` with `logger.error`
  (skip cancelled tasks).

- [ ] **4.6 Reload the TLS certificate without a restart** (only when the proxy terminates TLS).
  *Design:* a background task in `ProxyServer` checks the mtime of `tls_cert`/`tls_key` every
  hour and calls `load_cert_chain` on the existing `SSLContext` (applies to new handshakes).
  Log reload failures and keep the old cert.
  *Done when:* a unit test with a temp cert pair shows the context reloads after an mtime change.

- [ ] **4.7 Retain selected Primary Backend updates during outages.**
  `PrimaryChannel` keeps established Charger sessions open, locally acknowledges and durably
  queues `StatusNotification`, `MeterValues`, and `StopTransaction`, then retries and replays
  them in order. It retries indefinitely with exponential backoff and jitter, and the per-Charger
  queue survives restarts with a 10,000-call limit.

## Phase 5: Observability and operations

- [ ] **5.1 Health endpoint.**
  *Design:* in `ProxyServer._process_request`, answer `GET /healthz` (no WebSocket upgrade)
  with `200` and a minimal JSON. Per charger: `connected`, `primary_connected`,
  `secondary_connected`, `secondary_queue`. No ids, tags or URLs beyond the charger id.
  `SecondaryChannel` exposes `connected` and a new `queue_depth`; sessions register a status
  provider in the server. Switch the Dockerfile `HEALTHCHECK` to this endpoint
  (`urllib.request`). The container stays healthy when the secondary is down: report it, don't
  fail on it.
  *Done when:* integration tests cover idle, connected, and secondary-down.

- [ ] **5.2 Billing-at-risk warnings.**
  *Design:* log a WARNING when the secondary has been unreachable for more than
  `[secondary] warn_offline_after` seconds (default 900), repeated hourly, including the queue
  depth. Log an ERROR at 80 % of `max_queue`. These lines are what to alert on in TrueNAS or
  Loki.

- [ ] **5.3 State inspection command.**
  *Design:* `python -m ocpp_2w_proxy state --config … [--charger ID]` prints, read-only and
  redacted, the transaction map, pending starts, queue (action + timestamp) and cached
  boot/status. Put it in a new `cli_state.py`; keep `__main__.py` as a thin dispatcher with
  argparse subcommands (`serve` as default).
  For use in the TrueNAS shell: `docker exec ocpp-2w-proxy python -m ocpp_2w_proxy state`.

## Phase 6: Policy tuning from field data (depends on 0.4/0.5)

- [ ] **6.1 Review configuration commands.** Verify forwarding and standard refusal responses
  against each backend's configured policy. Configuration-key permissions must preserve
  exclusive ownership and reserve authorization settings for the Primary Backend.
- [ ] **6.2 Review remote start/stop.** Verify that only the Primary Backend can forward
  remote starts, while permitted Secondary Backends can stop or adjust an existing
  Transaction without bypassing primary authorization.
- [ ] **6.3 Review metering settings.** Verify that configured metering intervals and
  stop data provide the information required by the selected billing backends, without
  allowing conflicting configuration changes.

## Phase 7: Deployment polish

- [ ] **7.1 `scripts/build-image.sh <version>`**: runs `docker build --platform linux/amd64`,
  `docker save | gzip` and prints the `scp`/`docker load` commands. Read the version from
  `pyproject.toml` when no argument is given.
- [ ] **7.2 Verify the image** (Docker wasn't available when 0.2.0 was written):
  - build succeeds;
  - the container starts as uid 568 with a read-only root filesystem;
  - `/data` is writable;
  - the healthcheck turns healthy;
  - SIGTERM shuts down within the timeout.
- [ ] **7.3 Backups:** document a TrueNAS periodic snapshot task on the `data` dataset. It holds
  the transaction map and undelivered billing messages.
