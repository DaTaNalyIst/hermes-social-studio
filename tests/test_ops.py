"""add -> approve -> run-due -> insights -> cleanup, with a local media store and fake platforms."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from socialstudio import ops
from socialstudio.queue import Queue, iso, parse
from socialstudio.storage import LocalStore
from socialstudio.vault import Vault

from conftest import PASS
from fakehttp import FakeHttp

SUNDAY = datetime(2026, 10, 4, 5, 0, tzinfo=timezone.utc)


def spec(m, **kw):
    s = {"title": "Kenyan coffee", "media": [str(m / "portrait.mp4")], "caption": "How Kenyan coffee is grown",
         "hashtags": ["coffee", "kenya", "barista"], "language": "en",
         "targets": [{"platform": "facebook", "format": "reel"},
                     {"platform": "instagram", "format": "reel", "caption": "Kenyan coffee, seed to cup #coffee"},
                     {"platform": "youtube", "format": "short", "title": "Kenyan coffee in 60 seconds"},
                     {"platform": "bluesky", "format": "video"},
                     {"platform": "telegram", "format": "video", "when": "asap"}]}
    s.update(kw)
    return s


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "store")


def test_add_plans_shares_variants_and_waits_for_approval(repo, media, store, tmp_path):
    post = ops.add(repo, spec(media), tmp_path, store=store, now=SUNDAY)
    assert post["approval"] == "pending"
    names = {a["name"] for t in post["targets"] for a in t["assets"]}
    assert len(names) == 3  # one vertical MP4 for FB/IG/YT + size-capped files for Bluesky and Telegram
    vertical = [t for t in post["targets"] if t["assets"][0]["name"].count("mp4_vertical")]
    assert {t["platform"] for t in vertical} == {"facebook", "instagram", "youtube"}
    assert all(t["slot"] for t in post["targets"])
    tg = next(t for t in post["targets"] if t["platform"] == "telegram")
    assert parse(tg["slot"]) <= SUNDAY + timedelta(minutes=75)  # asap
    ig = next(t for t in post["targets"] if t["platform"] == "instagram")
    assert ig["caption"] == "Kenyan coffee, seed to cup #coffee"
    fb = next(t for t in post["targets"] if t["platform"] == "facebook")
    assert fb["caption"].endswith("#coffee #kenya #barista")
    saved = Queue(repo.queue_dir).get(post["id"])
    assert "path" not in saved["targets"][0]["assets"][0] and saved["targets"][0]["assets"][0]["asset_id"]
    assert len(list((tmp_path / "store" / post["id"]).iterdir())) == 3


def test_add_reports_every_problem(repo, media, tmp_path):
    bad = spec(media, targets=[{"platform": "facebook", "format": "reel", "media": [0]},
                               {"platform": "instagram", "format": "reel", "caption": "x " + "#t " * 7},
                               {"platform": "youtube", "format": "short", "title": "a" * 120},
                               {"platform": "tiktok", "format": "video"}],
               media=[str(media / "long.mp4")])
    with pytest.raises(ops.AddError) as e:
        ops.add(repo, bad, tmp_path, now=SUNDAY, preview=True)
    text = " | ".join(e.value.problems)
    assert "at most 90 s" in text and "hashtag" in text and "youtube title" in text and "unknown platform" in text


def test_auto_targets_for_images(repo, media, tmp_path):
    s = {"title": "Poster", "media": [str(media / "img0.png"), str(media / "img2.png")], "caption": "New poster",
         "targets": "auto"}
    post = ops.add(repo, s, tmp_path, preview=True, now=SUNDAY)
    fmts = {t["platform"]: t["format"] for t in post["targets"]}
    assert fmts == {"facebook": "album", "instagram": "carousel", "threads": "carousel", "bluesky": "images",
                    "telegram": "album"}


def _runner(repo, store, http):
    return ops.Runner(repo, Vault(repo.vault_path, PASS), http, store, sleep=lambda s: None)


def _ok_http():
    h = FakeHttp()
    h.on("POST", r"video_reels$", lambda c: {"video_id": "v1", "upload_url": "https://rupload.facebook.com/video-upload/v25.0/v1"}
         if c["data"]["upload_phase"] == "start" else {"success": True})
    h.on("POST", r"rupload", {"success": True})
    h.on("POST", r"/media$", {"id": "c1"})
    h.on("GET", r"/c1$", {"status_code": "FINISHED"})
    h.on("POST", r"media_publish", {"id": "m1"})
    h.on("GET", r"/m1$", {"permalink": "https://instagram.com/p/m1"})
    h.on("POST", r"oauth2", {"access_token": "ya29.x"})
    h.on("POST", r"upload/youtube", {}, headers={"Location": "https://up/s"})
    h.on("PUT", r"https://up/s", {"id": "yt1", "status": {"privacyStatus": "private"}})
    h.on("POST", r"createSession", {"accessJwt": "eyJa.eyJb.sigsigsigsig", "did": "did:plc:x", "handle": "me.bsky.social"})
    h.on("GET", r"getServiceAuth", {"token": "t"})
    h.on("POST", r"uploadVideo", {"jobId": "j", "blob": {"ref": "b"}})
    h.on("POST", r"createRecord", {"uri": "at://did:plc:x/app.bsky.feed.post/r1"})
    h.on("POST", r"/sendVideo$", {"ok": True, "result": {"message_id": 7}})
    return h


def test_nothing_publishes_before_approval_then_everything_does(repo, media, store, tmp_path):
    post = ops.add(repo, spec(media), tmp_path, store=store, now=SUNDAY)
    later = SUNDAY + timedelta(days=8)
    h = _ok_http()
    assert _runner(repo, store, h).run(now=later) == []
    ops.approve(repo, post["id"])
    rep = _runner(repo, store, h).run(now=later)
    assert {r["status"] for r in rep} == {"published"} and len(rep) == 5
    p = Queue(repo.queue_dir).get(post["id"])
    assert all(t["status"] == "published" and t["result"]["id"] for t in p["targets"])
    yt = next(t for t in p["targets"] if t["platform"] == "youtube")
    assert yt["result"]["privacy"] == "private"


def test_approve_only_some_targets(repo, media, store, tmp_path):
    post = ops.add(repo, spec(media), tmp_path, store=store, now=SUNDAY)
    p = ops.approve(repo, post["id"], ["instagram", "youtube:short"])
    status = {t["platform"]: t["status"] for t in p["targets"]}
    assert status == {"facebook": "cancelled", "instagram": "planned", "youtube": "planned",
                      "bluesky": "cancelled", "telegram": "cancelled"}


def test_transient_failure_retries_then_gives_up(repo, media, store, tmp_path):
    s = spec(media, targets=[{"platform": "telegram", "format": "video"}])
    post = ops.add(repo, s, tmp_path, store=store, approve=True, now=SUNDAY)
    h = FakeHttp().on("POST", r"/sendVideo$", {"description": "busy"}, status=502)
    later = SUNDAY + timedelta(days=8)
    statuses = [_runner(repo, store, h).run(now=later)[0]["status"] for _ in range(3)]
    assert statuses == ["failed_retry", "failed_retry", "failed"]
    t = Queue(repo.queue_dir).get(post["id"])["targets"][0]
    assert t["attempts"] == 3 and "502" in t["error"]
    ops.retry(repo, post["id"], "telegram:video", now=later)
    assert Queue(repo.queue_dir).get(post["id"])["targets"][0]["status"] == "planned"


def test_checksum_mismatch_blocks_publishing(repo, media, store, tmp_path):
    post = ops.add(repo, spec(media, targets=[{"platform": "telegram", "format": "video"}]), tmp_path, store=store,
                   approve=True, now=SUNDAY)
    a = post["targets"][0]["assets"][0]
    (tmp_path / "store" / a["asset_id"]).write_bytes(b"tampered")
    rep = _runner(repo, store, FakeHttp()).run(now=SUNDAY + timedelta(days=8))
    assert "checksum" in rep[0]["error"]


def test_dry_run_changes_nothing(repo, media, store, tmp_path):
    post = ops.add(repo, spec(media), tmp_path, store=store, approve=True, now=SUNDAY)
    rep = _runner(repo, store, FakeHttp()).run(now=SUNDAY + timedelta(days=8), dry_run=True)
    assert len(rep) == 5 and all("would publish" in r["action"] for r in rep)
    assert all(t["status"] == "planned" for t in Queue(repo.queue_dir).get(post["id"])["targets"])


def test_interrupted_publish_is_not_repeated(repo, media, store, tmp_path):
    post = ops.add(repo, spec(media, targets=[{"platform": "telegram", "format": "video"}]), tmp_path, store=store,
                   approve=True, now=SUNDAY)
    q = Queue(repo.queue_dir)
    p = q.get(post["id"])
    p["targets"][0].update(status="publishing", started_at=iso(SUNDAY + timedelta(days=7)))
    q.put(p)
    _runner(repo, store, FakeHttp()).run(now=SUNDAY + timedelta(days=8))
    t = q.get(post["id"])["targets"][0]
    assert t["status"] == "unknown" and "check the platform" in t["error"]


def test_move_now_cancel_and_cleanup(repo, media, store, tmp_path):
    post = ops.add(repo, spec(media), tmp_path, store=store, now=SUNDAY)
    p = ops.move(repo, post["id"], "instagram:reel", "2026-10-09 19:00")
    ig = next(t for t in p["targets"] if t["platform"] == "instagram")
    assert ig["pinned"] and parse(ig["slot"]).astimezone().hour >= 0
    assert parse(ig["slot"]) == datetime(2026, 10, 9, 16, 0, tzinfo=timezone.utc)
    p = ops.publish_now(repo, post["id"], "youtube", now=SUNDAY)
    assert next(t for t in p["targets"] if t["platform"] == "youtube")["slot"] == iso(SUNDAY)
    ops.reject(repo, post["id"])
    later = SUNDAY + timedelta(days=30)
    assert ops.cleanup(repo, store, now=later) == [post["id"]]
    assert not (tmp_path / "store" / post["id"]).exists()
    assert Queue(repo.queue_dir).get(post["id"])["release_deleted"]


def test_insights_shift_weights_after_history(repo, media, store, tmp_path):
    from socialstudio import insights
    post = ops.add(repo, spec(media, targets=[{"platform": "bluesky", "format": "video"}]), tmp_path,
                   store=store, approve=True, now=SUNDAY)
    q = Queue(repo.queue_dir)
    # seed history: three average posts
    (repo.state_dir / "history.json").write_text(json.dumps({"bluesky": [10, 12, 8]}))
    p = q.get(post["id"])
    p["targets"][0].update(status="published", published_at=iso(datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)),
                           result={"id": "at://did:plc:x/app.bsky.feed.post/r1"})
    q.put(p)
    h = FakeHttp()
    h.on("POST", r"createSession", {"accessJwt": "eyJa.eyJb.sigsigsigsig", "did": "did:plc:x"})
    h.on("GET", r"getPosts", {"posts": [{"likeCount": 30, "repostCount": 5, "replyCount": 4, "quoteCount": 0}]})
    s = insights.evaluate(repo, _runner(repo, store, h), now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert s["evaluated"] == 1 and s["nudges"][0]["factor"] > 1.5
    w = json.loads((repo.state_dir / "weights.json").read_text())
    assert w["bluesky"]["1-18"] > 1.0       # Tuesday 18:00 Nairobi got the boost
    assert 1.0 < w["bluesky"]["1-17"] < w["bluesky"]["1-18"]
    assert q.get(post["id"])["targets"][0]["evaluated"]
