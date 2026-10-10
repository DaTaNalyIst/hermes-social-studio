"""Autopilot: a weekly content plan (autopilot.toml) turned into daily Kaggle runs.

Each day at `run_at` (audience time) the publisher on GitHub Actions launches a Kaggle run with
that day's brief. Hermes makes the media and writes one post per slot; when the run finishes the
publisher collects the posts, queues them for the best times that day and asks you to approve
(or approves them itself after `approve_after_hours`, depending on `approval`).
"""

from __future__ import annotations

import json
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import FORMATS, Config

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
KINDS = {
    "short": "a narrated vertical short film of 30-45 seconds (kaggle-studio film, format short, "
             "quality draft, 5-7 scenes, at most 2 video scenes)",
    "clip": "one 5-second vertical video clip (kaggle-studio video, preset wan14b, --aspect portrait)",
    "image": "one striking image (draft 3 options with klein4b, re-render the best with zimage)",
    "carousel": "3 to 5 related images that work as a swipeable set (klein4b, consistent style)",
    "text": "no media: a short text post",
}
TEMPLATE = """# Autopilot: what Hermes makes and posts every day. No secrets here.
[autopilot]
enabled = false               # set true (or send /resume to your control bot) to start daily runs
run_at = "06:00"              # audience-local time the daily Kaggle run starts
approval = "timeout"          # required: wait for you | timeout: post unless you reject | auto
approve_after_hours = 3       # with "timeout": hours you have to reject before it is approved
language = "en"
brand = "Describe who you are in one sentence, e.g. a Nairobi coffee roaster sharing coffee culture"
audience = "Who you post for, e.g. young professionals in East Africa who love coffee"
style = "warm, cinematic, natural light, authentic African settings"
voice = "friendly and curious"
avoid = ["politics", "real, identifiable people", "medical or financial claims"]
topics = [
  "how coffee cherries are picked",
  "a barista's morning routine",
  "three ways to brew coffee at home",
]

# One [[slot]] per kind of post. days: mon..sun or "daily".
[[slot]]
id = "short"
days = ["mon", "wed", "fri"]
kind = "short"
targets = ["instagram:reel", "facebook:reel", "youtube:short", "telegram:video"]

[[slot]]
id = "image"
days = ["tue", "thu", "sat", "sun"]
kind = "image"
targets = ["instagram:image", "facebook:photo", "telegram:photo"]
"""


class PlanError(ValueError):
    pass


def path(cfg: Config) -> Path:
    return cfg.root / "autopilot.toml"


def load(cfg: Config) -> dict:
    p = path(cfg)
    if not p.exists():
        return {"autopilot": {"enabled": False}, "slot": []}
    with open(p, "rb") as f:
        plan = tomllib.load(f)
    plan.setdefault("autopilot", {})
    plan.setdefault("slot", [])
    return plan


def validate(cfg: Config, plan: dict) -> list[str]:
    probs = []
    a = plan.get("autopilot", {})
    if a.get("approval", "timeout") not in ("required", "timeout", "auto"):
        probs.append("autopilot.approval must be required, timeout or auto")
    try:
        h, m = (int(x) for x in str(a.get("run_at", "06:00")).split(":"))
        assert 0 <= h < 24 and 0 <= m < 60
    except Exception:
        probs.append("autopilot.run_at must look like 06:00")
    ids = set()
    for i, s in enumerate(plan.get("slot", [])):
        where = f"slot {i + 1} ({s.get('id', '?')})"
        if not s.get("id") or s["id"] in ids:
            probs.append(f"{where}: needs a unique id")
        ids.add(s.get("id"))
        if s.get("kind") not in KINDS:
            probs.append(f"{where}: kind must be one of {', '.join(KINDS)}")
        days = s.get("days", [])
        if days != ["daily"] and (not days or any(d not in DAYS for d in days)):
            probs.append(f"{where}: days must be a list of mon..sun, or [\"daily\"]")
        for t in s.get("targets", []):
            p, _, f = t.partition(":")
            if p not in FORMATS or f not in FORMATS[p]:
                probs.append(f"{where}: unknown target {t}")
            elif not cfg.platform(p).get("enabled"):
                probs.append(f"{where}: {p} is not connected/enabled (it will be skipped)")
        if not s.get("targets"):
            probs.append(f"{where}: no targets")
    return probs


def slots_for(plan: dict, day: date) -> list[dict]:
    wd = DAYS[day.weekday()]
    return [s for s in plan.get("slot", []) if s.get("days") == ["daily"] or wd in s.get("days", [])]


def history_path(cfg: Config) -> Path:
    return cfg.state_dir / "autopilot_history.json"


def history(cfg: Config) -> list[dict]:
    p = history_path(cfg)
    return json.loads(p.read_text()) if p.exists() else []


def record(cfg: Config, items: list[dict]):
    h = history(cfg) + items
    history_path(cfg).write_text(json.dumps(h[-200:], indent=1, ensure_ascii=False) + "\n")


def local_today(cfg: Config, now: datetime | None = None) -> date:
    return (now or datetime.now(ZoneInfo(cfg.tz))).astimezone(ZoneInfo(cfg.tz)).date()


def brief(cfg: Config, day: date) -> dict:
    """Everything the Kaggle run needs to know about one day's posts."""
    plan = load(cfg)
    a = plan["autopilot"]
    slots = []
    for s in slots_for(plan, day):
        targets = [t for t in s.get("targets", []) if t.partition(":")[0] in FORMATS
                   and t.partition(":")[2] in FORMATS[t.partition(":")[0]]
                   and cfg.platform(t.partition(":")[0]).get("enabled")]
        if targets:
            slots.append({"id": s["id"], "kind": s["kind"], "targets": targets,
                          "kind_text": KINDS[s["kind"]], "notes": s.get("notes", "")})
    recent = [h.get("topic") for h in history(cfg)[-30:] if h.get("topic")]
    return {"date": day.isoformat(), "timezone": cfg.tz, "language": a.get("language", "en"),
            "brand": a.get("brand", ""), "audience": a.get("audience", ""), "style": a.get("style", ""),
            "voice": a.get("voice", ""), "avoid": a.get("avoid", []), "topics": a.get("topics", []),
            "recent_topics": recent, "slots": slots}


def is_due(cfg: Config, state: dict, now: datetime) -> tuple[bool, str]:
    """Should the daily run start now? state = state/kaggle.json."""
    plan = load(cfg)
    a = plan["autopilot"]
    if not a.get("enabled"):
        return False, "autopilot is off"
    tz = ZoneInfo(cfg.tz)
    local = now.astimezone(tz)
    h, m = (int(x) for x in str(a.get("run_at", "06:00")).split(":"))
    if (local.hour, local.minute) < (h, m):
        return False, f"waiting for {a.get('run_at')}"
    today = local.date().isoformat()
    if any(r.get("mode") == "autopilot" and r.get("date") == today for r in state.get("runs", [])):
        return False, "already launched today"
    if not brief(cfg, local.date())["slots"]:
        return False, "nothing planned today"
    if local.hour >= 20:
        return False, "too late in the day to start"
    return True, "due"


def approval_fields(cfg: Config, now: datetime) -> dict:
    a = load(cfg)["autopilot"]
    mode = a.get("approval", "timeout")
    if mode == "auto":
        return {"approve": True}
    if mode == "timeout":
        at = now + timedelta(hours=float(a.get("approve_after_hours", 3)))
        return {"approve": False, "auto_approve_at": at}
    return {"approve": False}
