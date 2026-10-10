"""Public URLs for media that Meta must fetch itself (Instagram images/stories, Threads media).

Providers:
- "s3": any S3-compatible bucket (Backblaze B2's free 10 GB, Cloudflare R2, MinIO...).
  The file is uploaded to a private bucket, Meta gets a presigned GET link that expires in
  url_ttl_minutes, and the object is deleted right after publishing. Works for images and video.
- "facebook": images only, no extra account: the image is uploaded to your Page as an
  unpublished photo, its CDN link is used, and the photo is deleted afterwards.
SigV4 is implemented here with the standard library and tested against AWS's published example.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import urllib.parse
from pathlib import Path

from .http import Http


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str, service: str = "s3") -> bytes:
    k = _sign(("AWS4" + secret).encode(), date)
    k = _sign(k, region)
    k = _sign(k, service)
    return _sign(k, "aws4_request")


def _q(s: str) -> str:
    return urllib.parse.quote(s, safe="-_.~")


class SigV4:
    def __init__(self, key_id: str, secret: str, region: str, endpoint: str):
        self.key_id, self.secret, self.region = key_id, secret, region
        u = urllib.parse.urlparse(endpoint.rstrip("/"))
        self.scheme, self.host = u.scheme or "https", u.netloc

    def presign(self, method: str, bucket: str, key: str, expires: int, now: dt.datetime | None = None,
                host: str | None = None, path: str | None = None) -> str:
        now = now or dt.datetime.now(dt.timezone.utc)
        amz = now.strftime("%Y%m%dT%H%M%SZ")
        date = amz[:8]
        host = host or self.host
        path = path if path is not None else "/" + bucket + "/" + "/".join(_q(p) for p in key.split("/"))
        scope = f"{date}/{self.region}/s3/aws4_request"
        query = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{self.key_id}/{scope}",
                 "X-Amz-Date": amz, "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host"}
        cq = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(query.items()))
        canonical = f"{method}\n{path}\n{cq}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
        sts = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        sig = hmac.new(_signing_key(self.secret, date, self.region), sts.encode(), hashlib.sha256).hexdigest()
        return f"{self.scheme}://{host}{path}?{cq}&X-Amz-Signature={sig}"


class S3Host:
    videos = True

    def __init__(self, settings: dict, vault, http: Http | None = None):
        self.s = settings
        self.http = http or Http()
        self.sig = SigV4(vault.require("hosting.key_id"), vault.require("hosting.secret"),
                         settings.get("region", "us-east-1"), settings["endpoint"])
        self.bucket = settings["bucket"]
        if not self.bucket or not settings.get("endpoint"):
            raise ValueError("hosting: set endpoint and bucket in studio.toml")

    def public_url(self, path: Path, kind: str):
        key = f"social-tmp/{secrets.token_hex(12)}/{Path(path).name}"
        put = self.sig.presign("PUT", self.bucket, key, 900)
        ctype = "video/mp4" if kind == "video" else "image/jpeg"
        with open(path, "rb") as f:
            self.http.request("PUT", put, body=f.read(), headers={"Content-Type": ctype}, platform="hosting",
                              expect_json=False)
        ttl = int(self.s.get("url_ttl_minutes", 60)) * 60
        url = self.sig.presign("GET", self.bucket, key, ttl)

        def undo():
            self.http.request("DELETE", self.sig.presign("DELETE", self.bucket, key, 900), platform="hosting",
                              expect_json=False)
        return url, undo

    def check(self) -> str:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            f.write(b"\xff\xd8\xff\xd9")
            f.flush()
            url, undo = self.public_url(Path(f.name), "image")
            self.http.request("GET", url, platform="hosting", expect_json=False)
            undo()
        return f"bucket {self.bucket}: upload, public link and delete work"


class FacebookPhotoHost:
    videos = False

    def __init__(self, facebook_adapter):
        self.fb = facebook_adapter

    def public_url(self, path: Path, kind: str):
        if kind != "image":
            raise ValueError("facebook hosting serves images only; use s3 for video")
        photo = self.fb.upload_photo(path, published=False)
        url = self.fb.photo_url(photo["id"])
        return url, lambda: self.fb.delete(photo["id"])

    def check(self) -> str:
        return "images go through an unpublished photo on your Facebook Page"


def host_for(cfg, vault, http, facebook_adapter=None):
    p = cfg.data["hosting"].get("provider", "facebook")
    if p == "s3":
        return S3Host(cfg.data["hosting"], vault, http)
    if p == "facebook" and facebook_adapter is not None and not facebook_adapter.missing():
        return FacebookPhotoHost(facebook_adapter)
    return None
