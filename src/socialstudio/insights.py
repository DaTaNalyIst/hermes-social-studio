"""Daily learning: read engagement of published posts and shift the scheduler's weights.

For each published target older than `evaluate_after_hours` (168 h for YouTube) the engagement
score is compared with the median of that platform's last 30 evaluated posts. The weekday/hour it
went out at is nudged by sqrt(ratio), clipped to [0.5, 2] and damped by the learning rate; the
neighbouring hours get half the nudge. Stories and Telegram have no API metrics and are skipped.
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import gitsync
from .config import Config
from .queue import Queue, now_utc, parse
from .scheduler import Weights

log = logging.getLogger("social")
HISTORY_KEEP = 30


def evaluate(cfg: Config, runner, now: datetime | None = None) -> dict:
    now = now or now_utc()
    L = cfg.data["learning"]
    tz = ZoneInfo(cfg.tz)
    gitsync.pull(cfg.root)
    todo = []
    for post in Queue(cfg.queue_dir).all():
        for t in post["targets"]:
            if t["status"] != "published" or t.get("evaluated") or t["lane"] == "story" or t["platform"] == "telegram":
                continue
            hours = L["youtube_evaluate_after_hours"] if t["platform"] == "youtube" else L["evaluate_after_hours"]
            if now - parse(t["published_at"]) >= timedelta(hours=hours):
                todo.append((post["id"], t))
    measured = {}
    for pid, t in todo:
        if t["platform"] not in cfg.enabled():
            continue
        try:
            m = runner.adapter(t["platform"]).metrics(t)
        except Exception as e:
            log.warning("metrics for %s %s failed: %s", pid, t["tid"], e)
            continue
        if m is not None:
            measured[(pid, t["tid"])] = m

    summary = {"evaluated": 0, "nudges": []}

    def apply():
        weights = Weights(cfg.state_dir / "weights.json")
        hist_path = cfg.state_dir / "history.json"
        hist = json.loads(hist_path.read_text()) if hist_path.exists() else {}
        q = Queue(cfg.queue_dir)
        summary["evaluated"], summary["nudges"] = 0, []
        for (pid, tid), m in measured.items():
            post = q.get(pid)
            t = next(x for x in post["targets"] if x["tid"] == tid)
            if t.get("evaluated"):
                continue
            p = t["platform"]
            past = hist.get(p, [])
            score = float(m.get("engagement", 0))
            if len(past) >= 3:
                med = statistics.median(past) or 1.0
                ratio = (score + 1.0) / (med + 1.0)
                factor = max(0.5, min(2.0, ratio ** 0.5))
                local = parse(t["published_at"]).astimezone(tz)
                for dh, rate in ((0, L["rate"]), (-1, L["rate"] / 2), (1, L["rate"] / 2)):
                    when = local + timedelta(hours=dh)
                    weights.nudge(p, when.weekday(), when.hour, factor, rate, L["min_weight"], L["max_weight"])
                summary["nudges"].append({"post": pid, "target": tid, "score": score, "median": med,
                                          "factor": round(factor, 3), "slot": f"{local:%a %H:00}"})
            hist[p] = (past + [score])[-HISTORY_KEEP:]
            t["metrics"], t["evaluated"] = m, True
            q.put(post)
            summary["evaluated"] += 1
        weights.save()
        hist_path.write_text(json.dumps(hist, indent=1) + "\n")
        return summary

    if measured:
        gitsync.transact(cfg.root, f"insights: {len(measured)} post(s)", apply)
    return summary


def report(cfg: Config, days: int = 7, now: datetime | None = None) -> dict:
    now = now or now_utc()
    since = now - timedelta(days=days)
    rows, counts = [], {}
    for post in Queue(cfg.queue_dir).all():
        for t in post["targets"]:
            if t.get("published_at") and parse(t["published_at"]) >= since:
                rows.append({"post": post["id"], "title": post["title"], "target": t["tid"],
                             "published_at": t["published_at"], "url": (t.get("result") or {}).get("url"),
                             "engagement": (t.get("metrics") or {}).get("engagement")})
            counts[t["status"]] = counts.get(t["status"], 0) + 1
    return {"days": days, "published": rows, "status_counts": counts}
