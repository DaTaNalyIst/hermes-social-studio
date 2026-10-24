#!/usr/bin/env python3
"""Run the Hermes Telegram gateway for this Kaggle session and stop when idle.

GPU time counts against your weekly quota for as long as the session runs,
even when nobody is chatting. This keeps the gateway up while you use it and
ends the session after --idle-minutes with no messages and no running jobs
(or at --max-hours), then saves Hermes' memory into /kaggle/working.

usage: python3 serve.py [--idle-minutes 30] [--max-hours 11.5] [--background]
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studiolib import common as C  # noqa: E402
from studiolib import jobs as J  # noqa: E402

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES_BIN = Path.home() / ".local" / "bin" / "social"
PID_FILE = C.STATE / "gateway.pid"
STOP_FILE = C.STATE / "stop-requested"


def env_value(key):
    if os.environ.get(key):
        return os.environ[key]
    p = HERMES_HOME / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return ""


def telegram_notify(text):
    token, users = env_value("TELEGRAM_BOT_TOKEN"), env_value("TELEGRAM_ALLOWED_USERS")
    for uid in [u.strip() for u in users.split(",") if u.strip()]:
        try:
            data = urllib.parse.urlencode({"chat_id": uid, "text": text}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15)
        except Exception as e:
            print(f"telegram notify failed: {e}", flush=True)


def last_message_time() -> float:
    """Newest message Hermes stored (any platform), falling back to DB file times."""
    db = HERMES_HOME / "state.db"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        row = con.execute("SELECT MAX(timestamp) FROM messages").fetchone()
        con.close()
        if row and row[0]:
            return float(row[0])
    except sqlite3.Error:
        pass
    times = [p.stat().st_mtime for p in (db, db.with_name("state.db-wal")) if p.exists()]
    return max(times) if times else 0.0


def gateway_running() -> int | None:
    try:
        pid = int(PID_FILE.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def start_gateway() -> int:
    pid = gateway_running()
    if pid:
        return pid
    if not env_value("TELEGRAM_BOT_TOKEN"):
        raise SystemExit("No TELEGRAM_BOT_TOKEN. Add it in Add-ons > Secrets, re-run the setup cell, then this one.")
    C.STATE.mkdir(parents=True, exist_ok=True)
    log = open(C.LOGS / "gateway.log", "a")
    p = subprocess.Popen([str(HERMES_BIN), "telegram-poll"], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, cwd=str(C.WORK), start_new_session=True)
    PID_FILE.write_text(str(p.pid))
    time.sleep(8)
    if p.poll() is not None:
        tail = (C.LOGS / "gateway.log").read_text(errors="replace")[-2000:]
        raise SystemExit(f"gateway exited at once:\n{tail}")
    return p.pid


def stop_gateway():
    pid = gateway_running()
    if not pid:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(30):
        if not gateway_running():
            break
        time.sleep(1)
    PID_FILE.unlink(missing_ok=True)


def save_state():
    """Pack Hermes' memories, sessions and skills into /kaggle/working so the next run can restore them."""
    C.STATE.mkdir(parents=True, exist_ok=True)
    out = C.STATE / "hermes_state.tgz"
    parts = [p for p in ("memories", "skills", "state.db", "SOUL.md") if (HERMES_HOME / p).exists()]
    if not parts:
        return None
    subprocess.run(["tar", "-czf", str(out), "--exclude=skills/creative/kaggle-studio",
                    "--exclude=skills/creative/social-publisher",
                    "-C", str(HERMES_HOME), *parts], check=False)
    return out


def watch(idle_minutes: float, max_hours: float, started: float):
    print(f"gateway running. Ends after {idle_minutes:.0f} idle minutes or {max_hours} h.", flush=True)
    last_seen = time.time()
    while True:
        time.sleep(30)
        busy = J.running_jobs()
        msg_t = last_message_time()
        last_seen = max(last_seen, msg_t, time.time() if busy else 0)
        idle = (time.time() - last_seen) / 60
        hours = (time.time() - started) / 3600
        if not gateway_running():
            print("gateway stopped by itself; see logs/gateway.log", flush=True)
            return "gateway exited"
        if STOP_FILE.exists():
            STOP_FILE.unlink(missing_ok=True)
            time.sleep(20)  # let Hermes finish its goodbye message
            return "you asked me to switch off"
        if idle >= idle_minutes:
            return f"idle for {idle:.0f} min"
        if hours >= max_hours and not busy:
            return f"reached {max_hours} h"
        if hours >= max_hours + 0.3:
            return f"reached {max_hours} h (a job was still running)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle-minutes", type=float, default=float(os.environ.get("STUDIO_IDLE_MINUTES", "30")))
    ap.add_argument("--max-hours", type=float, default=float(os.environ.get("STUDIO_MAX_HOURS", "11.5")))
    ap.add_argument("--background", action="store_true", help="start the gateway and return (interactive use)")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--stop-soon", action="store_true",
                    help="ask the running session to end after the current reply (Hermes uses this)")
    a = ap.parse_args()
    if a.stop_soon:
        C.STATE.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text(str(time.time()))
        print(json.dumps({"ok": True, "message": "the studio will switch off in about a minute"}))
        return
    if a.stop:
        stop_gateway()
        print("gateway stopped; state saved to", save_state())
        return
    session = C.read_json(C.STATE / "session.json", {}) or {}
    started = float(session.get("started") or time.time())
    pid = start_gateway()
    print(f"Telegram gateway started (pid {pid}). Message your bot now.", flush=True)
    if a.background:
        return
    telegram_notify("Hermes Studio is online on Kaggle. Send me what to make. "
                    f"I switch off after {a.idle_minutes:.0f} quiet minutes to save your GPU hours, "
                    "or when you tell me to switch off.")
    try:
        reason = watch(a.idle_minutes, a.max_hours, started)
    except KeyboardInterrupt:
        reason = "stopped from the notebook"
    print(f"stopping: {reason}", flush=True)
    telegram_notify(f"Hermes Studio is going offline ({reason}). " + os.environ.get(
        "STUDIO_OFFLINE_NOTE", "Your files are in this notebook's output (/kaggle/working/outputs). "
                               "Start the notebook again to continue."))
    stop_gateway()
    print("state saved to", save_state(), flush=True)
    print(json.dumps({"stopped": reason, "outputs": str(C.OUT)}), flush=True)


if __name__ == "__main__":
    main()
