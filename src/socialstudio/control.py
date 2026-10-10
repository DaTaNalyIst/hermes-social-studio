"""Your control bot on Telegram: start the studio, run autopilot, approve posts, check status.

Messages reach GitHub Actions in one of two ways:
- webhook (instant): Telegram -> a free Cloudflare Worker (launcher/worker.js) -> the `telegram`
  workflow, which runs `social telegram-handle`;
- poll (no extra account): the publisher calls getUpdates every 30 minutes (`social telegram-poll`).
Only user ids in control.allowed_users are obeyed; the Worker checks them too.

This is a different bot from the one you chat with Hermes through (that one belongs to the Kaggle
session) and it may be the same bot that posts to your Telegram channel.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import autopilot, gitsync, kaggle, ops
from .config import Config
from .http import ApiError, Http
from .queue import Queue, now_utc, parse

API = "https://api.telegram.org"
HELP = """Hermes Social Studio control

/studio - start Hermes on Kaggle (~10 min); chat with it on your Hermes bot
/studio 60 - same, switching off after 60 quiet minutes
/autopilot - make today's planned posts now
/autopilot tomorrow - make tomorrow's posts now
/status - what's running and what's queued
/queue - the next posts and their times
/pause - stop the daily autopilot runs
/resume - start them again
Approve or reject posts with the buttons under each preview."""


class Bot:
    def __init__(self, token: str, http: Http | None = None):
        self.token, self.http = token, http or Http()

    def call(self, method: str, data: dict | None = None, files=None):
        try:
            r = self.http.request("POST", f"{API}/bot{self.token}/{method}", data=data, files=files,
                                  platform="telegram")
        except ApiError as e:
            raise RuntimeError(f"telegram {method}: {e}") from None
        if not r.get("ok", False):
            raise RuntimeError(f"telegram {method}: {r.get('description')}")
        return r.get("result")


def configured(vault) -> bool:
    try:
        return bool(vault and vault.get("control.bot_token") and vault.get("control.allowed_users"))
    except Exception:
        return False


def allowed(vault) -> set[str]:
    return {u.strip() for u in str(vault.get("control.allowed_users") or "").split(",") if u.strip()}


def bot(vault, http=None) -> Bot:
    return Bot(vault.require("control.bot_token"), http)


def notify(vault, text: str, http=None, buttons=None):
    if not configured(vault):
        return
    b = bot(vault, http)
    for uid in allowed(vault):
        data = {"chat_id": uid, "text": text[:4000], "disable_web_page_preview": "true"}
        if buttons:
            data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
        try:
            b.call("sendMessage", data)
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Post previews with approve / reject buttons
# ---------------------------------------------------------------------------
def _local(cfg, ts):
    return parse(ts).astimezone(ZoneInfo(cfg.tz)).strftime("%a %d %b %H:%M") if ts else "-"


def post_text(cfg: Config, post: dict) -> str:
    lines = [f"{post.get('title') or post['id']}  ({post['id']})"]
    for t in post["targets"]:
        if t["status"] in ("planned", "failed_retry"):
            lines.append(f"• {t['tid']} - {_local(cfg, t.get('slot'))}")
    first = next((t for t in post["targets"] if t.get("caption")), None)
    if first:
        cap = first["caption"].strip().splitlines()[0][:180]
        lines.append(f"\n“{cap}”")
    if post.get("approval") == "pending":
        if post.get("auto_approve_at"):
            lines.append(f"\nPosts automatically after {_local(cfg, post['auto_approve_at'])} unless you reject it.")
        else:
            lines.append("\nWaiting for your approval.")
    else:
        lines.append("\nApproved and scheduled.")
    return "\n".join(lines)


def buttons_for(post: dict):
    if post.get("approval") != "pending":
        return [[{"text": "❌ Cancel", "callback_data": f"rj:{post['id']}"}]]
    return [[{"text": "✅ Approve", "callback_data": f"ap:{post['id']}"},
             {"text": "❌ Reject", "callback_data": f"rj:{post['id']}"}]]


def _preview_file(post: dict, store, tmp: Path) -> Path | None:
    asset = next((a for t in post["targets"] for a in t.get("assets", []) if a.get("asset_id")), None)
    if not asset or store is None:
        return None
    src = tmp / asset["name"]
    try:
        store.download(asset["asset_id"], src)
    except Exception:
        return None
    if src.suffix == ".mp4":
        jpg = tmp / "frame.jpg"
        dur = float(asset.get("duration") or 2)
        p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{min(2.0, dur / 2):.2f}",
                            "-i", str(src), "-frames:v", "1", "-vf", "scale=720:-2", str(jpg)])
        return jpg if p.returncode == 0 and jpg.exists() else None
    return src


def send_post(cfg: Config, vault, post: dict, store=None, http=None):
    if not configured(vault):
        return
    b = bot(vault, http)
    text = post_text(cfg, post)
    markup = json.dumps({"inline_keyboard": buttons_for(post)})
    with tempfile.TemporaryDirectory() as d:
        img = _preview_file(post, store, Path(d))
        for uid in allowed(vault):
            try:
                if img:
                    with open(img, "rb") as f:
                        b.call("sendPhoto", {"chat_id": uid, "caption": text[:1000], "reply_markup": markup},
                               {"photo": ("preview.jpg", f, "image/jpeg")})
                else:
                    b.call("sendMessage", {"chat_id": uid, "text": text, "reply_markup": markup})
            except RuntimeError:
                pass


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------
def normalize(update: dict) -> dict | None:
    """Raw Telegram update -> the small dict the Worker also sends."""
    if update.get("callback_query"):
        c = update["callback_query"]
        msg = c.get("message") or {}
        return {"type": "callback", "data": c.get("data", ""), "user_id": str(c["from"]["id"]),
                "chat_id": str(msg.get("chat", {}).get("id", c["from"]["id"])), "message_id": msg.get("message_id"),
                "callback_id": c.get("id")}
    m = update.get("message") or {}
    if m.get("text") and m.get("from"):
        return {"type": "message", "text": m["text"], "user_id": str(m["from"]["id"]),
                "chat_id": str(m["chat"]["id"])}
    return None


def _reply(b: Bot, chat_id: str, text: str):
    try:
        b.call("sendMessage", {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": "true"})
    except RuntimeError:
        pass


def status_text(cfg: Config) -> str:
    st = kaggle.load_state(cfg)
    a = autopilot.load(cfg)["autopilot"]
    lines = [f"Mode: {cfg.general['mode']} | autopilot: {'on at ' + str(a.get('run_at')) if a.get('enabled') else 'paused'}"
             f" ({a.get('approval', 'timeout')})"]
    act = kaggle.active_runs(cfg)
    if act:
        for r in act:
            lines.append(f"Kaggle: {r['mode']} run {r['run_id']} started {_local(cfg, r['launched_at'])}")
    else:
        last = (st.get("runs") or [None])[-1]
        lines.append("Kaggle: nothing running" + (f" (last: {last['mode']} {last['status']})" if last else ""))
    q = Queue(cfg.queue_dir).all()
    pend = [p for p in q if p.get("approval") == "pending" and any(t["status"] == "planned" for t in p["targets"])]
    planned = sum(1 for p in q if p.get("approval") == "approved" for t in p["targets"] if t["status"] == "planned")
    failed = sum(1 for p in q for t in p["targets"] if t["status"] in ("failed", "unknown"))
    lines.append(f"Queue: {planned} scheduled, {len(pend)} waiting for approval, {failed} failed")
    return "\n".join(lines)


def queue_text(cfg: Config, limit: int = 10) -> str:
    rows = []
    for p in Queue(cfg.queue_dir).all():
        if p.get("approval") == "rejected":
            continue
        for t in p["targets"]:
            if t["status"] in ("planned", "failed_retry") and t.get("slot"):
                rows.append((t["slot"], f"{_local(cfg, t['slot'])}  {t['tid']}  {p.get('title', '')[:40]}"
                             + ("  (needs approval)" if p.get("approval") == "pending" else "")))
    rows.sort()
    return "Next posts:\n" + "\n".join(r for _, r in rows[:limit]) if rows else "Nothing scheduled."


def handle(cfg: Config, vault, upd: dict, store=None, http=None, now: datetime | None = None) -> str:
    """Do what the update asks; reply on Telegram; return a short log line."""
    if not upd:
        return "ignored: empty update"
    if str(upd.get("user_id")) not in allowed(vault):
        return f"ignored: user {upd.get('user_id')} is not in control.allowed_users"
    b = bot(vault, http)
    chat = upd["chat_id"]
    if upd["type"] == "callback":
        action, _, pid = (upd.get("data") or "").partition(":")
        try:
            if action == "ap":
                post = ops.approve(cfg, pid)
                msg = f"✅ Approved {pid}.\n" + post_text(cfg, post)
            elif action == "rj":
                ops.reject(cfg, pid)
                msg = f"❌ {pid} cancelled. Nothing from it will be posted."
            else:
                return "ignored: unknown button"
        except (KeyError, ValueError) as e:
            msg = f"Could not do that: {e}"
        if upd.get("message_id"):
            try:
                b.call("editMessageReplyMarkup", {"chat_id": chat, "message_id": upd["message_id"],
                                                  "reply_markup": json.dumps({"inline_keyboard": []})})
            except RuntimeError:
                pass
        _reply(b, chat, msg)
        return msg
    text = (upd.get("text") or "").strip()
    cmd, *args = text.split() or [""]
    cmd = cmd.split("@")[0].lower()
    try:
        if cmd in ("/start", "/help"):
            msg = HELP
        elif cmd == "/studio":
            idle = int(args[0]) if args and args[0].isdigit() else None
            r = kaggle.launch(cfg, "chat", idle_minutes=idle, now=now, reason="telegram")
            msg = (f"Starting Hermes on Kaggle (run {r['run_id']}). It takes about 10 minutes; Hermes will "
                   f"message you on your Hermes bot when it's ready. It switches off after "
                   f"{idle or cfg.data['kaggle'].get('chat_idle_minutes', 30)} quiet minutes, or when you tell it to.")
        elif cmd == "/autopilot":
            day = None
            if args:
                tz = ZoneInfo(cfg.tz)
                today = (now or now_utc()).astimezone(tz).date()
                if args[0] == "tomorrow":
                    from datetime import timedelta
                    day = (today + timedelta(days=1)).isoformat()
                elif args[0] != "today":
                    day = args[0]
            r = kaggle.launch(cfg, "autopilot", day=day, now=now, reason="telegram")
            msg = (f"Autopilot started for {r['date']} (run {r['run_id']}). I'll send the posts for approval "
                   f"when Kaggle is done, usually within 1-2 hours.")
        elif cmd == "/status":
            msg = status_text(cfg)
        elif cmd == "/queue":
            msg = queue_text(cfg)
        elif cmd in ("/pause", "/resume"):
            from .cli import set_toml
            on = cmd == "/resume"
            gitsync.transact(cfg.root, f"autopilot {'on' if on else 'off'}",
                             lambda: set_toml(autopilot.path(cfg), "autopilot", "enabled", "true" if on else "false"),
                             paths=("autopilot.toml",))
            msg = "Daily autopilot is on." if on else "Daily autopilot is paused. /resume to start it again."
        else:
            msg = "I don't know that one.\n\n" + HELP
    except kaggle.KaggleError as e:
        msg = f"Kaggle: {e}"
    except Exception as e:
        msg = f"That failed: {type(e).__name__}: {e}"
    _reply(b, chat, msg)
    return msg


def poll(cfg: Config, vault, store=None, http=None) -> list[str]:
    """Poll mode: process waiting updates (used when no webhook is set)."""
    if not configured(vault) or (vault.get("control.mode") or "poll") != "poll":
        return []
    b = bot(vault, http)
    sp = cfg.state_dir / "control.json"
    st = json.loads(sp.read_text()) if sp.exists() else {}
    ups = b.call("getUpdates", {"offset": st.get("offset", 0), "timeout": 0,
                                "allowed_updates": json.dumps(["message", "callback_query"])}) or []
    out = []
    for u in ups:
        st["offset"] = u["update_id"] + 1
        n = normalize(u)
        if n and n["type"] == "callback":
            try:
                b.call("answerCallbackQuery", {"callback_query_id": n["callback_id"], "text": "On it…"})
            except RuntimeError:
                pass
        out.append(handle(cfg, vault, n, store, http))
    if ups:
        gitsync.transact(cfg.root, "control bot: offset", lambda: sp.write_text(json.dumps(st) + "\n"))
    return out


def set_webhook(vault, url: str, http=None) -> str:
    b = bot(vault, http)
    b.call("setWebhook", {"url": url, "secret_token": vault.require("control.webhook_secret"),
                          "allowed_updates": json.dumps(["message", "callback_query"]), "drop_pending_updates": "true"})
    b.call("setMyCommands", {"commands": json.dumps([
        {"command": "studio", "description": "Start Hermes on Kaggle"},
        {"command": "autopilot", "description": "Make today's posts now"},
        {"command": "status", "description": "What's running and queued"},
        {"command": "queue", "description": "Next posts"},
        {"command": "pause", "description": "Pause daily autopilot"},
        {"command": "resume", "description": "Resume daily autopilot"},
        {"command": "help", "description": "Help"}])})
    return "webhook set"
