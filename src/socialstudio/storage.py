"""Media store: one GitHub release per post in your own repo (free, 2 GiB per file).

Kaggle uploads the platform-ready files with a fine-grained token (Contents: read & write);
the publisher on GitHub Actions downloads them with its built-in GITHUB_TOKEN.
Releases are prereleases tagged media-<post id> and are deleted after the retention period.
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.parse
from pathlib import Path

from .http import ApiError, Http

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"


def detect_repo(root: Path) -> str:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    if os.environ.get("SOCIAL_REPO"):
        return os.environ["SOCIAL_REPO"]
    url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True,
                         text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        raise RuntimeError("cannot tell which GitHub repo this is; set SOCIAL_REPO=owner/name")
    return m.group(1)


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t
    # The Kaggle notebook stores the token for git in ~/.git-credentials (mode 600); reuse it so
    # processes started by Hermes, which may not inherit the notebook's environment, still work.
    cred = Path.home() / ".git-credentials"
    if cred.exists():
        for line in cred.read_text().splitlines():
            m = re.match(r"https://[^:]+:([^@]+)@github\.com", line.strip())
            if m:
                return urllib.parse.unquote(m.group(1))
    raise RuntimeError("no GitHub token: set GITHUB_TOKEN (Kaggle: add it as a secret; Actions: automatic)")


class ReleaseStore:
    def __init__(self, repo: str, http: Http | None = None, tok: str | None = None):
        self.repo, self.http, self._tok = repo, http or Http(), tok

    def _h(self, extra=None):
        h = {"Authorization": f"Bearer {self._tok or token()}", "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28"}
        h.update(extra or {})
        return h

    def tag(self, post_id: str) -> str:
        return f"media-{post_id}"

    def ensure_release(self, post_id: str, title: str = "") -> dict:
        tag = self.tag(post_id)
        try:
            return self.http.request("GET", f"{API}/repos/{self.repo}/releases/tags/{urllib.parse.quote(tag)}",
                                     headers=self._h(), platform="github")
        except ApiError as e:
            if e.status != 404:
                raise
        return self.http.request("POST", f"{API}/repos/{self.repo}/releases", headers=self._h(), platform="github",
                                 json_body={"tag_name": tag, "name": f"media {post_id} {title}"[:120],
                                            "body": "Media for a queued social post. Deleted automatically "
                                                    "after the retention period.", "prerelease": True})

    def upload(self, release: dict, path: Path, name: str) -> dict:
        for a in release.get("assets", []):
            if a["name"] == name:
                return a
        ctype = "video/mp4" if name.endswith(".mp4") else "image/jpeg" if name.endswith(".jpg") \
            else "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        return self.http.request("POST", f"{UPLOADS}/repos/{self.repo}/releases/{release['id']}/assets",
                                 params={"name": name}, body=data, platform="github",
                                 headers=self._h({"Content-Type": ctype}))

    def download(self, asset_id: int, dest: Path) -> Path:
        # GitHub answers with a redirect to signed storage; requests drops the auth header on
        # the cross-host hop, which is what that storage expects.
        return self.http.download(f"{API}/repos/{self.repo}/releases/assets/{asset_id}", dest,
                                  headers=self._h({"Accept": "application/octet-stream"}), platform="github")

    def delete_release(self, post_id: str):
        tag = self.tag(post_id)
        try:
            rel = self.http.request("GET", f"{API}/repos/{self.repo}/releases/tags/{urllib.parse.quote(tag)}",
                                    headers=self._h(), platform="github")
        except ApiError as e:
            if e.status == 404:
                return False
            raise
        self.http.request("DELETE", f"{API}/repos/{self.repo}/releases/{rel['id']}", headers=self._h(),
                          platform="github", expect_json=False)
        try:
            self.http.request("DELETE", f"{API}/repos/{self.repo}/git/refs/tags/{tag}", headers=self._h(),
                              platform="github", expect_json=False)
        except ApiError:
            pass
        return True


class LocalStore:
    """Offline store for tests and dry runs without GitHub: copies files into a folder."""

    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._n = 0

    def ensure_release(self, post_id, title=""):
        d = self.dir / post_id
        d.mkdir(exist_ok=True)
        return {"id": post_id, "assets": [{"name": p.name, "id": f"{post_id}/{p.name}"} for p in d.iterdir()]}

    def upload(self, release, path, name):
        import shutil
        shutil.copy2(path, self.dir / release["id"] / name)
        return {"name": name, "id": f"{release['id']}/{name}"}

    def download(self, asset_id, dest):
        import shutil
        shutil.copy2(self.dir / asset_id, dest)
        return dest

    def delete_release(self, post_id):
        import shutil
        shutil.rmtree(self.dir / post_id, ignore_errors=True)
        return True


def store_for(root: Path, http: Http | None = None):
    if os.environ.get("SOCIAL_LOCAL_STORE"):
        return LocalStore(Path(os.environ["SOCIAL_LOCAL_STORE"]))
    return ReleaseStore(detect_repo(root), http)
