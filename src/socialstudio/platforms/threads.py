"""Threads API: text, image, carousel and video posts. Media must be at a public URL (hosting)."""

from __future__ import annotations

import time

from ..http import ApiError
from .base import Platform, PublishError

REFRESH_AFTER_DAYS = 7  # long-lived tokens last 60 days; refresh weekly, never closer than 24 h


class Threads(Platform):
    name = "threads"
    required = ("threads.token", "threads.user_id")

    @property
    def host(self) -> str:
        return self.settings.get("host", "https://graph.threads.net/v1.0").rstrip("/")

    @property
    def root(self) -> str:
        return self.host.rsplit("/v", 1)[0]

    def call(self, method, path, params=None, data=None):
        q = dict(params or {})
        q["access_token"] = self.vault.require("threads.token")
        url = f"{self.host}/{path.lstrip('/')}"
        try:
            return self.http.request(method, url, params=q, data=data, platform=self.name)
        except ApiError as e:
            raise PublishError(str(e), transient=e.transient) from None

    def refresh(self) -> bool:
        created = float(self.vault.get("threads.token_created") or 0)
        if not created or time.time() - created < REFRESH_AFTER_DAYS * 86400:
            return False
        r = self.http.request("GET", f"{self.root}/refresh_access_token", platform=self.name,
                              params={"grant_type": "th_refresh_token",
                                      "access_token": self.vault.require("threads.token")})
        self.vault.set("threads.token", r["access_token"])
        self.vault.set("threads.token_created", str(int(time.time())))
        return True

    def check(self) -> str:
        r = self.call("GET", "me", {"fields": "id,username"})
        age = (time.time() - float(self.vault.get("threads.token_created") or time.time())) / 86400
        return f"@{r.get('username')} (token {age:.0f} days old, renewed weekly by the publisher)"

    @property
    def uid(self):
        return self.vault.require("threads.user_id")

    def _wait(self, cid, timeout=600):
        self.sleep(3)
        return self.poll(lambda: self.call("GET", cid, {"fields": "status,error_message"}),
                         done=lambda r: r.get("status") in ("FINISHED", "PUBLISHED"),
                         failed=lambda r: r.get("status") in ("ERROR", "EXPIRED"),
                         timeout=timeout, every=5, what="Threads media processing")

    def _media(self, path, kind, extra, cleanup):
        if not self.hosting or (kind == "video" and not getattr(self.hosting, "videos", False)):
            raise PublishError("threads: media needs a public URL; set [hosting] provider = \"s3\" for videos")
        url, undo = self.hosting.public_url(path, kind)
        cleanup.append(undo)
        field = "image_url" if kind == "image" else "video_url"
        return self.call("POST", f"{self.uid}/threads",
                         data={"media_type": "IMAGE" if kind == "image" else "VIDEO", field: url, **extra})["id"]

    def publish(self, target, files, post):
        fmt, text = target["format"], target.get("caption", "")
        cleanup: list = []
        try:
            if fmt == "text":
                data = {"media_type": "TEXT", "text": text}
                if target.get("link"):
                    data["link_attachment"] = target["link"]
                cid = self.call("POST", f"{self.uid}/threads", data=data)["id"]
            elif fmt == "image":
                cid = self._media(files[0], "image", {"text": text}, cleanup)
            elif fmt == "video":
                cid = self._media(files[0], "video", {"text": text}, cleanup)
            elif fmt == "carousel":
                kids = [self._media(f, "image", {"is_carousel_item": "true"}, cleanup) for f in files]
                for k in kids:
                    self._wait(k)
                cid = self.call("POST", f"{self.uid}/threads",
                                data={"media_type": "CAROUSEL", "children": ",".join(kids), "text": text})["id"]
            else:
                raise PublishError(f"threads: unknown format {fmt}")
            self._wait(cid)
            mid = self.call("POST", f"{self.uid}/threads_publish", data={"creation_id": cid})["id"]
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
        r = self.call("GET", f"{target['result']['id']}/insights", {"metric": "views,likes,replies,reposts,quotes"})
        out = {}
        for m in r.get("data", []):
            if "values" in m and m["values"]:
                out[m["name"]] = m["values"][0].get("value", 0)
            else:
                out[m["name"]] = m.get("total_value", {}).get("value", 0)
        out["engagement"] = (out.get("likes", 0) + 2 * out.get("replies", 0) + 3 * out.get("reposts", 0)
                             + 3 * out.get("quotes", 0))
        return out
