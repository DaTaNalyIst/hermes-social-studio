"""One HTTP layer for every platform: timeouts, retries on transient errors, redacted errors.

Tests swap in a fake with the same `request` signature (see tests/fakehttp.py).
"""

from __future__ import annotations

import json
import time

import requests

from .redact import redact

TRANSIENT = {408, 429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(self, platform: str, status: int, body, transient: bool = False):
        self.platform, self.status, self.body, self.transient = platform, status, body, transient
        super().__init__(redact(f"{platform} API error {status}: {str(body)[:600]}"))


class Http:
    def __init__(self, timeout: float = 120.0, retries: int = 3):
        self.s = requests.Session()
        self.timeout, self.retries = timeout, retries

    def request(self, method: str, url: str, *, platform: str = "http", params=None, data=None,
                json_body=None, headers=None, body: bytes | None = None, files=None,
                expect_json: bool = True, allow_redirects: bool = True, stream: bool = False):
        last = None
        for attempt in range(self.retries):
            try:
                r = self.s.request(method, url, params=params, data=body if body is not None else data,
                                   json=json_body, headers=headers, files=files, timeout=self.timeout,
                                   allow_redirects=allow_redirects, stream=stream)
            except (requests.ConnectionError, requests.Timeout) as e:
                last = ApiError(platform, 0, str(e), transient=True)
                time.sleep(2 ** attempt)
                continue
            if r.status_code in TRANSIENT and attempt < self.retries - 1:
                last = ApiError(platform, r.status_code, r.text, transient=True)
                time.sleep(min(30, 2 ** (attempt + 1)))
                continue
            if r.status_code >= 400:
                try:
                    body_ = r.json()
                except ValueError:
                    body_ = r.text
                raise ApiError(platform, r.status_code, body_, transient=r.status_code in TRANSIENT)
            if stream:
                return r
            if not expect_json:
                return r
            if not r.content:
                return {}
            try:
                return r.json()
            except ValueError:
                return {"_text": r.text, "_headers": dict(r.headers)}
        raise last  # type: ignore[misc]

    def download(self, url: str, dest, headers=None, platform="download"):
        r = self.request("GET", url, headers=headers, platform=platform, stream=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        return dest


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
