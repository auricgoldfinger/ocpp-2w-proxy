"""Field test 4: stop a running session, wait, and start it again remotely with your Tap card.

Answers: does the charger accept RemoteStartTransaction after a RemoteStop with the cable still in,
does it Authorize the card at Tap first, does the new meterStart equal the old meterStop,
does the current jump back to Curr HW, does the car resume, and does the charger end a
session in Finishing on its own (Cst_StaleTimeout = 1800 s).

Settings come from .env next to this script (see .env.example); NAS_SSH_HOST also from
../.env.build. Python 3 stdlib only.

Usage (car plugged in and charging):
  python3 pause_resume_test.py check          # charger status, KEBA report, running transaction
  python3 pause_resume_test.py stop [txId]    # RemoteStopTransaction (txId default: from the proxy state)
  python3 pause_resume_test.py check          # repeat now and then during the pause
  python3 pause_resume_test.py start          # RemoteStartTransaction with TAP_ID_TAG on connector 1
  python3 pause_resume_test.py check          # right after start, and again ~1 min later
  python3 pause_resume_test.py log [minutes]  # proxy monitor: exchanges + meterStop/meterStart summary

The debug endpoint talks straight to the charger: nothing goes through the proxy's policy,
so the start creates a real new session at Tap for your card (exactly what option A would do).
"""
import json
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    values = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip("'\"")
    return values


ENV = {**load_env(os.path.join(HERE, "..", ".env.build")), **load_env(os.path.join(HERE, ".env")), **os.environ}
DEBUG_URL = ENV.get("PROXY_DEBUG_URL", "http://192.168.0.10:8322").rstrip("/")
CHARGER_ID = ENV.get("CHARGER_ID", "99224881")
CHARGER_IP = ENV.get("CHARGER_IP", "192.168.0.222")
ID_TAG = ENV.get("TAP_ID_TAG", "")
NAS_SSH_HOST = ENV.get("NAS_SSH_HOST", "")
DATA_DIR = ENV.get("PROXY_DATA_DIR", "/mnt/volume/apps/ocpp-2w-proxy/data")


def command(action, payload=None, timeout=30):
    body = json.dumps({"action": action, "payload": payload or {}, "timeout": timeout}).encode()
    request = urllib.request.Request(
        f"{DEBUG_URL}/debug/chargers/{CHARGER_ID}/commands",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout + 10) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as e:
        sys.exit(f"{action}: HTTP {e.code} {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"{action}: debug endpoint unreachable ({e.reason})")
    if answer.get("status") == "CallResult":
        print(f"{action}: {json.dumps(answer['result'])}  ({answer.get('latency_ms')} ms)")
        return answer["result"]
    print(f"{action}: {json.dumps(answer)}")
    return None


def keba(report):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(f"report {report}".encode(), (CHARGER_IP, 7090))
        return json.loads(s.recvfrom(8192)[0])
    except (OSError, ValueError) as e:
        print(f"KEBA report {report}: no answer ({e})")
        return {}
    finally:
        s.close()


def on_nas(remote_command):
    if not NAS_SSH_HOST:
        sys.exit("NAS_SSH_HOST is not set (in .env or ../.env.build)")
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", NAS_SSH_HOST, remote_command], capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        sys.exit(f"ssh {NAS_SSH_HOST} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def monitor(sql):
    out = on_nas(f"sqlite3 -readonly -json {shlex.quote(DATA_DIR + '/monitor.sqlite')} {shlex.quote(sql)}")
    return json.loads(out) if out.strip() else []


def current_transaction():
    """Charger-side (Tap) transaction id of the running session, from the proxy's persisted state."""
    state = json.loads(on_nas(f"cat {shlex.quote(f'{DATA_DIR}/{CHARGER_ID}.json')}"))
    ids = [int(tx) for tx in state.get("transactions", {})]
    return max(ids) if ids else None


def check():
    print(time.strftime("%H:%M:%S"))
    r2, r3 = keba(2), keba(3)
    if r2:
        print("KEBA:", {k: r2.get(k) for k in ("State", "Plug", "Enable sys", "Max curr", "Curr HW", "Curr user", "Curr timer")})
    if r3:
        print("KEBA:", {"P (W)": r3.get("P", 0) / 1000, "I1 (A)": r3.get("I1", 0) / 1000,
                        "E pres (Wh)": r3.get("E pres", 0) / 10, "E total (Wh)": r3.get("E total", 0) / 10})
    # The charger sends the triggered messages to the proxy; `log` shows what it reported.
    command("TriggerMessage", {"requestedMessage": "StatusNotification", "connectorId": 1})
    command("TriggerMessage", {"requestedMessage": "MeterValues", "connectorId": 1})
    if NAS_SSH_HOST:
        print("running transaction (proxy state):", current_transaction())


def stop(tx):
    if tx is None:
        tx = current_transaction()
        if tx is None:
            sys.exit("no running transaction in the proxy state; pass the id: stop <txId>")
    print(f"stopping transaction {tx}")
    command("RemoteStopTransaction", {"transactionId": tx})


def start():
    if not ID_TAG:
        sys.exit("TAP_ID_TAG is not set in .env (the full idTag of your Tap card, not only the last 4 characters)")
    print(f"starting connector 1 with idTag ***{ID_TAG[-4:]}")
    command("RemoteStartTransaction", {"connectorId": 1, "idTag": ID_TAG})


def decode(hex_payload):
    if not hex_payload:
        return None
    raw = bytes.fromhex(hex_payload)
    if raw[:1] == b"\x78":  # zlib-compressed payload
        raw = zlib.decompress(raw)
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace")


def short(value, width=180):
    text = json.dumps(value) if not isinstance(value, str) else value
    return text if len(text) <= width else text[: width - 3] + "..."


def log(minutes):
    since = f"strftime('%s','now') - {float(minutes) * 60}"
    exchanges = monitor(
        "select id, started_at, origin, action, outcome, transaction_id, id_tag, headline, not_sent "
        f"from exchanges where charger_id = '{CHARGER_ID}' and started_at > {since} "
        "and action <> 'Heartbeat' order by id"
    )
    if not exchanges:
        print(f"no exchanges in the last {minutes} minutes")
        return
    first = exchanges[0]["id"]
    frames, decisions = {}, {}
    for f in monitor(f"select exchange_rowid, peer, dir, msg_type, hex(payload) as p from frames where exchange_rowid >= {first} order by at"):
        frames.setdefault(f["exchange_rowid"], []).append(f)
    for d in monitor(f"select exchange_rowid, peer, reason, detail from decisions where exchange_rowid >= {first} order by at"):
        decisions.setdefault(d["exchange_rowid"], []).append(d)

    summary = []
    for e in exchanges:
        at = time.strftime("%H:%M:%S", time.localtime(e["started_at"]))
        extra = " ".join(
            f"{k}={e[k]}" for k in ("transaction_id", "id_tag") if e.get(k) is not None
        )
        print(f"{at} {e['origin']:>13} {e['action']:<22} {e['outcome'] or '':<10} {extra} {e['headline'] or ''}".rstrip())
        if e["action"] == "MeterValues":
            continue
        for f in frames.get(e["id"], []):
            payload = decode(f["p"])
            if payload is not None and f["peer"] in ("charger", "primary"):
                print(f"           {f['peer']:>8} {f['dir']:<3} {f['msg_type']:<6} {short(payload)}")
            if not isinstance(payload, dict):
                continue
            if f["peer"] == "charger" and f["dir"] == "in" and f["msg_type"] == "call":
                if e["action"] == "StopTransaction":
                    summary.append(f"{at} stop  tx {payload.get('transactionId')} meterStop {payload.get('meterStop')} Wh, reason {payload.get('reason')}")
                elif e["action"] == "StartTransaction":
                    summary.append(f"{at} start idTag {payload.get('idTag')} meterStart {payload.get('meterStart')} Wh")
                elif e["action"] == "StatusNotification":
                    summary.append(f"{at} status {payload.get('status')}")
            elif f["peer"] == "charger" and f["dir"] == "out" and f["msg_type"] == "result" and e["action"] == "StartTransaction":
                summary.append(f"{at}   answered: tx {payload.get('transactionId')} {payload.get('idTagInfo', {}).get('status')}")
            elif f["peer"] == "primary" and f["dir"] == "in" and f["msg_type"] == "result" and e["action"] == "Authorize":
                summary.append(f"{at} Tap answered Authorize: {payload.get('idTagInfo', {}).get('status')}")
            elif f["peer"] == "charger" and f["dir"] == "in" and f["msg_type"] == "result" and e["action"].startswith("Remote"):
                summary.append(f"{at} charger answered {e['action']}: {payload.get('status')}")
        for d in decisions.get(e["id"], []):
            if d["reason"] != "DEBUG_COMMAND":
                print(f"           decision {d['peer']}: {d['reason']} {d['detail'] or ''}".rstrip())
        if e.get("not_sent") not in (None, "[]"):
            print(f"           not sent to: {e['not_sent']}")

    print("\nsummary:")
    for line in summary:
        print("  " + line)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if action == "check":
        check()
    elif action == "stop":
        stop(int(arg) if arg else None)
    elif action == "start":
        start()
    elif action == "log":
        log(float(arg) if arg else 180)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
