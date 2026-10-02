# Roadmap

Follow-up work for ocpp-2w-proxy after the 0.2.0 rewrite. Each item lists the problem, the
intended design, where it goes, and what "done" means, so it can be picked up cold.

Status legend: `[ ]` open, `[x]` done.

## Context for a new session

**Goal.** A SolarEdge ONE EV charger (OCPP 1.6J) connects to this proxy. The proxy connects
to SolarEdge (*primary*: control, solar modes, card authorisation) and Tap Electric
(*secondary*: billing). The charger only ever sees the primary's replies. Tap must never be able
to break charging or override SolarEdge's settings. RFID cards are registered in both backends;
SolarEdge decides. Deployment: Docker on TrueNAS SCALE, LAN only, image loaded via `docker load`.

**Module map** (`ocpp_2w_proxy/`, one responsibility each):

| Module | Responsibility |
|---|---|
| `ocpp.py` | OCPP-J frame model, parse/validate/serialize |
| `config.py` | TOML config → frozen dataclasses; secrets from env vars |
| `policy.py` | Per-backend command policy (forward / answer / error) + canned answers |
| `charger_auth.py` | Handshake auth: path → charger id, allowlist, Basic auth |
| `backend_auth.py` | Upstream URL + Authorization header per backend auth mode |
| `backend_link.py` | One WebSocket to a backend; `call()` with reply correlation, `serve()` read loop |
| `secondary_channel.py` | Reconnecting store-and-forward link to the secondary (boot replay, durable queue) |
| `transactions.py` | primary ↔ secondary transactionId mapping and payload rewriting |
| `command_router.py` | Proxy-unique ids for backend→charger commands; routes replies back |
| `state.py` | Atomic JSON persistence per charger (`<state_dir>/<id>.json`) |
| `session.py` | One charger session wiring all of the above |
| `server.py` | WebSocket server, handshake hook, one session per charger id |
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

## Phase 0: Field validation (manual, blocks everything else)

Done with the real charger. Record the findings in this file (or `docs/FIELD_NOTES.md`):
they decide the config and possibly the code.

- [ ] **0.1 Find the SolarEdge OCPP endpoint.** Ask SolarEdge support or the installer for the
  full `wss://host/path`, or read the hostname from the router / Pi-hole DNS log while the charger
  is on its default setting. *Blocker if not obtainable.*
- [ ] **0.2 Capture the charger handshake.** Run the proxy (`[logging] level = "INFO"`), point
  the charger at it and record the `handshake from …` line: path, username (= charger id?),
  password present/length, subprotocol, User-Agent. Decide the primary `auth` mode:
  - password present and SolarEdge needs it → `auth = "forward"`;
  - no password → `auth = "none"`;
  - if SolarEdge turns out to need a client certificate (Security Profile 3) → *blocker*:
    a proxy cannot impersonate the charger.
- [ ] **0.3 Certificate acceptance.**
  1. Quick test: let the proxy terminate TLS with a self-signed cert (`tls_cert`/`tls_key`). If
     the charger connects, no public cert is needed.
  2. Otherwise: DuckDNS name → LAN IP, Nginx Proxy Manager with a Let's Encrypt DNS-01 cert, and
     a router DNS-rebind whitelist (see README).
- [ ] **0.4 One-way run.** Config with only `[primary]`. Verify in the mySolarEdge app that
  "charge on solar" and "do not use battery" still work, and that a charge session with the RFID
  card starts and stops. Keep a DEBUG log (`log_payloads = true`) of one full session. It shows
  which commands SolarEdge sends (expected: `SetChargingProfile`, `ChangeConfiguration`,
  `DataTransfer`), which matters for the 4.x items.
- [ ] **0.5 Add Tap.** Add `[secondary]` with the Tap URL and credentials from the Tap
  dashboard. Do one full session and record:
  - whether Tap accepts the proxy's BootNotification;
  - every command Tap sends and what the proxy answers (look for `answered by proxy (policy)`).
    Feeds item 6.1;
  - that the session appears in Tap with the correct kWh and the correct start/stop times.
- [ ] **0.6 Outage drill.** During a session: stop Tap connectivity (e.g. firewall rule on the
  NAS for the Tap host) for 10+ minutes, then restore it. Verify the session is billed in Tap
  with complete meter data. Then restart the container mid-session and verify again.

---

## Phase 4: Robustness (known limitations of 0.2.0)

- [ ] **4.1 Never drop durable billing messages on timeout.**
  *Problem:* `SecondaryChannel._send_head` drops any call after 3 timeouts, including
  `StartTransaction`/`StopTransaction`, so the session is lost for billing.
  *Design:* for durable items, a timeout ends the current connection (raise
  `BackendUnavailable`) so the reconnect/backoff loop retries later. Only a `CallError` drops a
  durable item. Keep the 3-attempt drop for transient items.
  *Done when:* a test with a secondary that stays silent for `StartTransaction` and then
  recovers shows it delivered exactly once after reconnect.

- [ ] **4.2 Retry a Pending/Rejected live BootNotification.**
  *Problem:* `_boot` only retries the *cached* boot. When the charger's own boot is first in the
  queue and Tap answers `Pending`, the proxy only logs it and continues draining; Tap may then
  reject everything.
  *Design:* treat a BootNotification at the head of the queue as the handshake: move the
  accept/retry loop from `_boot` into a helper used for both cached and live boots. Use the
  `interval` from the reply. Inject `sleep` through `ChargerSession` → `SecondaryChannel`, so
  tests don't wait 10 s. Add an optional `sleep` param to `ChargerSession`, or a
  `boot_retry_min` config value.
  *Done when:* an integration test with a secondary that answers `Pending` once, then
  `Accepted`, sees two BootNotifications before any StartTransaction.

- [ ] **4.3 De-duplicate StartTransaction replays.**
  *Problem:* if SolarEdge drops after Tap already got a `StartTransaction`, the charger replays
  that start on reconnect and Tap records the session twice.
  *Design:* in `TransactionMap`, keep a small persisted map
  `start_fingerprints: {fingerprint: secondary_tx}`. The fingerprint is a hash of `connectorId`,
  `idTag`, `meterStart` and `timestamp`. `SecondaryChannel.submit` checks it for
  `StartTransaction`: on a hit, don't enqueue; pair the new `start_ref` with the known
  secondary tx (`secondary_started(start_ref, tx)`). Cap it at ~100 entries like the pending
  maps.
  *Done when:* a test where the primary drops after the secondary answered StartTransaction,
  followed by the charger replaying the identical start, shows Tap gets exactly one
  StartTransaction, and MeterValues/StopTransaction carry the right Tap id.

- [ ] **4.4 Send cached StatusNotifications after the durable backlog.**
  *Problem:* on reconnect, current statuses are sent *before* replaying hours-old
  `StartTransaction`/`StopTransaction`, so Tap's final view of the connector can be stale.
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

- [ ] **6.1 Silence Tap's configuration attempts.** If Tap keeps retrying or flags errors on
  `ChangeConfiguration` → `Rejected`, add a `change_configuration_accept_keys` option. The
  proxy answers `Accepted` without forwarding (a deliberate, documented lie), alongside the
  existing `change_configuration_allow_keys` (really forwarded). Consider forwarding keys that
  only add data, such as `MeterValuesSampledData` additions, if SolarEdge doesn't depend on them.
- [ ] **6.2 Review RemoteStart/Stop from Tap.** If the Tap app starts sessions remotely, confirm
  SolarEdge still applies its solar profile to them (the profile is stripped from Tap's
  request). If it doesn't, consider `RemoteStartTransaction = "answer"`.
- [ ] **6.3 SolarEdge commands that affect billing.** If 0.4 shows SolarEdge changing
  `MeterValueSampleInterval` or `StopTxnSampledData` to values that are too coarse for Tap,
  document it, or add a primary allowlist. Default stays "primary may do everything".

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
