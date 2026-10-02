# ocpp-2w-proxy

A two-way OCPP **1.6J** proxy. One charger connects to the proxy; the proxy connects to two
backends (CSMSes) at the same time:

- **primary**: the *control* backend, e.g. **SolarEdge** ("charge on solar", "do not use battery").
  It is in charge: its answers are what the charger sees, and it may send any command.
- **secondary**: the *billing* backend, e.g. **Tap Electric**. It sees every session and meter
  reading, may start/stop sessions, but cannot change anything the primary relies on.

```
                         ┌──────────── primary (SolarEdge) ── control, card authorisation
charger ── wss ── proxy ─┤
                         └──────────── secondary (Tap) ────── billing
```

## How messages are routed

| From | Message | Goes to |
|---|---|---|
| charger | Call (BootNotification, StartTransaction, ...) | primary, **and** secondary if listed in `forward_actions` |
| primary | reply to a charger Call | charger |
| secondary | reply to a charger Call | kept by the proxy (used for transaction id mapping), not sent to the charger |
| primary | command (Call) | charger (policy: everything allowed by default) |
| secondary | command (Call) | **policy**: forwarded, or answered by the proxy itself (see below) |
| charger | reply to a command | the backend that sent the command |

Details that make this work in practice:

- **Transaction ids.** In OCPP 1.6 each backend issues its own `transactionId`. The charger only
  knows the primary's, so the proxy remembers which secondary id belongs to it and rewrites
  `MeterValues`/`StopTransaction` (to the secondary) and `RemoteStopTransaction` (from the
  secondary). The mapping is stored on disk.
- **Message ids.** Every backend command is given a fresh unique id on its way to the charger,
  so both backends using the same id (e.g. counters) cannot get each other's replies.
- **Card authorisation** is decided by the primary (register your RFID card in SolarEdge *and*
  Tap). If the secondary rejects a card the primary accepted, a warning is logged: that session
  may not be billed.
- **The secondary can never break charging.** If it is down or slow the charger doesn't notice.
  `StartTransaction`, `MeterValues` and `StopTransaction` are queued on disk and replayed in
  order (with their original timestamps) when it is back, after re-sending the last
  `BootNotification` and `StatusNotification`s. Heartbeats/Authorize are not replayed.
- **The primary is required.** If it is unreachable or drops, the proxy closes the charger
  connection (code 1011). The charger then uses its own offline buffering and reconnects.
- Vendor `DataTransfer` and firmware/diagnostics notifications go to the primary only.

### Secondary (Tap) command policy

| Command from secondary | Default |
|---|---|
| RemoteStartTransaction | forwarded, with any `chargingProfile` removed (it would override solar charging) |
| RemoteStopTransaction | forwarded with the transaction id translated; unknown id → `Rejected` |
| TriggerMessage, GetConfiguration, UnlockConnector, GetCompositeSchedule | forwarded |
| GetLocalListVersion | answered by proxy: `listVersion: -1` (so it won't push a card list) |
| SendLocalList | answered: `NotSupported` |
| ChangeConfiguration | answered: `Rejected`, unless the key is in `change_configuration_allow_keys` |
| SetChargingProfile, ChangeAvailability, Reset, ClearCache, ReserveNow, CancelReservation, DataTransfer | answered: `Rejected` |
| ClearChargingProfile | answered: `Unknown` |
| UpdateFirmware, GetDiagnostics | answered with an empty confirmation |
| anything else | `CallError NotSupported` |

Override per action in `[secondary.policy] actions = { Reset = "forward" }`
(`forward` | `answer` | `error`). The primary has the same mechanism (`[primary.policy]`).

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

## Before you rely on it: Phase 0 checks

1. **Find the SolarEdge OCPP endpoint.** Pointing the charger straight at Tap disables the solar
   features, so SolarEdge controls the charger over this OCPP link and the proxy needs its URL.
   - Ask SolarEdge support / your installer for the full URL (`wss://host/path`); the charger
     appends its id.
   - Or look at your router's / Pi-hole's DNS log while the charger is on its default setting to
     find the hostname (the path still has to be confirmed).
2. **See what the charger sends.** Point the charger at the proxy. Every handshake is logged,
   including rejected ones: path, charger id, whether a password is present (never its value)
   and the offered subprotocol:
   ```
   handshake from 192.168.1.50:51234 path='/ocpp/SE123' username='SE123' password=present (16 chars) subprotocols='ocpp1.6'
   ```
   - If SolarEdge expects the charger's built-in password, use `auth = "forward"` for the
     primary (only works if the charger still sends it with a custom URL).
   - If SolarEdge uses client certificates (Security Profile 3), no proxy can sit in between.
3. **Start 1-way.** First run with only `[primary]` (remove `[secondary]`) and confirm solar
   charging still works through the proxy. Then add Tap.

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

[primary]                       # SolarEdge
url = "wss://<solaredge-endpoint>"
auth = "forward"

[secondary]                     # Tap Electric
url = "wss://<tap-endpoint>"
auth = "basic"
password_env = "SECONDARY_PASSWORD"
```

## Run locally

Uses [uv](https://docs.astral.sh/uv/) and Python 3.14.

```sh
uv sync
SECONDARY_PASSWORD=... uv run python -m ocpp_2w_proxy --config config.toml
uv run pytest            # tests, with fake charger and backends
```

## Deploy on TrueNAS SCALE

The image is built on your PC and loaded on the NAS manually.

```sh
# on your PC (build for the NAS architecture)
docker build --platform linux/amd64 -t ocpp-2w-proxy:0.2.0 .
docker save ocpp-2w-proxy:0.2.0 | gzip > ocpp-2w-proxy-0.2.0.tar.gz
scp ocpp-2w-proxy-0.2.0.tar.gz admin@truenas:/tmp/

# on the NAS (shell)
sudo docker load -i /tmp/ocpp-2w-proxy-0.2.0.tar.gz
```

1. Create datasets, e.g. `tank/apps/ocpp-2w-proxy/config` and `tank/apps/ocpp-2w-proxy/data`.
   Give `data` to user/group **568 (apps)**, as the container runs as that uid and must write
   state there. Put `config.toml` in `config`.
2. **Apps → Discover Apps → ⋮ → Install via YAML** and paste `compose.yaml`, after adjusting
   the host paths, the LAN IP in `ports`, the timezone and the secrets.
3. Check the logs in the app's page. Back up the `data` dataset: it holds the transaction
   mapping and any not-yet-delivered billing messages.

To upgrade: bump the tag, build/save/load again, change `image:` in the app's YAML.
