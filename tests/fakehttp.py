"""A scripted stand-in for socialstudio.http.Http that records every request."""

import re
from pathlib import Path

from socialstudio.http import ApiError


class Resp:
    def __init__(self, data=None, headers=None, status=200):
        self.data, self.headers, self.status_code = data or {}, headers or {}, status
        self.content = b"x"

    def json(self):
        return self.data

    def iter_content(self, n):
        yield self.data if isinstance(self.data, bytes) else b"data"


class FakeHttp:
    def __init__(self):
        self.routes = []
        self.calls = []

    def on(self, method, pattern, response=None, status=200, headers=None, times=None):
        """response: dict, list (returned in turn), or callable(call) -> dict."""
        self.routes.append({"m": method, "p": re.compile(pattern), "r": response, "s": status, "h": headers,
                            "n": times})
        return self

    def request(self, method, url, *, platform="http", params=None, data=None, json_body=None, headers=None,
                body=None, files=None, expect_json=True, allow_redirects=True, stream=False):
        call = {"method": method, "url": url, "params": dict(params or {}), "data": dict(data or {}) if
                isinstance(data, dict) else data, "json": json_body, "headers": dict(headers or {}),
                "body_len": len(body) if body is not None else None,
                "files": sorted((files or {}).keys()), "platform": platform}
        self.calls.append(call)
        for r in self.routes:
            if r["m"] == method and r["p"].search(url) and (r["n"] is None or r["n"] > 0):
                if r["n"] is not None:
                    r["n"] -= 1
                if r["s"] >= 400:
                    raise ApiError(platform, r["s"], r["r"], transient=r["s"] in (429, 500, 502, 503, 504))
                res = r["r"]
                if callable(res):
                    res = res(call)
                elif isinstance(res, list):
                    res = res.pop(0) if len(res) > 1 else res[0]
                if stream or not expect_json:
                    return Resp(res, r["h"])
                return res if res is not None else {}
        raise AssertionError(f"unexpected request {method} {url}")

    def download(self, url, dest, headers=None, platform="download"):
        self.request("GET", url, headers=headers, platform=platform, stream=True)
        Path(dest).write_bytes(b"downloaded")
        return dest

    def find(self, method, pattern):
        return [c for c in self.calls if c["method"] == method and re.search(pattern, c["url"])]
