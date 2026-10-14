# Plan: current control through the KEBA UDP interface

Status: on hold until the gate test with a charging car passes. Saved 2026-10-11.

## Context

The SolarEdge ONE EV Pro (charger `99224881`, firmware 04.00.70) accepts and stores OCPP charging
profiles but never applies them. Every variant was tried (TxProfile, TxDefaultProfile,
ChargePointMaxProfile, absolute and relative, A and W, connector 0 and 1, several stack levels):

- `SetChargingProfile` is answered `Accepted`, and `ClearChargingProfile` of that id is `Accepted`
  once, then `Unknown`, so the profile is stored;
- `GetCompositeSchedule` always returns `limit: -1` on connector 0 and 1;
- `Current.Offered` stays 20 A and the car keeps drawing about 18 A;
- every OCPP profile change makes the charger's relay click (it seems to stop and restart
  charging) without limiting anything.

The charger also speaks KEBA's UDP protocol on port 7090 (`UdpConnectionEnabled = true` in its
OCPP configuration; `report 1` returns `"Product": "ONE_EV_Pro"`). There `currtime <mA> <delay s>`
sets the current limit: the value goes to `Curr timer` and, after the delay (≥ 1 s; with 0 it is
only stored, not applied), is copied to `Curr user`, the limit in effect, and `currtime 0 1` pauses charging with the
cable in. KEBA documents `currtime` as not permanent: it is reset when the cable is unplugged,
so it is meant for frequent control and should not wear the flash. `curr`, `ena` and `failsafe`
are "permanent until reboot" and must not be used for regular control.

Goal: limit or pause charging from Home Assistant without unplugging the cable and without ending
the Tap Electric session, and without wearing the charger's relay or flash.

Test scripts: `scripts/keba_set_current.py [amps]` and `scripts/keba_pause.py`.

## Gate (before any code)

With the car charging:

1. `keba_set_current.py 6` (`currtime 6000 1`): `Max curr` in `report 2` becomes 6000 and
   `Current.Import` in the proxy log drops to about 6 A.
2. `keba_pause.py` (`currtime 0 1`): charging stops and the proxy log shows no `StopTransaction`
   (the Tap session stays open).
3. `keba_set_current.py 20`: charging resumes in the same transaction.
4. Listen to the relay: going from 6 A to 7 A with `currtime` must not click (under IEC 61851 a
   current change is only a change of the CP signal's duty cycle). Pause and resume are expected
   to click once each.

If a step fails, or 6 A to 7 A clicks, stop and re-plan.

## Hardware protection

- HA's OCPP profiles must no longer reach the charger (they click the relay and achieve nothing).
- Only pause/resume may switch the relay: at least 10 minutes between pause/resume transitions
  (configurable). A request inside that window is held and applied when the window ends, and the
  latest request wins.
- Current changes: only when the value changes by at least 1 A, at most once per 30 s
  (configurable).

## Option A (recommended): the proxy translates profiles into KEBA UDP

Opt-in per charger, for example `[chargers.current_control] type = "keba-udp"`; the host is the
charger's remote address, the port 7090.

- For the backend that owns `SetChargingProfile` (HA), intercept `SetChargingProfile` and
  `ClearChargingProfile` instead of forwarding them. Keep a small profile store (purpose, stack
  level, connector, transaction id, schedule). Answer `Accepted`, and `Accepted` or `Unknown` for
  a clear, as a charger would.
- Work out the limit in effect now: the minimum of the ChargePointMaxProfile and the
  highest-stack Tx/TxDefault profile; the current period of an Absolute, Relative or Recurring
  schedule; W converted to A at 230 V × phases. A limit below 6 A means pause (`currtime 0 1`);
  no profile means 20 A.
- Send `currtime` through the hardware guard above. Send it again after a replug or a
  `StartTransaction`, because `currtime` resets on unplug. Use a timer for the next period
  boundary. Drop a TxProfile when its transaction stops.
- Answer `GetCompositeSchedule` from the store, so HA sees the limit that is really in effect.
- New modules, one responsibility each:
  - `ocpp_2w_proxy/keba_udp.py`: async UDP client (send a command, read the reply, `report 2`);
  - `ocpp_2w_proxy/profile_limit.py`: profile store and limit evaluation, pure;
  - `ocpp_2w_proxy/current_control.py`: the glue, hooked into `session.py` where a secondary's
    calls are decided by `CommandPolicy.decide` (`policy.py`).
- Configuration in `config.py`; docs in the README, `config.example.toml`, a use case and the
  requirements catalog.
- Tests: profile evaluation (pure), the UDP client against a fake UDP server, and session
  integration (an HA `SetChargingProfile` becomes a UDP datagram; a replug sends it again).

Why A over B: the guard sits in the one place every command and every plug-in passes through, so
no automation can bypass it; HA's own current setting (and evcc later) keeps working.

## Option B: no proxy code

HA sends UDP itself, e.g.
`shell_command: echo -n "currtime {{ ma }} 1" | nc -u -w1 192.168.0.222 7090`, with an automation
that sends the limit again after a plug-in and its own hysteresis (`for:` conditions). The proxy
answers HA's profiles itself (`[secondary.policy] SetChargingProfile = "answer"`, and
`ClearChargingProfile` too), so they no longer reach the charger.

## Verification

The gate test above. For A: `uv run pytest`, then live: set HA's maximum current to 6 A →
`report 2` shows `Curr timer` 6000 and the car draws about 6 A; set 0 → charging pauses and the
Tap session stays open; replug → the limit is applied again; a fast series of changes from HA
reaches the charger at most once per 30 s, and pause/resume at most once per 10 minutes.
