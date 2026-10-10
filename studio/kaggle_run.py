#!/usr/bin/env python3
"""Runs inside a Kaggle session started from GitHub (see src/socialstudio/kaggle.py).

usage: python kaggle_run.py /kaggle/working/repo/run.json

1. installs the `social` package from the snapshot and points it at the outbox,
2. installs and starts the studio (setup_studio.py) and restores Hermes' memory,
3. mode "autopilot": makes the day's posts (studio/autopilot.py)
   mode "chat":      runs Hermes on Telegram until idle (serve.py),
4. always: packs Hermes' memory and writes run_report/report.json for the collector.
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent          # repo/studio
REPO = HERE.parent
WORK = Path(os.environ.get("STUDIO_WORK", "/kaggle/working"))
OUTBOX = WORK / "outbox"
REPORT_DIR = WORK / "run_report"
MEMORY_DIR = WORK / "studio_memory"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
OUR_SKILLS = {"kaggle-studio", "social-publisher"}


def log(msg):
    print(f"[run] {msg}", flush=True)


def stream(cmd, env=None, timeout=None):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    t0 = time.time()
    for line in p.stdout:
        print(line, end="", flush=True)
        if timeout and time.time() - t0 > timeout:
            p.kill()
            raise RuntimeError(f"{cmd[1] if len(cmd) > 1 else cmd[0]} timed out")
    rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd[:3]))} exited with {rc}")


def restore_memory():
    tgz = REPO / "state" / "hermes_memory.tgz"
    if tgz.exists():
        HERMES_HOME.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tgz) as t:
            try:
                t.extractall(HERMES_HOME, filter="data")
            except TypeError:
                t.extractall(HERMES_HOME)
        log("restored Hermes' memory")


def pack_memory():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    out = MEMORY_DIR / "hermes_memory.tgz"
    with tarfile.open(out, "w:gz") as t:
        for name in ("memories", "SOUL.md"):
            if (HERMES_HOME / name).exists():
                t.add(HERMES_HOME / name, arcname=name)
        skills = HERMES_HOME / "skills"
        if skills.exists():  # skills Hermes wrote itself; ours come from the repo every time
            for d in skills.rglob("SKILL.md"):
                if d.parent.name not in OUR_SKILLS:
                    t.add(d.parent, arcname=str(d.parent.relative_to(HERMES_HOME)))
    log(f"packed memory ({out.stat().st_size // 1024} KB)")


def main(run_path):
    run = json.loads(Path(run_path).read_text())
    report = {"run_id": run["run_id"], "mode": run["mode"], "started": time.time(), "ok": False}
    try:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        stream([sys.executable, "-m", "pip", "install", "-q", "-e", str(REPO)])
        (Path.home() / ".social-studio.json").write_text(json.dumps({"repo": str(REPO), "outbox": str(OUTBOX)}))
        env = dict(os.environ, SOCIAL_REPO_DIR=str(REPO), SOCIAL_OUTBOX=str(OUTBOX), SOCIAL_NO_GIT="1",
                   STUDIO_BRAIN_MODEL=run.get("brain", "qwen3.5:9b"))
        os.environ.update(env)
        if run["mode"] == "chat" and not os.environ.get("TELEGRAM_BOT_TOKEN"):
            raise RuntimeError("no Telegram chat token reached this run: run the 'studio' workflow with mode "
                               "keys once (docs/autopilot.md), or attach the secrets to this notebook on Kaggle")
        stream([sys.executable, str(HERE / "setup_studio.py"), "--brain", run.get("brain", "qwen3.5:9b")], env=env)
        restore_memory()
        if run["mode"] == "autopilot":
            brief_path = REPO / "brief.json"
            brief_path.write_text(json.dumps(run["brief"], indent=1, ensure_ascii=False))
            res_path = WORK / "autopilot_result.json"
            stream([sys.executable, str(HERE / "autopilot.py"), str(brief_path), str(res_path)], env=env,
                   timeout=float(run.get("max_hours", 6)) * 3600 - 1200)
            res = json.loads(res_path.read_text())
            report.update(staged=res.get("staged", []), failed_slots=res.get("failed", []))
            report["ok"] = bool(res.get("staged"))
            if not report["ok"]:
                report["error"] = "no post was made"
        else:
            env["STUDIO_OFFLINE_NOTE"] = ("Anything you asked me to post is queued within about 30 minutes; "
                                          "you'll get approval messages from your control bot. "
                                          "Send /studio to your control bot to start me again.")
            stream([sys.executable, str(HERE / "serve.py"), "--idle-minutes", str(run.get("idle_minutes", 30)),
                    "--max-hours", str(max(0.5, float(run.get("max_hours", 6)) - 0.5))], env=env)
            report["ok"] = True
    except Exception as e:
        report["error"] = str(e)[:1500]
        traceback.print_exc()
    finally:
        try:
            pack_memory()
        except Exception as e:
            log(f"could not pack memory: {e}")
        report["finished"] = time.time()
        report["outbox_items"] = sorted(p.name for p in OUTBOX.iterdir()) if OUTBOX.exists() else []
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "report.json").write_text(json.dumps(report, indent=1))
        log(f"report: ok={report['ok']} items={len(report['outbox_items'])} error={report.get('error')}")


if __name__ == "__main__":
    main(sys.argv[1])
