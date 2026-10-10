# ocpp-2w-proxy

A two-way OCPP **1.6J** proxy. One charger connects to the proxy; the proxy connects to one
primary and any number of named secondary backends (CSMSes) at the same time:

- **primary**: the backend *in charge*, e.g. **Tap Electric** (billing). Card authorization,
  transaction numbers and remote starts come from it; its answers are what the charger sees,
  and by default it may send any command.
- **secondary backends**: *control* or statistics backends, e.g. **HomeAssistant** ("charge on
  solar"). Each sees the messages configured for it and may lower/pause/resume charging via
  charging profiles assigned to it, but cannot change anything the primary relies on.

```
                          ┌──────────── primary (Tap) ────── billing, card authorisation, remote start
charger ── wss ── proxy ─┤
                          └──────────── secondaries (HomeAssistant, ...) ─ solar charging profiles
```

## How messages are routed

| From | Message | Goes to |
|---|---|---|
| charger | Call (BootNotification, StartTransaction, ...) | primary, **and** every secondary whose `forward_actions` list it |
| primary | reply to a charger Call | charger |
| any secondary | reply to a charger Call | kept by the proxy (used for transaction id mapping), not sent to the charger |
| primary | command (Call) | charger (policy: everything allowed by default) |
| any secondary | command (Call) | **policy**: forwarded, or answered by the proxy itself (see below) |
| charger | reply to a command | the backend that sent the command |

Details that make this work in practice:

- **Transaction ids.** In OCPP 1.6 each backend issues its own `transactionId`. The charger only
  knows the primary's, so the proxy remembers which id each secondary assigned to it and rewrites
  `MeterValues`/`StopTransaction` (to that secondary) and `RemoteStopTransaction` (from it).
  The mapping is stored on disk.
- **Message ids.** Every backend command is given a fresh unique id on its way to the charger,
  so both backends using the same id (e.g. counters) cannot get each other's replies.
- **Card authorisation** is decided by the primary (register your RFID card in Tap). Whenever a
  secondary does not accept a card, a warning naming that backend is logged: the session may
  not be billed there.
- **The secondaries can never break charging.** If one is down or slow neither the charger nor
  the other secondaries notice. `StartTransaction`, `MeterValues` and `StopTransaction` are
  queued on disk **per backend** and replayed in order (with their original timestamps) when
  it is back, after re-sending the last `BootNotification` and `StatusNotification`s it is
  configured for. Heartbeats/Authorize are not replayed. A secondary that answers a queued
  message with an OCPP error gets it again after a growing delay; after 5 errors in a row
  the message is dropped and logged, so the messages behind it are not held up forever.
  A secondary that is not sent the charger's heartbeats gets its own heartbeats from the
  proxy, at the interval it asked for in its BootNotification reply.
- **The charger sees the primary's availability.** If the primary is unreachable during
  connection setup, the proxy closes the charger connection (code 1011). If it drops after the
  session starts, the proxy retries with increasing delays (1–300 seconds, ±50% jitter) and
  hides the outage for `outage_grace` seconds (default 30). A longer outage closes the charger
  connection (code 1011), so the charger goes offline and follows its own OCPP offline rules:
  it authorizes cards locally and queues its transaction messages until it can reconnect, which
  succeeds once the primary is back. During the grace period the proxy answers the charger itself:
    - `StopTransaction` and the `MeterValues` of a transaction are queued on disk and replayed
      in order.
    - Of `StatusNotification` and firmware/diagnostics status notifications only the newest per
      connector is kept; they are sent after the queue has been emptied.
    - `Heartbeat` is answered with the current time; `MeterValues` outside a transaction are
      dropped.
    - A call that needs the primary's own answer (`Authorize`, `StartTransaction`,
      `BootNotification`, `DataTransfer`) waits up to `call_timeout` for the primary to
      reconnect. Without an answer it is never given a substitute: the charger connection is
      closed, so the charger keeps the message and resends it once it is back online.
    - A `StartTransaction` is refused with an OCPP error while the queue is not yet emptied.

  The queue holds up to 10,000 messages per charger (`max_queue`, at least 10,000); when it
  overflows, meter readings are dropped first, then the oldest message, and this is logged.
  Delivery continues after the charger disconnects. With primary `auth = "forward"`,
  a proxy restart cannot replay queued calls until a new charger handshake supplies credentials.
- Vendor `DataTransfer` and firmware/diagnostics notifications go to the primary only.

### Secondary command policy

Each `[[secondary]]` entry has its own policy. Defaults for every secondary:

| Command from secondary | Default |
|---|---|
| RemoteStopTransaction | forwarded with the transaction id translated; unknown id → `Rejected` |
| TriggerMessage, GetConfiguration, UnlockConnector, GetCompositeSchedule | forwarded |
| RemoteStartTransaction | answered: `Rejected` (Authorization Commands stay with the primary) |
| GetLocalListVersion | answered by proxy: `listVersion: -1` (so it won't push a card list) |
| SendLocalList | answered: `NotSupported` |
| ChangeConfiguration | answered: `Rejected`, unless the key is in `change_configuration_allow_keys` |
| SetChargingProfile, ChangeAvailability, Reset, ClearCache, ReserveNow, CancelReservation, DataTransfer | answered: `Rejected` |
| ClearChargingProfile | answered: `Unknown` |
| UpdateFirmware, GetDiagnostics | answered with an empty confirmation |
| anything else | `CallError NotSupported` |

Override per action in that backend's `[secondary.policy] actions = { Reset = "forward" }`
(`forward` | `answer` | `error`), right after its `[[secondary]]` entry. The primary has the
same mechanism (`[primary.policy]`).

The proxy **refuses to start** on configurations that would send conflicting commands
(checked at startup, naming the command and the backends):

- an **Exclusive Command** (charging profiles, configuration, availability, reset, firmware,
  local list, cache, reservations, data transfer, remote start) forwarded by more than one
  backend — to hand one to a secondary, first take it away from the primary, e.g.
  `[primary.policy] actions = { SetChargingProfile = "answer" }` +
  `[secondary.policy] actions = { SetChargingProfile = "forward" }`;
- an **Authorization Command** (remote start, local list, reservations) or an authorization
  configuration key (e.g. `LocalAuthListEnabled`) forwarded by a secondary backend;
- a configuration key permitted (`change_configuration_allow_keys`) for two backends.

A secondary may own a configuration key (`change_configuration_allow_keys`) while the primary
forwards all configuration changes: the proxy then answers the primary's changes to that key
with `Rejected`.

## Security

- **Allowlist**: only chargers listed in `[[chargers]]` can connect; others get HTTP 404
  before the WebSocket is opened.
- **Charger authentication**: the charger sends its id as Basic-auth username; it must match the
  id in the URL. If `password_env` is set for the charger, the password is required too
  (constant-time compare). Without a password, anyone on your LAN who knows the charger id could
  impersonate it: keep the proxy LAN-only and set a password if the charger lets you.
- **Credentials are per backend**: `none`, `basic` (backend charger id + password from an
  environment variable) or `forward` (the charger's own Authorization header, primary only).
  Charger credentials are never sent to the secondary.
- **Logs** never contain passwords; RFID tags are masked (`***C4D5`). Full (redacted) payloads
  are only logged with `level = "DEBUG"` and `log_payloads = true`.
- **Container** runs as uid 568, read-only root filesystem, no capabilities. Config is mounted
  read-only; secrets come from environment variables.
- **Never** forward the proxy port on your router.

## Debug endpoint

An optional HTTP endpoint that sends **any charger-bound OCPP 1.6 command** to a connected charger
and returns the charger's own CALLRESULT or CALLERROR. It exists to diagnose charger behavior,
for example when a backend's `SetChargingProfile` is `Accepted` but the charging power does not
change (`Accepted` means "stored", not "applied"). It is off by default.

Enable it in `config.toml` (see `config.example.toml`):

```toml
[debug]
enabled = true
listen = "127.0.0.1"    # default
port = 8322             # default; must differ from [proxy] port
default_timeout = 30    # seconds to wait for the charger
max_timeout = 250       # per-request timeout is clamped to this
```

### Security model

There is **no authentication**. The guard-rails are:

- off unless `enabled = true`; binds to loopback by default (a warning is logged when bound elsewhere);
- only `GET` and `POST`; a request with an `Origin` header (a browser) is refused with 403 and a
  command needs `Content-Type: application/json`;
- at most one command in flight per charger (409 otherwise); small request size limits;
- one audit log line per command; no queuing when the charger is offline.

Things to know before you use it:

- It **bypasses the command policy** and the exclusive-command rules, and **no `transactionId`
  rewriting** happens: use the charger's own transaction id (the primary's), not a secondary's.
- Debug commands and their replies are **never forwarded to backends**, but their effects stay on
  the charger and can interfere with the control backend's charging profiles.
- Persistent effects: `ChangeConfiguration`, `ChangeAvailability` and charging profiles survive
  until changed again. An empty `ClearChargingProfile` removes every profile, including the
  load-balancing cap the control backend set.
- Hardware risks: `UpdateFirmware`, `Reset` (a hard reset or `UnlockConnector` under load),
  `DataTransfer`, `GetDiagnostics` (uploads to any URL you give) and vendor or safety keys via
  `ChangeConfiguration` (e.g. `AuthorizationKey`). A profile above the site's breaker rating is
  not checked by the proxy.
- Anyone who can reach the port can do all of this. Never publish it to the LAN or router.

### Endpoints

List the configured chargers:

```sh
curl http://127.0.0.1:8322/debug/chargers
```
```json
{"chargers": [{"id": "SE123456", "connected": true, "connected_since": "2026-10-10T08:01:12Z", "remote": "192.168.1.50:51234"}]}
```

Send a command (`payload` defaults to `{}`, `timeout` to `default_timeout`):

```sh
curl -X POST -H 'Content-Type: application/json' \
  -d '{"action":"GetConfiguration","payload":{"key":["ChargingScheduleMaxPeriods"]}}' \
  http://127.0.0.1:8322/debug/chargers/SE123456/commands
```
```json
{
  "status": "CallResult",
  "answered_by": "charger",
  "message_id": "dbg-0f3a...",
  "action": "GetConfiguration",
  "latency_ms": 143,
  "result": {"configurationKey": [{"key": "ChargingScheduleMaxPeriods", "readonly": true, "value": "24"}]},
  "sent": [2, "dbg-0f3a...", "GetConfiguration", {"key": ["ChargingScheduleMaxPeriods"]}],
  "received": [3, "dbg-0f3a...", {"configurationKey": []}]
}
```

A charger that answers with an OCPP error is still HTTP 200, with `"status": "CallError"` and
`"error": {"code": ..., "description": ..., "details": ...}` instead of `result`.
`answered_by: "charger"` tells a charger's own `Rejected` from one the proxy would answer
itself. Only the 19 charger-bound OCPP 1.6 actions are accepted (not charger-to-backend ones such
as `Authorize`).

| HTTP | Meaning |
|---|---|
| 200 | the charger answered (CallResult or CallError) |
| 400 | invalid JSON, unknown or charger-originated action, `payload` not an object, bad `timeout` |
| 403 | `Origin` header present (browser request) |
| 404 | unknown charger, or charger not connected |
| 408 / 411 / 413 / 431 / 501 | request read timeout / no `Content-Length` / body too large / headers too large / chunked or unsupported method |
| 409 | another debug command is in flight for this charger |
| 502 | the charger disconnected before it answered |
| 504 | the charger did not answer within the timeout (the frame that was sent is included) |

### Bruno collection and diagnostic playbook

`bruno/` holds a [Bruno](https://www.usebruno.com/) collection with one request per charger-bound
action (open the folder in Bruno, pick the `local` environment, set `chargerId`) and its own
README with the diagnostic playbook for a charging profile that is accepted but has no effect:
`GetConfiguration` (supported feature profiles, schedule limits) -> `TriggerMessage MeterValues`
-> `GetCompositeSchedule` -> `ClearChargingProfile` -> `SetChargingProfile` variants, re-checking
`GetCompositeSchedule` and the meter's `Current.Offered` after each.

### Docker

Inside the container the endpoint must listen on all interfaces (`listen = "0.0.0.0"`), which is
only safe if you publish it on the host's loopback: use `"127.0.0.1:8322:8322"` in `ports` (the
commented line in `compose.yaml`), never the LAN IP. Remove it again when you are done.

## Before you rely on it: Phase 0 checks

1. **Find the backend OCPP endpoints.** Pointing the charger straight at only one backend
   loses the other's features, so both connect through the proxy and each needs its URL.
    - Ask each backend's support / your installer for the full URL (`wss://host/path`); the
      charger appends its id.
    - Or look at your router's / Pi-hole's DNS log while the charger is on its default setting
      to find the hostname (the path still has to be confirmed).
2. **See what the charger sends.** Point the charger at the proxy. Every handshake is logged,
    including rejected ones: path, charger id, whether a password is present (never its value),
    the offered subprotocol and the user agent:
    ```
    handshake from 192.168.1.50:51234 path='/ocpp/SE123' username='SE123' password=present (16 chars) subprotocols='ocpp1.6' user-agent='...'
    ```
    - If the primary expects the charger's built-in password, use `auth = "forward"` for the
      primary (only works if the charger still sends it with a custom URL).
    - If a backend uses client certificates (Security Profile 3), no proxy can sit in between.
3. **Start 1-way.** First run with only `[primary]` (remove every `[[secondary]]` entry) and
   confirm charging and card authorization work through the proxy. Then add the secondaries.

## TLS (the charger requires `wss://`) without owning a domain

The charger almost certainly validates the certificate against public CAs, so a self-signed
certificate will probably be refused. A free option that needs no open ports:

1. Create a free subdomain at **DuckDNS** (e.g. `myocpp.duckdns.org`) and set its IP to the
   **LAN IP** of your TrueNAS (e.g. `192.168.1.10`). It is only resolvable to a private address,
   nothing is exposed.
2. Install **Nginx Proxy Manager** from the TrueNAS app catalog. Add a proxy host:
   - Domain `myocpp.duckdns.org`, scheme `http`, forward to `192.168.1.10:8321`,
     **Websockets Support: on**.
   - SSL: request a Let's Encrypt certificate using the **DNS challenge** with provider DuckDNS
     (paste your DuckDNS token). Renewal is automatic.
3. If your router has DNS-rebind protection (e.g. FRITZ!Box), whitelist `myocpp.duckdns.org`,
   otherwise it refuses to resolve a public name to a private IP.
4. In the charger set the OCPP URL to `wss://myocpp.duckdns.org/ocpp` (the charger appends its id).

Alternative: let the proxy terminate TLS itself with `tls_cert`/`tls_key` in `[proxy]`
(mount the files into `/config`; restart after renewal). Handy for quickly testing whether the
charger accepts a self-signed certificate.

## Configuration

Copy `config.example.toml` to `config.toml` and edit it; every option is documented there.
Minimal example:

```toml
[proxy]
state_dir = "/data"

[[chargers]]
id = "SE123456"

[primary]                       # Tap Electric
url = "wss://<tap-endpoint>"
auth = "basic"
password_env = "PRIMARY_PASSWORD"

[primary.policy]                # hand the solar charging profiles to the secondary
actions = { SetChargingProfile = "answer", ClearChargingProfile = "answer" }

[[secondary]]                   # HomeAssistant
name = "homeassistant"
url = "ws://<homeassistant-endpoint>"
auth = "none"

[secondary.policy]
actions = { SetChargingProfile = "forward", ClearChargingProfile = "forward" }
```

## Run locally

Uses [uv](https://docs.astral.sh/uv/) and Python 3.14.

```sh
uv sync
SECONDARY_PASSWORD=... uv run python -m ocpp_2w_proxy --config config.toml
uv run pytest            # tests, with fake charger and backends
```

## Deploy on TrueNAS SCALE

The image is built on your PC and shipped to the NAS by `build-and-deploy.sh`
(tests, build, `podman save`, scp, `docker load` and tag cleanup in one go):

```sh
cp .env.build.example .env.build    # set NAS_SSH_HOST and NAS_TMP_DIR
./build-and-deploy.sh all
```

1. Create datasets, e.g. `tank/apps/ocpp-2w-proxy/config` and `tank/apps/ocpp-2w-proxy/data`.
   Give `data` to user/group **568 (apps)**, as the container runs as that uid and must write
   state there. Put `config.toml` in `config`.
2. **Apps → Discover Apps → ⋮ → Install via YAML** and paste `compose.yaml`, after adjusting
   the host paths, the LAN IP in `ports`, the timezone and the secrets.
3. Check the logs in the app's page. Back up the `data` dataset: it holds the transaction
   mapping and any not-yet-delivered Primary or Secondary Backend messages.

To upgrade: run `./build-and-deploy.sh all` again, then Stop and Start the app
(the app runs `ocpp-2w-proxy:latest`, which the script refreshed on the NAS).
