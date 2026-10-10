"""Facebook Page and Instagram (Facebook Login) adapters on the Meta Graph API.

Facebook: feed posts, photos, albums, videos, Reels (3-phase rupload), photo and video Stories.
Instagram: images, carousels, Reels and video Stories (resumable binary upload to rupload),
image Stories and feed images via a public URL from the hosting module.
Every call carries appsecret_proof.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from ..http import ApiError
from .base import Platform, PublishError

# Meta error codes worth retrying later (rate limits, temporary issues)
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 368, 613, 9004, 9007}


def _transient(e: ApiError) -> bool:
    if e.transient:
        return True
    try:
        return int(e.body.get("error", {}).get("code")) in TRANSIENT_CODES
    except Exception:
        return False


class _Graph(Platform):
    @property
    def ver(self) -> str:
        return self.cfg.platform("facebook").get("graph_version", "v25.0")

    @property
    def base(self) -> str:
        return f"https://graph.facebook.com/{self.ver}"

    @property
    def token(self) -> str:
        return self.vault.require("facebook.page_token")

    def proof(self, token: str | None = None) -> str:
        secret = self.vault.require("facebook.app_secret")
        return hmac.new(secret.encode(), (token or self.token).encode(), hashlib.sha256).hexdigest()

    def call(self, method: str, path: str, params=None, data=None, files=None, base=None):
        tok = self.token
        q = dict(params or {})
        q.update(access_token=tok, appsecret_proof=self.proof(tok))
        url = path if path.startswith("http") else f"{base or self.base}/{path.lstrip('/')}"
        try:
            if method == "GET":
                return self.http.request("GET", url, params=q, platform=self.name)
            return self.http.request(method, url, params=q, data=data, files=files, platform=self.name)
        except ApiError as e:
            raise PublishError(str(e), transient=_transient(e)) from None

    def rupload(self, url: str, path: Path):
        size = Path(path).stat().st_size
        with open(path, "rb") as f:
            body = f.read()
        try:
            return self.http.request("POST", url, body=body, platform=self.name,
                                     headers={"Authorization": f"OAuth {self.token}", "offset": "0",
                                              "file_size": str(size)})
        except ApiError as e:
            raise PublishError(str(e), transient=_transient(e)) from None


class Facebook(_Graph):
    name = "facebook"
    required = ("facebook.page_token", "facebook.page_id", "facebook.app_secret")

    @property
    def page(self) -> str:
        return self.vault.require("facebook.page_id")

    def check(self) -> str:
        r = self.call("GET", self.page, {"fields": "id,name"})
        return f"Page {r.get('name')} ({r.get('id')})"

    # -- helpers -------------------------------------------------------------
    def upload_photo(self, path: Path, published: bool, caption: str = "") -> dict:
        with open(path, "rb") as f:
            data = {"published": "true" if published else "false"}
            if caption:
                data["caption"] = caption
            return self.call("POST", f"{self.page}/photos", data=data,
                             files={"source": (Path(path).name, f, "image/jpeg")})

    def photo_url(self, photo_id: str) -> str:
        r = self.call("GET", photo_id, {"fields": "images"})
        imgs = sorted(r.get("images", []), key=lambda i: -int(i.get("width", 0)))
        if not imgs:
            raise PublishError("facebook: no image URL for the hosted photo")
        return imgs[0]["source"]

    def delete(self, obj_id: str):
        try:
            self.call("DELETE", obj_id)
        except PublishError:
            pass

    def _three_phase(self, edge: str, path: Path, finish: dict) -> dict:
        start = self.call("POST", f"{self.page}/{edge}", data={"upload_phase": "start"})
        vid, url = start["video_id"], start.get("upload_url") or \
            f"https://rupload.facebook.com/video-upload/{self.ver}/{start['video_id']}"
        up = self.rupload(url, path)
        if up and up.get("success") is False:
            raise PublishError(f"facebook: upload rejected: {up}")
        done = self.call("POST", f"{self.page}/{edge}", data={"upload_phase": "finish", "video_id": vid, **finish})
        done["video_id"] = vid
        return done

    # -- publish ---------------------------------------------------------------
    def publish(self, target, files, post):
        fmt, cap = target["format"], target.get("caption", "")
        if fmt == "post":
            data = {"message": cap}
            if target.get("link"):
                data["link"] = target["link"]
            r = self.call("POST", f"{self.page}/feed", data=data)
            return {"id": r["id"], "url": f"https://www.facebook.com/{r['id']}"}
        if fmt == "photo":
            r = self.upload_photo(files[0], True, cap)
            pid = r.get("post_id") or r["id"]
            return {"id": pid, "photo_id": r["id"], "url": f"https://www.facebook.com/{pid}"}
        if fmt == "album":
            ids = [self.upload_photo(f, False)["id"] for f in files]
            data = {"message": cap}
            for i, fid in enumerate(ids):
                data[f"attached_media[{i}]"] = json.dumps({"media_fbid": fid})
            r = self.call("POST", f"{self.page}/feed", data=data)
            return {"id": r["id"], "url": f"https://www.facebook.com/{r['id']}"}
        if fmt == "video":
            with open(files[0], "rb") as f:
                r = self.call("POST", f"{self.page}/videos", data={"description": cap, "title": post.get("title", "")[:250]},
                              files={"source": (Path(files[0]).name, f, "video/mp4")},
                              base=f"https://graph-video.facebook.com/{self.ver}")
            return {"id": r["id"], "url": f"https://www.facebook.com/{self.page}/videos/{r['id']}"}
        if fmt == "reel":
            r = self._three_phase("video_reels", files[0], {"video_state": "PUBLISHED", "description": cap})
            return {"id": r["video_id"], "url": f"https://www.facebook.com/reel/{r['video_id']}"}
        if fmt == "story":
            if Path(files[0]).suffix == ".mp4":
                r = self._three_phase("video_stories", files[0], {})
                return {"id": r.get("post_id") or r["video_id"], "url": None}
            photo = self.upload_photo(files[0], False)
            r = self.call("POST", f"{self.page}/photo_stories", data={"photo_id": photo["id"]})
            return {"id": r.get("post_id") or photo["id"], "url": None}
        raise PublishError(f"facebook: unknown format {fmt}")

    def metrics(self, target):
        rid = target["result"]["id"]
        if target["format"] == "story":
            return None
        try:
            r = self.call("GET", rid, {"fields": "reactions.summary(total_count),comments.summary(total_count),shares"})
            likes = r.get("reactions", {}).get("summary", {}).get("total_count", 0)
        except PublishError:  # videos/reels expose likes instead of reactions
            r = self.call("GET", rid, {"fields": "likes.summary(true),comments.summary(true)"})
            likes = r.get("likes", {}).get("summary", {}).get("total_count", 0)
        comments = r.get("comments", {}).get("summary", {}).get("total_count", 0)
        shares = r.get("shares", {}).get("count", 0)
        return {"likes": likes, "comments": comments, "shares": shares,
                "engagement": likes + 2 * comments + 3 * shares}


class Instagram(_Graph):
    name = "instagram"
    required = ("facebook.page_token", "facebook.app_secret", "instagram.user_id")

    @property
    def ig(self) -> str:
        return self.vault.require("instagram.user_id")

    def check(self) -> str:
        r = self.call("GET", self.ig, {"fields": "username"})
        lim = self.call("GET", f"{self.ig}/content_publishing_limit", {"fields": "quota_usage,config"})
        used = (lim.get("data") or [{}])[0].get("quota_usage", "?")
        return f"@{r.get('username')} (published in last 24 h: {used})"

    def _wait(self, cid: str, timeout=900):
        return self.poll(lambda: self.call("GET", cid, {"fields": "status_code,status"}),
                         done=lambda r: r.get("status_code") in ("FINISHED", "PUBLISHED"),
                         failed=lambda r: r.get("status_code") in ("ERROR", "EXPIRED"),
                         timeout=timeout, every=5, what="media processing")

    def _image_container(self, path: Path, extra: dict, cleanup: list) -> str:
        if not self.hosting:
            raise PublishError("instagram: images need a public URL; configure [hosting]")
        url, undo = self.hosting.public_url(path, "image")
        cleanup.append(undo)
        return self.call("POST", f"{self.ig}/media", data={"image_url": url, **extra})["id"]

    def _video_container(self, path: Path, extra: dict, cleanup: list) -> str:
        try:
            r = self.call("POST", f"{self.ig}/media", data={"upload_type": "resumable", **extra})
            url = r.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{self.ver}/{r['id']}"
            self.rupload(url, path)
            return r["id"]
        except PublishError as e:
            if not (self.hosting and getattr(self.hosting, "videos", False)):
                raise PublishError(f"instagram: resumable upload failed ({e}); set [hosting] provider = \"s3\" "
                                   f"to send videos by URL instead", transient=e.transient) from None
        url, undo = self.hosting.public_url(path, "video")
        cleanup.append(undo)
        return self.call("POST", f"{self.ig}/media", data={"video_url": url, **extra})["id"]

    def publish(self, target, files, post):
        fmt, cap = target["format"], target.get("caption", "")
        cleanup: list = []
        try:
            if fmt == "image":
                cid = self._image_container(files[0], {"caption": cap}, cleanup)
            elif fmt == "carousel":
                kids = [self._image_container(f, {"is_carousel_item": "true"}, cleanup) for f in files]
                for k in kids:
                    self._wait(k, 300)
                cid = self.call("POST", f"{self.ig}/media",
                                data={"media_type": "CAROUSEL", "children": ",".join(kids), "caption": cap})["id"]
            elif fmt == "reel":
                cid = self._video_container(files[0], {"media_type": "REELS", "caption": cap,
                                                       "share_to_feed": "true"}, cleanup)
            elif fmt == "story":
                if Path(files[0]).suffix == ".mp4":
                    cid = self._video_container(files[0], {"media_type": "STORIES"}, cleanup)
                else:
                    cid = self._image_container(files[0], {"media_type": "STORIES"}, cleanup)
            else:
                raise PublishError(f"instagram: unknown format {fmt}")
            self._wait(cid)
            mid = self.call("POST", f"{self.ig}/media_publish", data={"creation_id": cid})["id"]
            try:
                link = self.call("GET", mid, {"fields": "permalink"}).get("permalink")
            except PublishError:
                link = None
            return {"id": mid, "url": link}
        finally:
            for undo in cleanup:
                try:
                    undo()
                except Exception:
                    pass

    def metrics(self, target):
        mid = target["result"]["id"]
        if target["format"] == "story":
            return None
        r = self.call("GET", mid, {"fields": "like_count,comments_count"})
        out = {"likes": r.get("like_count", 0), "comments": r.get("comments_count", 0), "saves": 0, "shares": 0}
        try:
            ins = self.call("GET", f"{mid}/insights", {"metric": "reach,saved,shares,views"})
            for m in ins.get("data", []):
                v = (m.get("values") or [{}])[0].get("value", m.get("total_value", {}).get("value", 0))
                out[{"saved": "saves"}.get(m["name"], m["name"])] = v or 0
        except PublishError:
            pass
        out["engagement"] = out["likes"] + 2 * out["comments"] + 3 * out["saves"] + 3 * out["shares"]
        return out
