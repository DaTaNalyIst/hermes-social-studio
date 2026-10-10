"""Start Kaggle runs from GitHub Actions and collect what they made.

launch():  builds a private notebook that embeds a snapshot of this repo (code, skills, settings,
           the queue, Hermes' memory - never the vault) and pushes it with the Kaggle CLI, which
           starts it on GPU T4 x2. Modes: "autopilot" (make the day's posts, then stop) and
           "chat" (Hermes on Telegram until you go quiet).
collect(): once a run has finished, downloads its outbox and memory, queues the staged posts
           through the normal `social add` path and reports to your control bot.
The Kaggle run never receives a GitHub token or the vault passphrase.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from . import autopilot, gitsync, ops, outbox
from .config import Config
from .queue import iso, now_utc, parse
from .redact import redact

STATE = "kaggle.json"
SNAPSHOT = ["studio", "skills", "src", "pyproject.toml", "studio.toml", "autopilot.toml", "queue/posts",
            "state/weights.json", "state/hermes_memory.tgz"]
SKIP_PARTS = {"tests", "__pycache__", ".pytest_cache"}
LOST_AFTER = timedelta(hours=13)
MAX_MEMORY = 2 * 1024 * 1024


class KaggleError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Kaggle CLI
# ---------------------------------------------------------------------------
def cli(*args, check=True, timeout=600) -> str:
    exe = shutil.which("kaggle")
    if not exe:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "kaggle"], check=True)
        exe = shutil.which("kaggle") or "kaggle"
    if not (os.environ.get("KAGGLE_API_TOKEN") or os.environ.get("KAGGLE_KEY")
            or (Path.home() / ".kaggle" / "access_token").exists() or (Path.home() / ".kaggle" / "kaggle.json").exists()):
        raise KaggleError("no Kaggle credentials: add the KAGGLE_API_TOKEN secret to the GitHub repo")
    p = subprocess.run([exe, *map(str, args)], capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    if check and p.returncode != 0:
        raise KaggleError(redact(f"kaggle {' '.join(map(str, args[:3]))} failed: {out[-1200:]}"))
    return out


def parse_status(text: str) -> str:
    t = text.lower()
    for word in ("complete", "error", "cancel", "running", "queued", "new"):
        if re.search(rf'status "[^"]*{word}', t) or f"status {word}" in t:
            return {"cancel": "cancelled", "new": "queued"}.get(word, word)
    return "unknown"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def state_path(cfg: Config) -> Path:
    return cfg.state_dir / STATE


def load_state(cfg: Config) -> dict:
    p = state_path(cfg)
    return json.loads(p.read_text()) if p.exists() else {"runs": []}


def save_state(cfg: Config, st: dict):
    st["runs"] = st.get("runs", [])[-60:]
    state_path(cfg).write_text(json.dumps(st, indent=1, ensure_ascii=False) + "\n")


def kernel_id(cfg: Config, mode: str) -> str:
    k = cfg.data["kaggle"]
    user = k.get("username", "").strip()
    if not user:
        raise KaggleError("set [kaggle] username in studio.toml")
    return f"{user}/{k['autopilot_kernel' if mode == 'autopilot' else 'chat_kernel']}"


def active_runs(cfg: Config) -> list[dict]:
    return [r for r in load_state(cfg).get("runs", []) if r.get("status") in ("launched", "running", "queued")]


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
def snapshot(root: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in SNAPSHOT:
            src = root / rel
            if not src.exists():
                continue
            files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
            for f in files:
                parts = set(f.relative_to(root).parts)
                if parts & SKIP_PARTS or f.suffix in (".pyc", ".tmp") or f.name == "vault.enc":
                    continue
                info = tar.gettarinfo(str(f), arcname=str(f.relative_to(root)))
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                with open(f, "rb") as fh:
                    tar.addfile(info, fh)
    return buf.getvalue()


def notebook(payload: bytes, run: dict) -> dict:
    """A tiny notebook: unpack the snapshot, read the chat secrets, run studio/kaggle_run.py."""
    b64 = base64.b64encode(payload).decode()
    setup = f'''import base64, io, json, os, subprocess, sys, tarfile, glob
from pathlib import Path
RUN = json.loads({json.dumps(json.dumps(run))})
REPO = Path("/kaggle/working/repo")
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(PAYLOAD)), mode="r:gz") as t:
    try:
        t.extractall(REPO, filter="data")
    except TypeError:
        t.extractall(REPO)
(REPO / "run.json").write_text(json.dumps(RUN))
env = dict(os.environ)
# Telegram chat credentials: Kaggle secrets if attached, else the private keys dataset.
try:
    from kaggle_secrets import UserSecretsClient
    _s = UserSecretsClient()
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS"):
        try:
            env[k] = _s.get_secret(k)
        except Exception:
            pass
except Exception:
    pass
if not env.get("TELEGRAM_BOT_TOKEN"):
    for f in glob.glob("/kaggle/input/**/studio-keys.json", recursive=True):
        env.update({{k: v for k, v in json.load(open(f)).items() if k.startswith("TELEGRAM_")}})
        break
p = subprocess.Popen([sys.executable, str(REPO / "studio" / "kaggle_run.py"), str(REPO / "run.json")],
                     env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
for line in p.stdout:
    print(line, end="")
print("exit", p.wait())
'''
    cells = [
        {"cell_type": "markdown", "metadata": {}, "source": [
            f"# Hermes Social Studio: {run['mode']} run {run['run_id']}\n",
            "Launched automatically from GitHub. Edits here are overwritten by the next launch; "
            "change the repo instead.\n"]},
        {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
         "source": [f'PAYLOAD = "{b64}"\n']},
        {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
         "source": setup.splitlines(True)},
    ]
    return {"cells": cells, "nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                         "language_info": {"name": "python"}}}


def launch(cfg: Config, mode: str, day: str | None = None, idle_minutes: int | None = None,
           now: datetime | None = None, reason: str = "") -> dict:
    if mode not in ("autopilot", "chat"):
        raise KaggleError("mode must be autopilot or chat")
    gitsync.pull(cfg.root)
    now = now or now_utc()
    kid = kernel_id(cfg, mode)
    for r in active_runs(cfg):
        if r["kernel"] == kid:
            st = parse_status(cli("kernels", "status", kid, check=False))
            if st in ("running", "queued"):
                raise KaggleError(f"a {mode} run is already going ({r['run_id']}, started {r['launched_at']})")
    k = cfg.data["kaggle"]
    run = {"run_id": secrets.token_hex(4), "mode": mode, "kernel": kid, "brain": k.get("brain", "qwen3.5:9b"),
           "timezone": cfg.tz, "launched_at": iso(now)}
    if mode == "autopilot":
        d = day or autopilot.local_today(cfg, now).isoformat()
        run["date"] = d
        run["brief"] = autopilot.brief(cfg, datetime.fromisoformat(d).date())
        if not run["brief"]["slots"]:
            raise KaggleError(f"nothing planned for {d} (check autopilot.toml and enabled platforms)")
        max_h = float(k.get("autopilot_max_hours", 6))
    else:
        run["idle_minutes"] = int(idle_minutes or k.get("chat_idle_minutes", 30))
        max_h = float(k.get("chat_max_hours", 6))
    run["max_hours"] = max_h
    with tempfile.TemporaryDirectory() as tmp:
        slug = kid.split("/")[1]
        nb_path = Path(tmp) / f"{slug}.ipynb"
        nb_path.write_text(json.dumps(notebook(snapshot(cfg.root), run)))
        meta = {"id": kid, "title": slug.replace("-", " ").title(), "code_file": nb_path.name,
                "language": "python", "kernel_type": "notebook", "is_private": True, "enable_gpu": True,
                "enable_internet": True, "machine_shape": "NvidiaTeslaT4",
                "dataset_sources": [k["keys_dataset"]] if k.get("keys_dataset") else [],
                "competition_sources": [], "kernel_sources": [], "model_sources": []}
        (Path(tmp) / "kernel-metadata.json").write_text(json.dumps(meta, indent=1))
        out = cli("kernels", "push", "-p", tmp, "--accelerator", "NvidiaTeslaT4",
                  "-t", str(int(max_h * 3600)), timeout=900)
    record = {k_: run[k_] for k_ in ("run_id", "mode", "kernel", "launched_at") if k_ in run}
    record.update(status="launched", date=run.get("date"), reason=reason,
                  url=f"https://www.kaggle.com/code/{kid}")
    m = re.search(r"version\s+(\d+)", out, re.I)
    if m:
        record["version"] = int(m.group(1))

    def apply():
        st = load_state(cfg)
        st.setdefault("runs", []).append(record)
        save_state(cfg, st)
        return record
    return gitsync.transact(cfg.root, f"kaggle: launch {mode} {record['run_id']}", apply)


# ---------------------------------------------------------------------------
# Collect
# ---------------------------------------------------------------------------
def _queue_item(cfg: Config, store, item_dir: Path, spec: dict, run: dict, now: datetime) -> dict:
    spec = dict(spec)
    kw = {}
    ap = spec.get("autopilot")
    if ap:
        kw = autopilot.approval_fields(cfg, now)
        day = ap.get("date") or run.get("date")
        if day:
            spec.setdefault("not_before", f"{day} 07:00")
            spec.setdefault("not_after", f"{day} 22:30")
    approve = kw.pop("approve", False)
    return ops.add(cfg, spec, item_dir, store=store, approve=approve, now=now, source=f"kaggle:{run['mode']}",
                   auto_approve_at=kw.get("auto_approve_at"))


def collect(cfg: Config, store, now: datetime | None = None, download=None) -> list[dict]:
    """Collect every finished run. `download(kernel, dest)` is injectable for tests."""
    now = now or now_utc()
    gitsync.pull(cfg.root)
    reports = []
    for run in active_runs(cfg):
        status = parse_status(cli("kernels", "status", run["kernel"], check=False)) if download is None \
            else run.get("_test_status", "complete")
        if status in ("running", "queued", "unknown"):
            if now - parse(run["launched_at"]) > LOST_AFTER:
                _update_run(cfg, run["run_id"], status="lost", finished_at=iso(now))
                reports.append({"run": run, "status": "lost", "posts": [], "errors": ["no result after 13 h"]})
            continue
        rep = {"run": run, "status": status, "posts": [], "errors": []}
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            try:
                if download:
                    download(run["kernel"], dest)
                else:
                    cli("kernels", "output", run["kernel"], "-p", str(dest), "-o", "-q",
                        "--file-pattern", r"^(outbox|studio_memory|run_report)", timeout=1800)
            except KaggleError as e:
                rep["errors"].append(str(e))
            report = {}
            rp = dest / "run_report" / "report.json"
            if rp.exists():
                report = json.loads(rp.read_text())
            if report and report.get("run_id") != run["run_id"]:
                rep["errors"].append(f"output belongs to run {report.get('run_id')}, not {run['run_id']}")
                report = {}
            rep["report"] = report
            if status == "error" or (report and not report.get("ok")):
                rep["errors"].append(report.get("error") or "the Kaggle run failed (see its log on Kaggle)")
            done = set(load_state(cfg).get("collected_items", []))
            history = []
            if report:
                for oid, d, spec in outbox.items(dest / "outbox"):
                    key = f"{run['run_id']}:{oid}"
                    if key in done:
                        continue
                    try:
                        post = _queue_item(cfg, store, d, spec, run, now)
                        rep["posts"].append(post)
                        if spec.get("autopilot"):
                            history.append({"date": spec["autopilot"].get("date"), "slot": spec["autopilot"].get("slot"),
                                            "topic": spec.get("topic") or post.get("title"), "post": post["id"]})
                    except ops.AddError as e:
                        rep["errors"].append(f"{oid}: " + "; ".join(e.problems))
                    except Exception as e:
                        rep["errors"].append(f"{oid}: {e}")
                    done.add(key)
                for miss in report.get("failed_slots", []):
                    rep["errors"].append(f"slot {miss.get('slot')}: {miss.get('reason')}")
            mem = dest / "studio_memory" / "hermes_memory.tgz"

            def apply():
                if mem.exists() and mem.stat().st_size <= MAX_MEMORY:
                    shutil.copy2(mem, cfg.state_dir / "hermes_memory.tgz")
                if history:
                    autopilot.record(cfg, history)
                st = load_state(cfg)
                st["collected_items"] = sorted(done)[-500:]
                for r in st["runs"]:
                    if r["run_id"] == run["run_id"]:
                        r.update(status="failed" if rep["errors"] and not rep["posts"] else "collected",
                                 finished_at=iso(now), posts=[p["id"] for p in rep["posts"]],
                                 errors=rep["errors"][:10])
                save_state(cfg, st)
            gitsync.transact(cfg.root, f"kaggle: collect {run['run_id']} ({len(rep['posts'])} post(s))", apply)
        reports.append(rep)
    return reports


def _update_run(cfg, run_id, **fields):
    def apply():
        st = load_state(cfg)
        for r in st["runs"]:
            if r["run_id"] == run_id:
                r.update(fields)
        save_state(cfg, st)
    gitsync.transact(cfg.root, f"kaggle: {fields.get('status')} {run_id}", apply)


# ---------------------------------------------------------------------------
# Keys dataset (Telegram chat credentials for sessions started from GitHub/Telegram)
# ---------------------------------------------------------------------------
def keys_sync(cfg: Config, vault) -> str:
    user = cfg.data["kaggle"].get("username", "").strip()
    if not user:
        raise KaggleError("set [kaggle] username in studio.toml")
    token = vault.require("hermes.chat_bot_token")
    users = vault.require("hermes.chat_allowed_users")
    slug = f"{user}/hermes-studio-keys"
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        fd = os.open(d / "studio-keys.json", os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_ALLOWED_USERS": users}, f)
        (d / "dataset-metadata.json").write_text(json.dumps(
            {"title": "hermes-studio-keys", "id": slug, "licenses": [{"name": "other"}]}))
        st = cli("datasets", "status", slug, check=False).lower()
        if "ready" in st or "pending" in st:
            cli("datasets", "version", "-p", tmp, "-m", "update keys", "-q")
        else:
            cli("datasets", "create", "-p", tmp, "-q")   # private unless --public is given

    from .cli import set_toml
    gitsync.transact(cfg.root, "kaggle: keys dataset",
                     lambda: set_toml(cfg.path, "kaggle", "keys_dataset", f'"{slug}"'))
    return slug
