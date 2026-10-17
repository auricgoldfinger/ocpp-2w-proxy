# Plan: disconnect reasons and no boot replay after a short primary blip

Status: implemented 2026-10-17 with `boot_replay_grace` defaulting to 0 (skip off) until the
reasons have been watched and the skip has been tried with Tap.

## Context

The flow monitor (`data/monitor.sqlite`, table `links`) shows the primary link to Tap (charger
`99224881`) dropping and coming back within 1–2 s: 10-16 01:12, 09:46; 10-17 10:23, 12:28, 12:58.
Every reconnect replays the cached BootNotification (`BootReplay`, `boot_replay.py`), so Tap sees
a charger "reboot" each time. The charger heartbeats every 15 min and Tap asks for
`interval: 900`, so the drops are not a heartbeat timeout on Tap's side.

The monitor records only that a link went up or down (`link_changed(peer, up)` →
`LinkRow(charger_id, peer, at, up)`), not why. `TrafficLog.link_changed` is a no-op ("logged
where they happen"), and `BackendLink.serve()` logs only when websockets raises
`ConnectionClosed`: when the proxy closes a link itself (reader cancelled), nothing is logged.

Goals:

1. Every backend link that goes down records who closed it and why, both in the log and in the
   flow monitor (`links` table, `/api/links`, the SSE `link` event, the Links view).
2. A primary link that comes back within `boot_replay_grace` seconds (default 60) of a link that
   had its boot accepted does not replay the boot. The flow monitor shows for each primary
   reconnect whether the boot was replayed or skipped.

## Is skipping the boot safe?

The replay (833ae97) was a precaution: "backends that key their charger state to the booting
connection *could* reject or ignore" calls on a connection without a boot. It has not been
observed with Tap.

| Risk | Handling |
|---|---|
| Tap rejects or ignores calls on a connection that never saw a boot | Unknown until tried. `boot_replay_grace = 0` restores today's behaviour. After the first skip, check in the flow monitor that the next Heartbeat/StatusNotification/MeterValues to the primary is `delivered` with no CallError, and that Tap's app keeps showing the charger and any running session. |
| The charger really rebooted during the outage | Its own BootNotification goes to the primary as usual (`_answer_boot`: no replayed answer to consume, so it is sent). |
| The previous link never got a boot accepted | Only a link whose boot was accepted (the replay, or the charger's own boot) or that inherited one by skipping counts. Otherwise the boot is replayed. |
| The proxy restarted | The time the last booted link was lost lives in memory only, so a restart always replays. |
| Tap's view of the clock or interval goes stale | No impact: the charger's Heartbeats keep syncing the clock and the interval doesn't change. |

Upside: Tap no longer logs a charger reboot per network blip, and no replayed boot can touch an
open transaction on Tap's side. Secondaries are out of scope: Home Assistant needs the boot on
every connection because its reconnects are usually its own restarts.

## Steps

### 1. Disconnect reason on `BackendLink` (`backend_link.py`)

- Keep `connected_since` (already there); add `last_received_at`, set in `serve()` per frame.
- `mark_closing(cause: str)`: the proxy's own cause. Set **before** the reader is cancelled,
  because cancelling it runs `serve()`'s `finally` (and so the down event) before the socket is
  closed. A no-op once `serve()` has ended or a cause is already set, so a late caller never
  overwrites the real reason. `close(cause: str | None = None)` calls it.
- `disconnect_reason` property: the proxy cause if set, else derived from the websocket's
  `protocol.close_rcvd` / `close_sent`:
  - `closed by proxy: <cause>`: call timed out, idle, shutdown (see the callers below)
  - `closed by <backend>: <code> <reason>`: the backend sent the close frame first
  - `closed by proxy: <code> <reason>`: websockets itself, e.g. `1011 keepalive ping timeout`
  - `dropped without close frame`: TCP reset or timeout, NAT, network
- `serve()` `finally` logs one line for every end, with the reason (today only `ConnectionClosed`
  is logged), then reports the down event (step 2).

Callers that pass a cause. Each one is a place where the reader is still alive when the proxy
gives up on the link; without a cause it would show as `dropped without close frame`.

- `PrimaryChannel._send_live`: `link.close(f"{call.action} not answered within {timeout:.0f} s")`
  (it calls `link.close()` directly, not `_discard`).
- `PrimaryChannel._run` / `_pump`: `_pump` returns the cause instead of a bool:
  - reader ended: `None` (the reason is already recorded);
  - the sender failed: `str(exc)` of the `BackendUnavailable` from `_deliver`
    (`primary did not confirm queued StopTransaction`). Here the reader is still running;
  - parked: `idle: no session and nothing queued`.
  `_discard(link, cause)` calls `link.mark_closing(cause)` before `self._boot.stop()` and the
  reader cancel.
- `PrimaryChannel.close()`: `_discard(self._link, "proxy shutting down")`.
- `SecondaryChannel._serve()` `finally` (the secondary has no `_connect`/`_discard`): mark before
  cancelling the reader: `proxy shutting down` when the worker is closed, the sender's
  `BackendUnavailable` text when it failed (`_delivery_failed`), `idle: no session and nothing
  queued` when the sender returned normally.

### 2. Carry the reason through the flow recorder

- `FlowRecorder.link_changed(peer, up, note: str | None = None)` in `flow.py` (Protocol and
  `FanOut`), `MonitorRecorder` and `TrafficLog`. The charger-side calls in `session.py` stay as
  they are (note `None`). `note` is a string the call site already has; a link change is rare,
  so building it there doesn't load the relay path.
  - down: `disconnect_reason`, plus uptime (from `connected_since`) and time since the last
    frame: `closed by primary: 1000 (OK) · up 29m58s · last frame 7m01s ago`. Those two figures
    show whether the drops follow an idle or a session-length timeout.
  - up (primary only, see step 3): `boot replayed` or `boot replay skipped: down 1.8s`.
- The up event moves out of `BackendLink.open()`:
  - primary: into `PrimaryChannel._connect()`, right after the link is registered and
    `self._boot.start(link)` has decided, so the note carries the boot decision. That is also
    the moment the link becomes usable.
  - secondary: to the top of `SecondaryChannel._serve()`, after `self._link = link`, no note.
- `MonitorRecorder.link_changed` puts `detail = (up, note)`; the `EventKind.LINK` comment in
  `events.py` documents the new shape.

### 3. Skip the replay after a short blip (`boot_replay.py`, `primary_channel.py`)

- `BootReplay` keeps `_booted` for the current link. It is set when:
  - the replay is accepted (`_send`, next to `self._replayed = ...`);
  - the charger's own boot is accepted on that link: `PrimaryChannel.call()` calls
    `self._boot.accepted()` when a BootNotification went out via `_send_live` and
    `is_accepted(reply)`, and the link it went out on is still `self._link`;
  - the link inherited a skip.
- `BootReplay.stop()` records `_booted_lost_at = loop.time()` if the link was booted, else
  `None`, and clears `_booted`. `stop()` runs in `_discard` right after the drop, before the
  backoff sleep, so the grace counts from the drop.
- `BootReplay.start(link) -> str | None` decides and returns the note for the up event. It skips
  when `grace > 0`, `_booted_lost_at` is set and `loop.time() - _booted_lost_at <= grace`; it
  then sets `_done` (as the "no cached boot" path does), marks the link booted, logs one INFO
  line and returns `boot replay skipped: down 1.8s`. Otherwise it replays as today and returns
  `boot replayed`; `None` when there is no cached boot.
- `PrimaryChannel._connect()`: `note = self._boot.start(link)`, then
  `self._traffic.link_changed(self.name, True, note)`.
- Config: `[primary] boot_replay_grace = 60` (seconds, `0` = always replay) next to
  `outage_grace` in `PrimaryConfig`, `PRIMARY_KEYS`, the parser and `config.example.toml`;
  passed to `BootReplay(...)`.

### 4. Flow monitor storage and API

- `db.py`: append migration 2, `ALTER TABLE links ADD COLUMN note TEXT;` to `MIGRATIONS`
  (append-only; existing rows keep `NULL`). `_INSERT_LINK` gets the fifth column.
- `rows.py`: `LinkRow.note: str | None`.
- `batch_assembler.py`: **both** link sites read the tuple, since a tuple is always truthy:
  - `fold()`: `up, note = event.detail` → `LinkRow(..., up, note)`. Today's `bool(event.detail)`
    would store every down as up.
  - `_correlate_link`: `if not event.detail[0]`. Today's `if not event.detail` would never clear
    pending replies on a link down, so they'd never turn into orphans.
- SSE `link` event: `writer.py` publishes `link._asdict()`, so `note` follows `LinkRow` with no
  change there.
- `queries.links_report`: select `note`; each history entry gets `"note"`.
- `docs/monitor_api.md`: `history` entries and the `link` SSE event gain `note` (string or null).
- `static/tests/fixtures/links.json`: add `note` to every history entry (one down with a reason,
  one primary up with `boot replay skipped: …`, the rest `null`); `contract.test.js` line 54
  expects the new key set.

### 5. Flow monitor UI

The Links view (`views/links.js`) reloads `/api/links` on every SSE `link` event, so the note
only needs to be in `/api/links` to show live.

- `components/link_history.js`: `linkSegments` keeps the `note` of the change that starts each
  segment. `timelineBar`'s `<title>` becomes
  `down 12:58:32–12:58:34 · closed by primary: 1000 (OK) · up 29m58s …`, and an up segment's
  tooltip ends with its boot note.
- `components/links_panel.js`: under each peer's timeline (or as a row after the peer row), that
  peer's outages in the last 24 h, newest first: time, duration (`formatDuration`), reason and,
  for the primary, the boot note of the reconnect. Built from the same segments. A 1–2 s blip
  is a sliver on the 24 h bar, so the tooltip alone isn't enough.

### 6. Tests

- `test_backend_link.py`: `disconnect_reason` when the backend closes with a code
  (`FakeCsms.drop_connection`), when the proxy closes with a cause, on an abrupt TCP drop, and
  that `mark_closing` after `serve()` ended keeps the first reason.
- `test_primary_channel.py`:
  - a queued call that is not confirmed: the down note says `primary did not confirm queued …`,
    not `dropped without close frame`;
  - a reconnect within the grace period: only the charger's own boot reaches the primary, the
    calls that follow go through, and the up note says `skipped`;
  - `boot_replay_grace = 0`: the boot is replayed (adapt `test_reconnect_replays_the_cached_boot`
    to set it, since the new default would skip);
  - the previous link's replay was not accepted: the boot is replayed even within the grace period;
  - the charger's own accepted boot counts as booted;
  - a reconnect after the grace period: the boot is replayed (inject the clock or use a tiny grace).
- `test_secondary_channel.py`: the up event comes from `_serve()`; the down note when the link is
  closed idle.
- Monitor:
  - `test_monitor_db.py`: migration 1 → 2 on a v1 database keeps its rows; the note is written.
  - `test_monitor_batch_assembler.py`: extend
    `test_link_down_is_stored_and_turns_a_pending_reply_into_an_orphan` to send `(False, note)`;
    it guards the truthy-tuple trap.
  - `test_monitor_queries.py` / `test_monitor_api.py`: the note round-trips to `/api/links`.
  - `test_monitor_writer.py`: the published `link` event carries `note`.
  - JS: `links.test.js` for notes in `linkSegments`, the tooltip and the outage list;
    `contract.test.js` with the new fixture.

### 7. Docs

UC-003 (the boot-replay rule), UC-008 (link history shows reasons), BP-001/BP-003 if they show
the replay, the ROADMAP module table line for `boot_replay.py`, the `boot_replay.py` module
docstring, the README config section, `docs/monitor_api.md` (step 4).

## Rollout

1. Ship steps 1, 2, 4 and 5 first (no behaviour change) and let a few drops happen: the reason
   may point to a fix upstream of the boot, e.g. an idle timeout that a keepalive setting avoids.
2. Then ship step 3 and check the first skipped boot in the flow monitor as the risk table says.
