"""The queue: one JSON file per post in queue/posts/, committed to git.

Post:
{
  "id": "p_1006_ab12cd", "title": "...", "created": "...iso...", "approval": "pending|approved|rejected",
  "release": {"tag": "media/p_...", "id": 123},           # GitHub release holding the media
  "targets": [ Target, ... ], "notes": "..."
}
Target:
{
  "tid": "instagram:reel", "platform": "instagram", "format": "reel", "lane": "feed",
  "caption": "...", "title": "...(youtube)", "link": null,
  "assets": [{"name": "p_...__v1.mp4", "kind": "video", "sha256": "...", "size": 123, "width": 1080,
              "height": 1920, "duration": 31.2, "asset_id": 456}],
  "slot": "2026-10-08T16:00:00+00:00", "pinned": false,
  "status": "planned|publishing|published|failed|cancelled",
  "attempts": 0, "error": null, "result": {"id": "...", "url": "..."}, "published_at": null,
  "metrics": {}, "evaluated": false
}
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

ACTIVE = ("planned", "publishing", "failed_retry")


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def new_id() -> str:
    return "p_" + time.strftime("%m%d") + "_" + secrets.token_hex(3)


class Queue:
    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, pid: str) -> Path:
        if not pid.startswith("p_") or "/" in pid or ".." in pid:
            raise ValueError(f"bad post id {pid!r}")
        return self.dir / f"{pid}.json"

    def get(self, pid: str) -> dict:
        p = self.path(pid)
        if not p.exists():
            raise KeyError(f"no post {pid}")
        return json.loads(p.read_text())

    def put(self, post: dict):
        p = self.path(post["id"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(post, indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, p)

    def delete(self, pid: str):
        self.path(pid).unlink(missing_ok=True)

    def all(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("p_*.json")):
            try:
                out.append(json.loads(p.read_text()))
            except json.JSONDecodeError:
                continue
        return out

    def scheduled(self, exclude: str | None = None) -> list[tuple[dict, dict]]:
        """(post, target) pairs that occupy a slot (planned or already published)."""
        res = []
        for post in self.all():
            if post["id"] == exclude or post.get("approval") == "rejected":
                continue
            for t in post["targets"]:
                if t.get("slot") and t["status"] in ("planned", "publishing", "published", "failed_retry"):
                    res.append((post, t))
        return res

    def due(self, at: datetime, approval_mode: str) -> list[tuple[dict, dict]]:
        res = []
        for post in self.all():
            if post.get("approval") == "rejected":
                continue
            if approval_mode == "required" and post.get("approval") != "approved":
                continue
            for t in post["targets"]:
                if t["status"] in ("planned", "failed_retry") and t.get("slot") and parse(t["slot"]) <= at:
                    res.append((post, t))
        res.sort(key=lambda pt: pt[1]["slot"])
        return res


def find_target(post: dict, tid: str) -> dict:
    for t in post["targets"]:
        if t["tid"] == tid or t["platform"] == tid:
            return t
    raise KeyError(f"post {post['id']} has no target {tid}")
