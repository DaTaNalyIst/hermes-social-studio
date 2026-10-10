"""Request shapes for every platform flow, against a scripted HTTP layer."""

import hashlib
import hmac
import json

import pytest

from socialstudio.hosting import FacebookPhotoHost, S3Host
from socialstudio.platforms import PublishError, adapter
from socialstudio.platforms.bluesky import facets
from socialstudio.vault import Vault

from conftest import PASS
from fakehttp import FakeHttp

NOSLEEP = lambda s: None  # noqa: E731


def mk(repo, name, http, hosting=None):
    v = Vault(repo.vault_path, PASS)
    return adapter(name, repo, v, http, hosting, NOSLEEP), v


@pytest.fixture
def jpg(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8jpeg" * 100)
    return p


@pytest.fixture
def mp4(tmp_path):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"\x00mp4" * 1000)
    return p


def T(platform, fmt, caption="Hello #coffee https://example.com", **kw):
    return {"tid": f"{platform}:{fmt}", "platform": platform, "format": fmt, "caption": caption,
            "assets": [{"width": 1080, "height": 1920, "duration": 12.0}], **kw}


# --- Facebook ---------------------------------------------------------------
def test_facebook_reel_three_phase_with_appsecret_proof(repo, mp4):
    h = FakeHttp()
    h.on("POST", r"/111/video_reels$", lambda c: {"video_id": "v1", "upload_url": "https://rupload.facebook.com/video-upload/v25.0/v1"}
         if c["data"]["upload_phase"] == "start" else {"success": True})
    h.on("POST", r"rupload\.facebook\.com/video-upload/v25.0/v1", {"success": True})
    fb, v = mk(repo, "facebook", h)
    res = fb.publish(T("facebook", "reel"), [mp4], {"title": "t"})
    assert res["url"] == "https://www.facebook.com/reel/v1"
    start, finish = h.find("POST", r"video_reels$")
    assert start["data"] == {"upload_phase": "start"}
    assert finish["data"]["upload_phase"] == "finish" and finish["data"]["video_state"] == "PUBLISHED"
    up = h.find("POST", r"rupload")[0]
    assert up["headers"]["offset"] == "0" and up["headers"]["file_size"] == str(mp4.stat().st_size)
    assert up["headers"]["Authorization"].startswith("OAuth ")
    tok = v.get("facebook.page_token")
    want = hmac.new(b"appsecret123456", tok.encode(), hashlib.sha256).hexdigest()
    assert start["params"]["appsecret_proof"] == want


def test_facebook_album_and_photo_story(repo, jpg):
    h = FakeHttp()
    h.on("POST", r"/111/photos$", lambda c: {"id": f"ph{len(h.find('POST', 'photos'))}"})
    h.on("POST", r"/111/feed$", {"id": "111_9"})
    h.on("POST", r"/111/photo_stories$", {"success": True, "post_id": "st1"})
    fb, _ = mk(repo, "facebook", h)
    res = fb.publish(T("facebook", "album"), [jpg, jpg], {})
    feed = h.find("POST", r"/feed$")[0]
    assert json.loads(feed["data"]["attached_media[1]"]) == {"media_fbid": "ph2"}
    assert all(c["data"]["published"] == "false" for c in h.find("POST", "photos"))
    assert res["url"] == "https://www.facebook.com/111_9"
    res = fb.publish(T("facebook", "story"), [jpg], {})
    assert h.find("POST", "photo_stories")[0]["data"] == {"photo_id": "ph3"} and res["id"] == "st1"


def test_facebook_transient_meta_error_is_retryable(repo):
    h = FakeHttp().on("POST", r"/feed$", {"error": {"code": 4, "message": "rate"}}, status=400)
    fb, _ = mk(repo, "facebook", h)
    with pytest.raises(PublishError) as e:
        fb.publish(T("facebook", "post"), [], {})
    assert e.value.transient


# --- Instagram ----------------------------------------------------------------
def test_instagram_reel_resumable_and_publish(repo, mp4):
    h = FakeHttp()
    h.on("POST", r"/17841400000000000/media$", {"id": "c1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/c1"})
    h.on("POST", r"ig-api-upload/v25.0/c1", {"success": True})
    h.on("GET", r"/c1$", [{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}])
    h.on("POST", r"/media_publish$", {"id": "m1"})
    h.on("GET", r"/m1$", {"permalink": "https://www.instagram.com/reel/abc/"})
    ig, _ = mk(repo, "instagram", h)
    res = ig.publish(T("instagram", "reel"), [mp4], {})
    create = h.find("POST", r"/media$")[0]["data"]
    assert create["media_type"] == "REELS" and create["upload_type"] == "resumable" and create["share_to_feed"] == "true"
    assert h.find("POST", "media_publish")[0]["data"] == {"creation_id": "c1"}
    assert len(h.find("GET", r"/c1$")) == 2
    assert res["url"].startswith("https://www.instagram.com/reel/")


def test_instagram_carousel_via_facebook_photo_hosting(repo, jpg):
    h = FakeHttp()
    h.on("POST", r"/111/photos$", lambda c: {"id": f"ph{len(h.find('POST', 'photos'))}"})
    h.on("GET", r"/ph\d$", {"images": [{"width": 320, "source": "https://small"}, {"width": 1080, "source": "https://scontent.fbcdn/big.jpg"}]})
    h.on("DELETE", r"/ph\d$", {"success": True})
    h.on("POST", r"/17841400000000000/media$", lambda c: {"id": "car" if c["data"].get("media_type") == "CAROUSEL" else f"k{len(h.find('POST', '/media$'))}"})
    h.on("GET", r"/(k\d|car)$", {"status_code": "FINISHED"})
    h.on("POST", r"/media_publish$", {"id": "m2"})
    h.on("GET", r"/m2$", {"permalink": "https://www.instagram.com/p/x/"})
    fb, _ = mk(repo, "facebook", h)
    ig, _ = mk(repo, "instagram", h, FacebookPhotoHost(fb))
    ig.publish(T("instagram", "carousel", caption="Two"), [jpg, jpg], {})
    kids = [c for c in h.find("POST", r"/media$") if c["data"].get("is_carousel_item")]
    assert len(kids) == 2 and all(k["data"]["image_url"] == "https://scontent.fbcdn/big.jpg" for k in kids)
    car = [c for c in h.find("POST", r"/media$") if c["data"].get("media_type") == "CAROUSEL"][0]
    assert car["data"]["children"] == "k1,k2" and car["data"]["caption"] == "Two"
    assert len(h.find("DELETE", r"/ph\d$")) == 2  # temporary photos removed


def test_instagram_image_without_hosting_fails_clearly(repo, jpg):
    ig, _ = mk(repo, "instagram", FakeHttp())
    with pytest.raises(PublishError, match="public URL"):
        ig.publish(T("instagram", "image"), [jpg], {})


# --- Threads ------------------------------------------------------------------
def test_threads_video_through_s3_and_cleanup(repo, mp4):
    h = FakeHttp()
    h.on("PUT", r"backblazeb2\.com/social-media/social-tmp/", None)
    h.on("DELETE", r"backblazeb2\.com/social-media/social-tmp/", None)
    h.on("POST", r"/999/threads$", {"id": "tc1"})
    h.on("GET", r"/tc1$", {"status": "FINISHED"})
    h.on("POST", r"/999/threads_publish$", {"id": "tm1"})
    h.on("GET", r"/tm1$", {"permalink": "https://www.threads.com/@me/post/x"})
    v = Vault(repo.vault_path, PASS)
    host = S3Host(repo.data["hosting"], v, h)
    th = adapter("threads", repo, v, h, host, NOSLEEP)
    res = th.publish(T("threads", "video", caption="Clip #coffee"), [mp4], {})
    create = h.find("POST", r"/threads$")[0]["data"]
    assert create["media_type"] == "VIDEO" and "X-Amz-Signature=" in create["video_url"]
    assert "X-Amz-Expires=3600" in create["video_url"]
    assert h.find("DELETE", "social-tmp") and res["url"].startswith("https://www.threads.com/")


def test_threads_refreshes_old_token(repo):
    h = FakeHttp().on("GET", r"graph\.threads\.net/refresh_access_token$", {"access_token": "THfresh" + "b" * 40,
                                                                          "expires_in": 5184000})
    th, v = mk(repo, "threads", h)
    v.set("threads.token_created", "1")
    assert th.refresh() is True
    call = h.calls[0]
    assert call["params"]["grant_type"] == "th_refresh_token"
    assert v.get("threads.token").startswith("THfresh")


# --- YouTube ------------------------------------------------------------------
def test_youtube_short_upload_private_until_audited(repo, mp4):
    h = FakeHttp()
    h.on("POST", r"oauth2\.googleapis\.com/token", {"access_token": "ya29.access"})
    h.on("POST", r"upload/youtube/v3/videos", {}, headers={"Location": "https://upload.example/session1"})
    h.on("PUT", r"upload\.example/session1", {"id": "yt1", "status": {"privacyStatus": "private"}})
    yt, _ = mk(repo, "youtube", h)
    res = yt.publish(T("youtube", "short", caption="Coffee", title="Kenyan coffee"), [mp4], {"language": "en"})
    init = h.find("POST", "upload/youtube")[0]
    assert init["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    st = init["json"]["status"]
    assert st["privacyStatus"] == "private" and st["containsSyntheticMedia"] is True
    assert "#Shorts" in init["json"]["snippet"]["description"]
    assert res["url"] == "https://youtube.com/shorts/yt1" and "audited" in res["note"]
    assert h.find("PUT", "session1")[0]["headers"]["Content-Type"] == "video/mp4"


def test_youtube_expired_refresh_token_message(repo, mp4):
    h = FakeHttp().on("POST", r"oauth2", {"error": "invalid_grant"}, status=400)
    yt, _ = mk(repo, "youtube", h)
    with pytest.raises(PublishError, match="7 days"):
        yt.publish(T("youtube", "video", title="x"), [mp4], {})


# --- Bluesky ------------------------------------------------------------------
def test_bluesky_facets_use_utf8_byte_offsets():
    text = "Kahawa ☕ ni tamu #coffee https://example.com/a?b=1."
    f = facets(text)
    b = text.encode()
    link = next(x for x in f if x["features"][0]["$type"].endswith("#link"))
    tag = next(x for x in f if x["features"][0]["$type"].endswith("#tag"))
    assert b[link["index"]["byteStart"]:link["index"]["byteEnd"]] == b"https://example.com/a?b=1"
    assert b[tag["index"]["byteStart"]:tag["index"]["byteEnd"]] == b"#coffee"
    assert tag["features"][0]["tag"] == "coffee"


def _bsky_session(h):
    h.on("POST", r"createSession", {"accessJwt": "eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJlX19f", "did": "did:plc:abc",
                                    "handle": "me.bsky.social", "didDoc": {"service": [
                                        {"id": "#atproto_pds", "serviceEndpoint": "https://morel.us-east.host.bsky.network"}]}})
    h.on("POST", r"createRecord", lambda c: {"uri": "at://did:plc:abc/app.bsky.feed.post/3kxyz", "cid": "c"})


def test_bluesky_video_via_video_service(repo, mp4):
    h = FakeHttp()
    _bsky_session(h)
    h.on("GET", r"getServiceAuth", {"token": "svc"})
    h.on("POST", r"video\.bsky\.app/xrpc/app\.bsky\.video\.uploadVideo", {"jobId": "j1", "state": "JOB_STATE_CREATED"})
    h.on("GET", r"getJobStatus", [{"jobStatus": {"state": "JOB_STATE_ENCODING"}},
                                  {"jobStatus": {"state": "JOB_STATE_COMPLETED", "blob": {"$type": "blob", "ref": "x"}}}])
    bs, _ = mk(repo, "bluesky", h)
    res = bs.publish(T("bluesky", "video", caption="Clip"), [mp4], {"language": "en"})
    aud = h.find("GET", "getServiceAuth")[0]["params"]
    assert aud["aud"] == "did:web:morel.us-east.host.bsky.network" and aud["lxm"] == "com.atproto.repo.uploadBlob"
    rec = h.find("POST", "createRecord")[0]["json"]["record"]
    assert rec["embed"]["$type"] == "app.bsky.embed.video" and rec["embed"]["aspectRatio"] == {"width": 1080, "height": 1920}
    assert rec["langs"] == ["en"]
    assert h.find("POST", "createRecord")[0]["url"].startswith("https://morel.us-east.host.bsky.network/xrpc/")
    assert res["url"] == "https://bsky.app/profile/me.bsky.social/post/3kxyz"


def test_bluesky_video_falls_back_to_pds_blob(repo, mp4):
    h = FakeHttp()
    _bsky_session(h)
    h.on("GET", r"getServiceAuth", {"error": "x"}, status=400)
    h.on("POST", r"uploadBlob", {"blob": {"$type": "blob", "ref": "y"}})
    bs, _ = mk(repo, "bluesky", h)
    bs.publish(T("bluesky", "video", caption="Clip"), [mp4], {})
    assert h.find("POST", "uploadBlob")[0]["headers"]["Content-Type"] == "video/mp4"


# --- Telegram -----------------------------------------------------------------
def test_telegram_album_and_link(repo, jpg):
    h = FakeHttp().on("POST", r"/sendMediaGroup$", {"ok": True, "result": [{"message_id": 42}, {"message_id": 43}]})
    tg, _ = mk(repo, "telegram", h)
    res = tg.publish(T("telegram", "album", caption="Two photos"), [jpg, jpg], {})
    c = h.calls[0]
    media = json.loads(c["data"]["media"])
    assert media[0]["caption"] == "Two photos" and "caption" not in media[1]
    assert c["files"] == ["f0", "f1"] and res["url"] == "https://t.me/mychannel/42"


def test_telegram_api_failure(repo):
    h = FakeHttp().on("POST", r"/sendMessage$", {"ok": False, "description": "chat not found"})
    tg, _ = mk(repo, "telegram", h)
    with pytest.raises(PublishError, match="chat not found"):
        tg.publish(T("telegram", "text"), [], {})
