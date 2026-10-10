"""YouTube Data API v3: resumable uploads for Shorts and videos, plus statistics.

Uploads from API projects Google has not audited are forced to private. Set
[platforms.youtube] audited = true once your project passes the (free) audit.
"""

from __future__ import annotations

from pathlib import Path

from ..http import ApiError
from .base import Platform, PublishError

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"


class YouTube(Platform):
    name = "youtube"
    required = ("youtube.client_id", "youtube.client_secret", "youtube.refresh_token")
    _access = None

    def access_token(self) -> str:
        if self._access:
            return self._access
        try:
            r = self.http.request("POST", TOKEN_URL, platform=self.name, data={
                "client_id": self.vault.require("youtube.client_id"),
                "client_secret": self.vault.require("youtube.client_secret"),
                "refresh_token": self.vault.require("youtube.refresh_token"),
                "grant_type": "refresh_token"})
        except ApiError as e:
            if "invalid_grant" in str(e.body):
                raise PublishError("youtube: the refresh token expired or was revoked. If your OAuth app is "
                                   "in 'Testing', tokens last 7 days: run `social auth youtube` again, or "
                                   "publish the app (see docs/setup-youtube.md).") from None
            raise PublishError(str(e), transient=e.transient) from None
        self._access = r["access_token"]
        return self._access

    def _h(self, extra=None):
        h = {"Authorization": f"Bearer {self.access_token()}"}
        h.update(extra or {})
        return h

    def check(self) -> str:
        r = self.http.request("GET", f"{API}/channels", params={"part": "snippet", "mine": "true"},
                              headers=self._h(), platform=self.name)
        items = r.get("items") or [{}]
        audited = "audited" if self.settings.get("audited") else "NOT audited: uploads will be private"
        return f"channel {items[0].get('snippet', {}).get('title')} ({audited})"

    def publish(self, target, files, post):
        path = Path(files[0])
        title = (target.get("title") or post.get("title") or "Untitled")[:100]
        desc = target.get("caption", "")
        if target["format"] == "short" and "#shorts" not in (title + " " + desc).lower():
            desc = (desc + "\n\n#Shorts").strip()
        public = bool(self.settings.get("audited"))
        meta = {
            "snippet": {"title": title, "description": desc, "tags": target.get("tags", [])[:30],
                        "categoryId": str(target.get("category_id") or self.settings.get("category_id", "22"))},
            "status": {"privacyStatus": "public" if public else "private",
                       "selfDeclaredMadeForKids": bool(self.settings.get("made_for_kids", False)),
                       "containsSyntheticMedia": True, "embeddable": True},
        }
        if post.get("language"):
            meta["snippet"]["defaultLanguage"] = meta["snippet"]["defaultAudioLanguage"] = post["language"]
        size = path.stat().st_size
        try:
            init = self.http.request("POST", UPLOAD_URL, platform=self.name, expect_json=False,
                                     params={"uploadType": "resumable", "part": "snippet,status"},
                                     json_body=meta, headers=self._h({"X-Upload-Content-Type": "video/mp4",
                                                                      "X-Upload-Content-Length": str(size)}))
            loc = init.headers.get("Location") or init.headers.get("location")
            if not loc:
                raise PublishError("youtube: no upload session URL returned")
            with open(path, "rb") as f:
                body = f.read()
            r = self.http.request("PUT", loc, body=body, platform=self.name,
                                  headers=self._h({"Content-Type": "video/mp4", "Content-Length": str(size)}))
        except ApiError as e:
            raise PublishError(str(e), transient=e.transient) from None
        vid = r["id"]
        url = f"https://youtube.com/shorts/{vid}" if target["format"] == "short" else f"https://youtu.be/{vid}"
        res = {"id": vid, "url": url, "privacy": r.get("status", {}).get("privacyStatus")}
        if not public:
            res["note"] = "uploaded as private: the API project is not audited yet"
        return res

    def metrics(self, target):
        r = self.http.request("GET", f"{API}/videos", params={"part": "statistics", "id": target["result"]["id"]},
                              headers=self._h(), platform=self.name)
        st = (r.get("items") or [{}])[0].get("statistics", {})
        views, likes, comments = (int(st.get(k, 0)) for k in ("viewCount", "likeCount", "commentCount"))
        return {"views": views, "likes": likes, "comments": comments,
                "engagement": likes + 2 * comments + views / 100.0}
