"""Optimal-time scheduler: research prior x your learned weight x urgency, under caps and gaps.

Priors are local-time best windows from Sprout Social's 2026 analysis (Nov 2025 - Feb 2026) for
Facebook and Instagram; Threads and Bluesky borrow X's windows (text-first feeds). YouTube,
Telegram and Stories use heuristics. Learned weights (state/weights.json) come from your own
engagement and move each weekday/hour up or down between 0.5x and 2x.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import FORMATS, Config
from .queue import iso, now_utc, parse

# weekday 0 = Monday. Ranges are [start_hour, end_hour) in local time.
_SPROUT = {
    "facebook": {0: [(12, 13)], 1: [(12, 20)], 2: [(12, 20)], 3: [(12, 14), (20, 21)]},
    "instagram": {0: [(14, 16)], 1: [(13, 19)], 2: [(12, 21), (23, 24)], 3: [(12, 14)]},
    "x_like": {0: [(14, 15), (17, 18)], 1: [(12, 18)], 2: [(12, 18)], 3: [(12, 18)]},
}
_HEURISTIC = {
    "youtube": {d: [(14, 17)] for d in range(0, 4)} | {4: [(12, 15)], 5: [(9, 11)], 6: [(9, 11)]},
    "telegram": {d: [(8, 9), (12, 13), (18, 21)] for d in range(7)},
    "story": {d: [(8, 10), (12, 13), (18, 22)] for d in range(7)},
}
SOURCE = {"facebook": "sprout-2026", "instagram": "sprout-2026", "threads": "sprout-2026 (X windows)",
          "bluesky": "sprout-2026 (X windows)", "youtube": "heuristic", "telegram": "heuristic",
          "story": "heuristic"}


def prior_table(platform: str, lane: str) -> dict[int, list[float]]:
    if lane == "story":
        windows, weekend_factor = _HEURISTIC["story"], 1.0
    elif platform in ("facebook", "instagram"):
        windows, weekend_factor = _SPROUT[platform], 0.55
    elif platform in ("threads", "bluesky"):
        windows, weekend_factor = _SPROUT["x_like"], 0.55
    else:
        windows, weekend_factor = _HEURISTIC[platform], 1.0
    table = {}
    for d in range(7):
        row = []
        for h in range(24):
            base = 0.35 if 7 <= h < 23 else 0.12
            if any(a <= h < b for a, b in windows.get(d, [])):
                base = 1.0
            elif any(a - 1 <= h < b + 1 for a, b in windows.get(d, [])):
                base = 0.6  # shoulder hours next to a best window
            if d == 4 and d not in windows:
                base *= 0.8
            if d >= 5 and d not in windows:
                base *= weekend_factor
            row.append(round(base, 3))
        table[d] = row
    return table


class Weights:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, platform: str, weekday: int, hour: int) -> float:
        return float(self.data.get(platform, {}).get(f"{weekday}-{hour}", 1.0))

    def nudge(self, platform: str, weekday: int, hour: int, factor: float, rate: float, lo: float, hi: float):
        cur = self.get(platform, weekday, hour)
        new = max(lo, min(hi, (1 - rate) * cur + rate * cur * factor))
        self.data.setdefault(platform, {})[f"{weekday}-{hour}"] = round(new, 4)
        return new

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n")


class Scheduler:
    def __init__(self, cfg: Config, weights: Weights | None = None):
        self.cfg = cfg
        self.tz = ZoneInfo(cfg.tz)
        self.weights = weights or Weights(cfg.state_dir / "weights.json")
        self._priors: dict = {}

    def prior(self, platform, lane, local: datetime) -> float:
        key = (platform, lane)
        if key not in self._priors:
            self._priors[key] = prior_table(platform, lane)
        return self._priors[key][local.weekday()][local.hour]

    def score(self, platform: str, lane: str, slot_utc: datetime, earliest: datetime) -> float:
        local = slot_utc.astimezone(self.tz)
        days = max(0.0, (slot_utc - earliest).total_seconds() / 86400)
        urgency = 1.0 / (1.0 + 0.12 * days)
        return self.prior(platform, lane, local) * self.weights.get(platform, local.weekday(), local.hour) * urgency

    def _quiet(self, local: datetime) -> bool:
        a, b = self.cfg.general["quiet_hours"]
        h = local.hour
        return (a <= h < b) if a <= b else (h >= a or h < b)

    def _caps(self, platform, lane):
        p = self.cfg.platform(platform)
        if lane == "story":
            return int(p.get("story_cap", 5)), 60
        return int(p.get("daily_cap", 2)), int(p.get("min_gap_minutes", 120))

    def fits(self, platform, lane, slot: datetime, taken: list[tuple[str, str, datetime]]) -> bool:
        cap, gap = self._caps(platform, lane)
        local_day = slot.astimezone(self.tz).date()
        same = [t for (p, l, t) in taken if p == platform and l == lane]
        if sum(1 for t in same if t.astimezone(self.tz).date() == local_day) >= cap:
            return False
        return all(abs((slot - t).total_seconds()) >= gap * 60 for t in same)

    def candidates(self, earliest: datetime, latest: datetime):
        step = int(self.cfg.general.get("slot_minutes", 30))
        t = earliest.replace(second=0, microsecond=0)
        rem = t.minute % step
        if rem:
            t += timedelta(minutes=step - rem)
        while t <= latest:
            yield t
            t += timedelta(minutes=step)

    def plan(self, targets: list[dict], taken: list[tuple[str, str, datetime]], now: datetime | None = None,
             not_before: datetime | None = None, not_after: datetime | None = None) -> list[dict]:
        """Assign target['slot'] (UTC ISO) to every target without a pinned slot.
        `taken` = (platform, lane, utc datetime) already scheduled. Mutates and returns targets.
        not_after is a soft limit: if nothing fits before it, the normal window is used."""
        now = now or now_utc()
        g = self.cfg.general
        earliest = max(now + timedelta(minutes=int(g["lead_minutes"])), not_before or now)
        window_end = earliest + timedelta(days=int(g["window_days"]))
        latest = min(window_end, not_after) if not_after and not_after > earliest else window_end
        taken = list(taken)
        for t in targets:
            lane = FORMATS[t["platform"]][t["format"]]
            t["lane"] = lane
            if t.get("pinned") and t.get("slot"):
                taken.append((t["platform"], lane, parse(t["slot"])))
                continue
            asap = t.get("when") == "asap"
            best, best_score = None, -1.0
            for end in ([latest, window_end] if latest < window_end else [latest]):
                for slot in self.candidates(earliest, end):
                    if self._quiet(slot.astimezone(self.tz)) or not self.fits(t["platform"], lane, slot, taken):
                        continue
                    if asap:
                        best = slot
                        break
                    s = self.score(t["platform"], lane, slot, earliest)
                    if s > best_score + 1e-9:
                        best, best_score = slot, s
                if best is not None:
                    break
            if best is None:
                raise ValueError(f"no free slot for {t['platform']}:{t['format']} in the next "
                                 f"{g['window_days']} days (daily cap reached?) - raise daily_cap or window_days")
            t["slot"] = iso(best)
            t["score"] = round(best_score, 3) if not asap else None
            taken.append((t["platform"], lane, best))
        return targets

    def explain(self, slot_iso: str, platform: str, lane: str) -> str:
        slot = parse(slot_iso)
        local = slot.astimezone(self.tz)
        src = SOURCE["story" if lane == "story" else platform]
        return (f"{local:%a %d %b %H:%M} {self.cfg.tz} | prior {self.prior(platform, lane, local):.2f} "
                f"({src}) x learned {self.weights.get(platform, local.weekday(), local.hour):.2f}")


def parse_local(text: str, tz: str) -> datetime:
    """'2026-10-09 19:00' in the audience timezone -> aware UTC datetime."""
    dt = datetime.fromisoformat(text.strip().replace("T", " "))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc)
