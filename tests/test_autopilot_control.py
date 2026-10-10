"""Autopilot plan, outbox staging, Kaggle launch/collect and the control bot."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from socialstudio import autopilot as AP, control, kaggle as K, ops, outbox
from socialstudio.queue import Queue, parse
from socialstudio.storage import LocalStore
from socialstudio.vault import Vault

from conftest import PASS
from fakehttp import FakeHttp

# Monday 2026-10-12 03:00 UTC = 06:00 Nairobi
MONDAY_6AM = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def plan(repo):
    text = AP.TEMPLATE.replace("enabled = false", "enabled = true")
    text = text.replace('targets = ["instagram:reel", "facebook:reel", "youtube:short", "telegram:video"]',
                        'targets = ["instagram:reel", "telegram:video", "tiktok:video"]')
    AP.path(repo).write_text(text)
    return repo


@pytest.fixture
def ctl(repo):
    v = Vault(repo.vault_path, PASS)
    v.set("control.bot_token", "555555555:" + "c" * 35)
    v.set("control.allowed_users", "42")
    v.save()
    return Vault(repo.vault_path, PASS)


def test_plan_brief_and_due(plan):
    probs = AP.validate(plan, AP.load(plan))
    assert any("tiktok:video" in p for p in probs)
    b = AP.brief(plan, MONDAY_6AM.date())
    assert [s["id"] for s in b["slots"]] == ["short"]
    assert b["slots"][0]["targets"] == ["instagram:reel", "telegram:video"]  # unknown target dropped
    assert AP.is_due(plan, {"runs": []}, MONDAY_6AM - timedelta(minutes=1))[0] is False
    assert AP.is_due(plan, {"runs": []}, MONDAY_6AM) == (True, "due")
    st = {"runs": [{"mode": "autopilot", "date": "2026-10-12"}]}
    assert AP.is_due(plan, st, MONDAY_6AM)[1] == "already launched today"


def test_timeout_approval_and_not_before(plan, media, tmp_path):
    f = AP.approval_fields(plan, MONDAY_6AM)
    assert f["approve"] is False and f["auto_approve_at"] == MONDAY_6AM + timedelta(hours=3)
    spec = {"title": "T", "media": [str(media / "img0.png")], "caption": "Hi",
            "targets": [{"platform": "telegram", "format": "photo"}], "not_after": "2026-10-12 22:30"}
    post = ops.add(plan, spec, tmp_path, store=LocalStore(tmp_path / "s"), now=MONDAY_6AM,
                   auto_approve_at=f["auto_approve_at"])
    slot = parse(post["targets"][0]["slot"])
    assert slot >= f["auto_approve_at"] and slot.date() == MONDAY_6AM.date()
    assert post["approval"] == "pending" and post["auto_approve_at"]
    # nothing happens before the window ends ...
    assert ops.auto_approve_due(plan, MONDAY_6AM + timedelta(hours=2)) == []
    # ... then it is approved, unless rejected
    assert ops.auto_approve_due(plan, MONDAY_6AM + timedelta(hours=3, minutes=1)) == [post["id"]]
    assert Queue(plan.queue_dir).get(post["id"])["approval"] == "approved"


def test_reject_stops_auto_approval(plan, media, tmp_path):
    spec = {"title": "T", "media": [str(media / "img0.png")], "caption": "Hi",
            "targets": [{"platform": "telegram", "format": "photo"}]}
    post = ops.add(plan, spec, tmp_path, store=LocalStore(tmp_path / "s"), now=MONDAY_6AM,
                   auto_approve_at=MONDAY_6AM + timedelta(hours=1))
    ops.reject(plan, post["id"])
    assert ops.auto_approve_due(plan, MONDAY_6AM + timedelta(hours=5)) == []
    with pytest.raises(ValueError, match="rejected"):
        ops.approve(plan, post["id"])


def test_cli_add_stages_into_outbox(plan, media, tmp_path):
    box = tmp_path / "outbox"
    spec = tmp_path / "p.json"
    spec.write_text(json.dumps({"title": "Clip", "topic": "barista morning", "media": [str(media / "portrait.mp4")],
                                "caption": "Morning #coffee",
                                "targets": [{"platform": "instagram", "format": "reel"}]}))
    env = dict(os.environ, SOCIAL_REPO_DIR=str(plan.root), SOCIAL_OUTBOX=str(box), SOCIAL_NO_GIT="1",
               PYTHONPATH=str(Path(__file__).parent.parent / "src"), HOME=str(tmp_path))
    (tmp_path / ".social-studio.json").write_text(json.dumps({"repo": str(plan.root), "outbox": str(box),
                                                              "tag": {"autopilot": {"date": "2026-10-12", "slot": "short"}}}))
    r = subprocess.run([sys.executable, "-m", "socialstudio.cli", "--json", "add", str(spec), "--approve"],
                       capture_output=True, text=True, env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["staged"] and "queued automatically" in out["next"]
    items = outbox.items(box)
    assert len(items) == 1
    oid, d, staged = items[0]
    assert staged["media"] == ["m0.mp4"] and (d / "m0.mp4").exists()
    assert staged["autopilot"] == {"date": "2026-10-12", "slot": "short"}
    assert Queue(plan.queue_dir).all() == []  # nothing queued on Kaggle


def test_snapshot_and_notebook_never_carry_the_vault(plan):
    (plan.state_dir / "weights.json").write_text("{}")
    import io, tarfile, base64
    names = tarfile.open(fileobj=io.BytesIO(K.snapshot(plan.root))).getnames()
    assert "studio.toml" in names and "autopilot.toml" in names
    assert not any("vault" in n for n in names)
    nb = K.notebook(K.snapshot(plan.root), {"run_id": "r1", "mode": "chat"})
    src = "".join("".join(c["source"]) for c in nb["cells"])
    assert "kaggle_run.py" in src and "studio-keys.json" in src
    payload = src.split('PAYLOAD = "')[1].split('"')[0]
    assert tarfile.open(fileobj=io.BytesIO(base64.b64decode(payload))).getnames() == names


def fake_kaggle(tmp_path, monkeypatch, status="KernelWorkerStatus.COMPLETE"):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "kaggle.log"
    (bindir / "kaggle").write_text(f"""#!{sys.executable}
import json, shutil, sys, pathlib
open({str(log)!r}, "a").write(json.dumps(sys.argv[1:]) + "\\n")
a = sys.argv[1:]
if a[:2] == ["kernels", "push"]:
    d = pathlib.Path(a[a.index("-p") + 1])
    meta = json.loads((d / "kernel-metadata.json").read_text())
    shutil.copy(d / meta["code_file"], {str(tmp_path / 'pushed.ipynb')!r})
    shutil.copy(d / "kernel-metadata.json", {str(tmp_path / 'pushed-meta.json')!r})
    print("Kernel version 3 successfully pushed.")
elif a[:2] == ["kernels", "status"]:
    print(a[2] + ' has status "{status}"')
""")
    (bindir / "kaggle").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("KAGGLE_API_TOKEN", "KGAT_test")
    return log


def test_launch_pushes_private_gpu_notebook(plan, tmp_path, monkeypatch):
    log = fake_kaggle(tmp_path, monkeypatch)
    from socialstudio.cli import set_toml
    set_toml(plan.path, "kaggle", "username", '"kevo"')
    from socialstudio.config import Config
    cfg = Config(plan.root)
    run = K.launch(cfg, "autopilot", day="2026-10-12", now=MONDAY_6AM)
    meta = json.loads((tmp_path / "pushed-meta.json").read_text())
    assert meta["id"] == "kevo/hermes-studio-autopilot" and meta["is_private"] and meta["enable_gpu"]
    assert meta["enable_internet"] and meta["machine_shape"] == "NvidiaTeslaT4"
    push = json.loads(log.read_text().splitlines()[0])
    assert "--accelerator" in push and push[push.index("-t") + 1] == str(6 * 3600)
    assert run["version"] == 3 and run["date"] == "2026-10-12"
    st = K.load_state(cfg)
    assert st["runs"][0]["status"] == "launched"
    nb = json.loads((tmp_path / "pushed.ipynb").read_text())
    setup = "".join(nb["cells"][2]["source"])
    run_json = json.loads(json.loads(setup.split("json.loads(")[1].split(")\n")[0]))
    assert run_json["brief"]["slots"][0]["id"] == "short" and run_json["mode"] == "autopilot"
    # a second launch while it runs is refused
    (tmp_path / "x").mkdir()
    fake_kaggle(tmp_path / "x", monkeypatch, status="KernelWorkerStatus.RUNNING")
    with pytest.raises(K.KaggleError, match="already going"):
        K.launch(cfg, "autopilot", day="2026-10-12", now=MONDAY_6AM)


def _outputs(dest: Path, run_id: str, media: Path, ok=True, autopilot_tag=True):
    item = dest / "outbox" / "060101-abc123"
    item.mkdir(parents=True)
    import shutil
    shutil.copy(media / "img0.png", item / "m0.png")
    spec = {"title": "Rooftop cafe", "topic": "rooftop cafe at dusk", "media": ["m0.png"], "caption": "Dusk #cafe",
            "targets": [{"platform": "telegram", "format": "photo"}]}
    if autopilot_tag:
        spec["autopilot"] = {"date": "2026-10-12", "slot": "image"}
    (item / "spec.json").write_text(json.dumps(spec))
    (dest / "run_report").mkdir()
    (dest / "run_report" / "report.json").write_text(json.dumps({"run_id": run_id, "ok": ok}))
    (dest / "studio_memory").mkdir()
    (dest / "studio_memory" / "hermes_memory.tgz").write_bytes(b"\x1f\x8b fake")


def test_collect_queues_staged_posts_once(plan, media, tmp_path):
    st = {"runs": [{"run_id": "r1", "mode": "autopilot", "kernel": "kevo/hermes-studio-autopilot",
                    "launched_at": MONDAY_6AM.isoformat(), "status": "launched", "date": "2026-10-12"}]}
    K.save_state(plan, st)
    store = LocalStore(tmp_path / "store")
    reps = K.collect(plan, store, now=MONDAY_6AM + timedelta(hours=2),
                     download=lambda kernel, dest: _outputs(dest, "r1", media))
    assert len(reps[0]["posts"]) == 1 and not reps[0]["errors"]
    post = reps[0]["posts"][0]
    assert post["approval"] == "pending" and post["auto_approve_at"] and post["source"] == "kaggle:autopilot"
    assert post["topic"] == "rooftop cafe at dusk"
    assert (plan.state_dir / "hermes_memory.tgz").exists()
    assert AP.history(plan)[-1]["topic"] == "rooftop cafe at dusk"
    assert K.load_state(plan)["runs"][0]["status"] == "collected"
    # collecting again does nothing
    assert K.collect(plan, store, download=lambda k, d: None) == []
    assert len(Queue(plan.queue_dir).all()) == 1


def test_collect_reports_failures(plan, media, tmp_path):
    K.save_state(plan, {"runs": [{"run_id": "r2", "mode": "chat", "kernel": "k/c", "launched_at": MONDAY_6AM.isoformat(),
                                  "status": "launched"}]})

    def out(kernel, dest):
        (dest / "run_report").mkdir(parents=True)
        (dest / "run_report" / "report.json").write_text(json.dumps(
            {"run_id": "r2", "ok": False, "error": "no Telegram chat token reached this run"}))
    reps = K.collect(plan, LocalStore(tmp_path / "s"), now=MONDAY_6AM, download=out)
    assert "Telegram chat token" in reps[0]["errors"][0]
    assert K.load_state(plan)["runs"][0]["status"] == "failed"


# --- control bot ---------------------------------------------------------------------
def tg_http():
    h = FakeHttp()
    h.on("POST", r"/send(Message|Photo)$", {"ok": True, "result": {"message_id": 1}})
    h.on("POST", r"/editMessageReplyMarkup$", {"ok": True, "result": True})
    return h


def test_control_ignores_strangers(plan, ctl):
    h = tg_http()
    msg = control.handle(plan, ctl, {"type": "message", "text": "/studio", "user_id": "99", "chat_id": "99"}, http=h)
    assert msg.startswith("ignored") and not h.calls


def test_control_buttons_approve_and_reject(plan, ctl, media, tmp_path):
    spec = {"title": "T", "media": [str(media / "img0.png")], "caption": "Hi",
            "targets": [{"platform": "telegram", "format": "photo"}]}
    p1 = ops.add(plan, spec, tmp_path, store=LocalStore(tmp_path / "s"), now=MONDAY_6AM)
    p2 = ops.add(plan, spec, tmp_path, store=LocalStore(tmp_path / "s"), now=MONDAY_6AM)
    h = tg_http()
    control.handle(plan, ctl, {"type": "callback", "data": f"ap:{p1['id']}", "user_id": "42", "chat_id": "42",
                               "message_id": 9, "callback_id": "c"}, http=h)
    control.handle(plan, ctl, {"type": "callback", "data": f"rj:{p2['id']}", "user_id": "42", "chat_id": "42",
                               "message_id": 10, "callback_id": "c"}, http=h)
    q = Queue(plan.queue_dir)
    assert q.get(p1["id"])["approval"] == "approved" and q.get(p2["id"])["approval"] == "rejected"
    assert len(h.find("POST", "editMessageReplyMarkup")) == 2


def test_control_commands(plan, ctl, tmp_path, monkeypatch):
    fake_kaggle(tmp_path, monkeypatch)
    from socialstudio.cli import set_toml
    set_toml(plan.path, "kaggle", "username", '"kevo"')
    from socialstudio.config import Config
    cfg = Config(plan.root)
    h = tg_http()
    say = lambda text: control.handle(cfg, ctl, {"type": "message", "text": text, "user_id": "42", "chat_id": "42"},
                                      http=h, now=MONDAY_6AM)
    assert "Starting Hermes" in say("/studio 45")
    meta = json.loads((tmp_path / "pushed-meta.json").read_text())
    assert meta["id"] == "kevo/hermes-studio-chat"
    assert "chat run" in say("/status")
    assert "paused" in say("/pause")
    assert AP.load(cfg)["autopilot"]["enabled"] is False
    assert "on" in say("/resume")
    assert "Nothing scheduled" in say("/queue")
    assert "/studio" in say("/help")
    assert "Autopilot started for 2026-10-13" in say("/autopilot tomorrow")


def test_new_posts_go_to_telegram_once(plan, ctl, media, tmp_path):
    from socialstudio import approvals
    store = LocalStore(tmp_path / "s")
    spec = {"title": "Clip", "media": [str(media / "portrait.mp4")], "caption": "Morning",
            "targets": [{"platform": "instagram", "format": "reel"}]}
    post = ops.add(plan, spec, tmp_path, store=store, now=MONDAY_6AM, auto_approve_at=MONDAY_6AM + timedelta(hours=3))
    h = tg_http()
    gh = FakeHttp()
    gh.on("GET", r"/labels/", {})
    gh.on("POST", r"/issues$", {"number": 3})
    gh.on("PATCH", r"/issues/3$", {})
    import socialstudio.control as C
    orig = C.bot
    C.bot = lambda vault, http=None: C.Bot(vault.require("control.bot_token"), h)
    try:
        approvals.sync(plan, approvals.GitHub("me/r", gh, "t"), vault=ctl, store=store, now=MONDAY_6AM)
        approvals.sync(plan, approvals.GitHub("me/r", gh, "t"), vault=ctl, store=store, now=MONDAY_6AM)
    finally:
        C.bot = orig
    photos = h.find("POST", "sendPhoto")
    assert len(photos) == 1 and photos[0]["files"] == ["photo"]  # video preview frame as a photo
    assert "unless you reject" in photos[0]["data"]["caption"]
    assert json.loads(photos[0]["data"]["reply_markup"])["inline_keyboard"][0][0]["callback_data"] == f"ap:{post['id']}"
