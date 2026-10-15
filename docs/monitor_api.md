# Flow Monitor HTTP API

This is the contract between the monitor backend (`ocpp_2w_proxy/monitor/api.py`) and the browser UI
(`ocpp_2w_proxy/monitor/static/`).

All routes are `GET` and return JSON. Any other method gets 405. A request whose `Origin` header differs
from its `Host` gets 403. Timestamps are Unix epoch seconds as floats.

**Exchange ids are JSON strings**, because they can exceed the integers JavaScript represents safely.

## Names

- **Peers:** `"charger"`, the primary backend's name, and each secondary backend's name.
- **Origins:** `"charger"`, a backend name, `"proxy"` (replayed or synthetic) or `"debug"`. An orphan reply
  (one that matched no known exchange) has `origin: null` and `action: null`.
- **Outcomes:** `delivered`, `answered_by_proxy`, `queued`, `withheld`, `dropped`, `failed`, or `null`
  while a leg is still open.
- **Reason codes:** the names of `ocpp_2w_proxy.reasons.Reason`.

## Exchange summary

Used in lists and in SSE `exchange` events:

```json
{
  "id": "4611686018427387905",
  "charger_id": "99224881",
  "origin": "charger",
  "action": "MeterValues",
  "started_at": 1760536931.21,
  "last_at": 1760536931.30,
  "outcome": "delivered",
  "problem": false,
  "incomplete": false,
  "transaction_id": 10216,
  "connector_id": 1,
  "id_tag": "***4F2A",
  "headline": "7.4 kW · 1234.5 kWh",
  "not_sent": ["stats"],
  "legs": [
    {
      "peer": "primary",
      "wire_id": "c0ffee-1",
      "outcome": "delivered",
      "reason": null,
      "reason_text": null,
      "reply": "result",
      "error_code": null,
      "replied_by": "peer",
      "sent_at": 1760536931.22,
      "replied_at": 1760536931.26,
      "rewritten": [],
      "late": false,
      "duplicate": false
    },
    {
      "peer": "homeassistant",
      "wire_id": "7f3a…",
      "outcome": "delivered",
      "reason": null,
      "reason_text": null,
      "reply": "result",
      "error_code": null,
      "replied_by": "peer",
      "sent_at": 1760536931.23,
      "replied_at": 1760536931.30,
      "rewritten": [{"field": "tx", "from": 10216, "to": 1791822333}],
      "late": false,
      "duplicate": false
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `outcome` | The exchange's overall outcome: the worst leg outcome, ranked failed > dropped > withheld > queued > open (`null`) > answered_by_proxy > delivered. |
| `problem` | True when any leg's reason `is_problem`, or a peer replied with a CallError. |
| `incomplete` | The exchange was open during a monitor gap. |
| `legs[].outcome` / `reason` | Where the leg ended. `null` while still open. |
| `legs[].reply` | `"result"`, `"error"` or `null`. |
| `legs[].replied_by` | `"peer"`, `"proxy"` or `null`. A proxy CallResult is shown as "blocked"; a proxy CallError as "refused". |
| `legs[].rewritten[].field` | `"id"` (the idTag), `"tx"` or `"profile"`. `to: null` means the field was removed. Wire ids differ per leg by design and are not a rewrite. |
| Unknown (monitor gap) | A leg with `outcome: null` and `reason_text: "Unknown (monitor gap)"`: the exchange is incomplete, or the leg has been open longer than 120 s. |

For an exchange that originates from a backend, the leg toward the charger has `peer: "charger"`. When
`origin` is `"proxy"`, the decisions say why the proxy sent it (e.g. `BOOT_FROM_CACHE`).

## Routes

### `GET /api/chargers`
Returns `{"chargers": [{"id": "99224881", "connected": true}]}`.

### `GET /api/config`
Returns:

```json
{"primary": "primary", "secondaries": [{"name": "homeassistant", "forward_actions": ["MeterValues"]}],
 "chargers": ["99224881"], "retention_days": 90}
```

### `GET /api/exchanges`
Query parameters, all optional; a duplicate key gets 400:

| Parameter | Meaning |
|---|---|
| `charger` | charger id |
| `action` | exact action name |
| `outcome` | one of the outcomes |
| `peer` | exchanges with a leg to this peer |
| `problem` | `1` |
| `tx` | primary transaction id |
| `q` | substring of a wire id, exchange id, masked idTag or transaction id |
| `exclude` | comma-separated actions, e.g. `Heartbeat` |
| `since` | epoch seconds |
| `before` | keyset cursor taken from `next` |
| `limit` | default 200, at most 200 |

Returns `{"exchanges": [summary, …], "next": "<cursor>" | null}`, newest first. It also counts the
Heartbeats that matched the other filters: `"hidden": {"Heartbeat": 312}`. That count is only bounded by
`since`, so the UI always sends a time window.

### `GET /api/exchanges/{id}`
Returns the summary plus the fields below; 404 when the id is unknown:

```json
{
  "payload": {"connectorId": 1, "transactionId": 10216, "meterValue": []},
  "frames": [
    {"at": 1760536931.21, "peer": "charger", "dir": "in", "msg_type": "call", "wire_id": "c0ffee-1",
     "payload": {}, "same_as_origin": true, "error": null}
  ],
  "decisions": [
    {"at": 1760536931.22, "peer": "stats", "reason": "QUEUED", "text": "Queued for stats during an outage; acknowledged locally",
     "detail": null}
  ]
}
```

| Field | Meaning |
|---|---|
| `payload` | The origin payload (redacted). |
| `frames[].dir` | `"in"` means received by the proxy; `"out"` means sent by it. |
| `frames[].msg_type` | `"call"`, `"result"` or `"error"`. |
| `frames[].payload` | Always filled in; it is the origin payload when `same_as_origin` is true. |
| `frames[].error` | `{"code", "description"}` for CallError frames, otherwise `null`. |

### `GET /api/sessions/{tx}`
Returns the aggregate for a primary transaction id:

```json
{"transaction_id": 10216, "charger_id": "99224881", "connector_id": 1, "id_tag": "***4F2A",
 "started_at": 1760530000.0, "stopped_at": null, "energy_wh": 7421, "exchanges": 412, "problems": 0}
```

### `GET /api/links`
Returns the live snapshot taken from the channels, plus the last 24 h of link history:

```json
{
  "chargers": [
    {"id": "99224881", "connected": true, "since": 1760500000.0,
     "peers": [{"name": "primary", "role": "primary", "up": true, "since": 1760500001.0,
                "queued": 0, "messages_last_hour": 1210}],
     "monitor": {"dropped": 0, "last_drop_at": null}}
  ],
  "monitor": {"fanout_failures": 0, "dropped_batches": 0},
  "history": [{"charger_id": "99224881", "peer": "stats", "at": 1760530000.0, "up": false}],
  "gaps": [{"charger_id": "99224881", "from_at": 1760536922.0, "to_at": 1760536924.0, "dropped": 37}]
}
```

### `GET /api/events` (Server-Sent Events)

| Event | `data` |
|---|---|
| `exchange` | an exchange summary, sent when it is created or changes |
| `link` | `{"charger_id", "peer", "at", "up"}` |
| `gap` | a gap object, as in `/api/links` |
| `reset` | empty. The client was too slow; re-fetch everything and reconnect. |

A comment line `: ping` is sent every 15 s.
