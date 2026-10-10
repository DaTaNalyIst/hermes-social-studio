from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from socialstudio.scheduler import Scheduler, Weights, parse_local, prior_table
from socialstudio.queue import parse

NBO = ZoneInfo("Africa/Nairobi")
# Sunday 2026-10-04 08:00 Nairobi = 05:00 UTC
SUNDAY = datetime(2026, 10, 4, 5, 0, tzinfo=timezone.utc)


def local(slot):
    return parse(slot).astimezone(NBO)


def test_prior_tables_are_complete():
    for p in ("facebook", "instagram", "threads", "youtube", "bluesky", "telegram"):
        t = prior_table(p, "feed")
        assert len(t) == 7 and all(len(r) == 24 for r in t.values())
        assert max(max(r) for r in t.values()) == 1.0
    assert prior_table("facebook", "feed")[1][13] == 1.0  # Tuesday 1 pm
    assert prior_table("facebook", "feed")[5][13] < 0.5   # Saturday


def test_picks_a_research_window_in_audience_timezone(repo):
    s = Scheduler(repo)
    t = s.plan([{"platform": "facebook", "format": "reel"}], [], now=SUNDAY)[0]
    lt = local(t["slot"])
    assert lt.weekday() == 0 and lt.hour == 12  # Monday 12:00, the first 1.0 window


def test_caps_and_gaps(repo):
    s = Scheduler(repo)
    targets = [{"platform": "facebook", "format": "photo"} for _ in range(4)]
    s.plan(targets, [], now=SUNDAY)
    days = {}
    slots = sorted(parse(t["slot"]) for t in targets)
    for t in targets:
        d = local(t["slot"]).date()
        days[d] = days.get(d, 0) + 1
    assert max(days.values()) <= 2                               # daily_cap = 2
    assert all((b - a).total_seconds() >= 180 * 60 for a, b in zip(slots, slots[1:]))


def test_stories_use_their_own_lane(repo):
    s = Scheduler(repo)
    feed = s.plan([{"platform": "instagram", "format": "reel"}], [], now=SUNDAY)
    taken = [("instagram", "feed", parse(feed[0]["slot"]))]
    story = s.plan([{"platform": "instagram", "format": "story"}], taken, now=SUNDAY)[0]
    assert story["lane"] == "story" and story["slot"]


def test_quiet_hours_and_asap(repo):
    s = Scheduler(repo)
    late = datetime(2026, 10, 6, 20, 30, tzinfo=timezone.utc)  # 23:30 Nairobi
    t = s.plan([{"platform": "telegram", "format": "text", "when": "asap"}], [], now=late)[0]
    lt = local(t["slot"])
    assert lt.hour >= 6 or lt.hour == 0 and lt.minute >= 0
    assert not (0 <= lt.hour < 6)


def test_pinned_slot_is_kept(repo):
    s = Scheduler(repo)
    at = parse_local("2026-10-09 19:00", "Africa/Nairobi")
    t = s.plan([{"platform": "bluesky", "format": "text", "slot": at.isoformat(), "pinned": True}], [], now=SUNDAY)[0]
    assert parse(t["slot"]) == at


def test_learned_weights_move_the_choice(repo):
    w = Weights(repo.state_dir / "weights.json")
    for _ in range(12):
        w.nudge("facebook", 0, 12, 0.5, 0.5, 0.5, 2.0)   # Monday noon keeps underperforming
        w.nudge("facebook", 1, 18, 2.0, 0.5, 0.5, 2.0)   # Tuesday 18:00 keeps overperforming
    s = Scheduler(repo, w)
    t = s.plan([{"platform": "facebook", "format": "reel"}], [], now=SUNDAY)[0]
    lt = local(t["slot"])
    assert (lt.weekday(), lt.hour) == (1, 18)
    assert "learned 2.00" in s.explain(t["slot"], "facebook", "feed")
