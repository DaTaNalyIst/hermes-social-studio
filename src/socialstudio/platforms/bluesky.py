"""Bluesky (AT Protocol): text, up to 4 images, or a video, with link and hashtag facets."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..http import ApiError
from .base import Platform, PublishError

VIDEO_SERVICE = "https://video.bsky.app"
URL_RE = re.compile(rb"https?://[^\s<>\"]+[^\s<>\".,;:!?)\]']")
TAG_RE = re.compile(rb"(?:^|\s)(#[^\s#.,;:!?)\]'\"]+)")


def facets(text: str) -> list[dict]:
    """Rich-text facets with UTF-8 byte offsets (what Bluesky requires)."""
    b = text.encode("utf-8")
    out = []
    for m in URL_RE.finditer(b):
        out.append({"index": {"byteStart": m.start(), "byteEnd": m.end()},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0).decode()}]})
    for m in TAG_RE.finditer(b):
        tag = m.group(1)
        start = m.start(1)
        out.append({"index": {"byteStart": start, "byteEnd": start + len(tag)},
                    "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag[1:].decode()[:64]}]})
    return out


class Bluesky(Platform):
    name = "bluesky"
    required = ("bluesky.handle", "bluesky.app_password")
    _session = None

    def session(self) -> dict:
        if self._session:
            return self._session
        pds = (self.vault.get("bluesky.pds") or self.settings.get("pds") or "https://bsky.social").rstrip("/")
        try:
            s = self.http.request("POST", f"{pds}/xrpc/com.atproto.server.createSession", platform=self.name,
                                  json_body={"identifier": self.vault.require("bluesky.handle"),
                                             "password": self.vault.require("bluesky.app_password")})
        except ApiError as e:
            raise PublishError(str(e), transient=e.transient) from None
        endpoint = pds
        for svc in (s.get("didDoc") or {}).get("service", []):
            if svc.get("id", "").endswith("#atproto_pds"):
                endpoint = svc["serviceEndpoint"].rstrip("/")
        s["_pds"] = endpoint
        self._session = s
        return s

    def xrpc(self, method, nsid, params=None, json_body=None, body=None, headers=None):
        s = self.session()
        h = {"Authorization": f"Bearer {s['accessJwt']}"}
        h.update(headers or {})
        try:
            return self.http.request(method, f"{s['_pds']}/xrpc/{nsid}", params=params, json_body=json_body,
                                     body=body, headers=h, platform=self.name)
        except ApiError as e:
            raise PublishError(str(e), transient=e.transient) from None

    def check(self) -> str:
        s = self.session()
        return f"@{s.get('handle')} on {urlparse(s['_pds']).netloc}"

    def _blob(self, path: Path, ctype: str) -> dict:
        with open(path, "rb") as f:
            return self.xrpc("POST", "com.atproto.repo.uploadBlob", body=f.read(),
                             headers={"Content-Type": ctype})["blob"]

    def _video_blob(self, path: Path) -> dict:
        s = self.session()
        try:
            aud = f"did:web:{urlparse(s['_pds']).netloc}"
            tok = self.xrpc("GET", "com.atproto.server.getServiceAuth",
                            params={"aud": aud, "lxm": "com.atproto.repo.uploadBlob",
                                    "exp": int(time.time()) + 1800})["token"]
            with open(path, "rb") as f:
                job = self.http.request("POST", f"{VIDEO_SERVICE}/xrpc/app.bsky.video.uploadVideo",
                                        params={"did": s["did"], "name": Path(path).name}, body=f.read(),
                                        platform=self.name, headers={"Authorization": f"Bearer {tok}",
                                                                     "Content-Type": "video/mp4"})
            blob = job.get("blob")
            job_id = job.get("jobId")
            while not blob:
                st = self.http.request("GET", f"{VIDEO_SERVICE}/xrpc/app.bsky.video.getJobStatus",
                                       params={"jobId": job_id}, platform=self.name).get("jobStatus", {})
                blob = st.get("blob")
                if st.get("state") == "JOB_STATE_FAILED" and not blob:
                    raise PublishError(f"bluesky: video processing failed: {st.get('error') or st.get('message')}")
                if not blob:
                    self.sleep(2)
            return blob
        except (ApiError, PublishError, KeyError):
            # The video service is optional; uploading straight to the PDS also works,
            # the video just finishes processing a few seconds after the post appears.
            return self._blob(path, "video/mp4")

    def publish(self, target, files, post):
        text = target.get("caption", "")
        record = {"$type": "app.bsky.feed.post", "text": text,
                  "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        if post.get("language"):
            record["langs"] = [post["language"]]
        f = facets(text)
        if f:
            record["facets"] = f
        fmt = target["format"]
        assets = target.get("assets", [])
        if fmt == "images":
            imgs = []
            for path, a in zip(files, assets):
                imgs.append({"alt": target.get("alt") or post.get("title", ""), "image": self._blob(path, "image/jpeg"),
                             "aspectRatio": {"width": a.get("width", 1), "height": a.get("height", 1)}})
            record["embed"] = {"$type": "app.bsky.embed.images", "images": imgs}
        elif fmt == "video":
            a = assets[0] if assets else {}
            record["embed"] = {"$type": "app.bsky.embed.video", "video": self._video_blob(files[0]),
                               "aspectRatio": {"width": a.get("width", 16), "height": a.get("height", 9)}}
            if target.get("alt"):
                record["embed"]["alt"] = target["alt"]
        elif target.get("link"):
            record["embed"] = {"$type": "app.bsky.embed.external",
                               "external": {"uri": target["link"], "title": post.get("title", "")[:300],
                                            "description": ""}}
        s = self.session()
        r = self.xrpc("POST", "com.atproto.repo.createRecord",
                      json_body={"repo": s["did"], "collection": "app.bsky.feed.post", "record": record})
        rkey = r["uri"].rsplit("/", 1)[-1]
        return {"id": r["uri"], "cid": r.get("cid"), "url": f"https://bsky.app/profile/{s.get('handle', s['did'])}/post/{rkey}"}

    def metrics(self, target):
        r = self.xrpc("GET", "app.bsky.feed.getPosts", params={"uris": target["result"]["id"]})
        p = (r.get("posts") or [{}])[0]
        out = {k: p.get(k + "Count", 0) for k in ("like", "repost", "reply", "quote")}
        out["engagement"] = out["like"] + 2 * out["reply"] + 3 * out["repost"] + 3 * out["quote"]
        return out
