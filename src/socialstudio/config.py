"""studio.toml: committed, secret-free settings. Defaults below apply to anything not set."""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

PLATFORMS = ("facebook", "instagram", "threads", "youtube", "bluesky", "telegram")

# Formats each platform can publish. "lane" groups formats that share caps and gaps.
FORMATS = {
    "facebook": {"post": "feed", "photo": "feed", "album": "feed", "video": "feed", "reel": "feed",
                 "story": "story"},
    "instagram": {"image": "feed", "carousel": "feed", "reel": "feed", "story": "story"},
    "threads": {"text": "feed", "image": "feed", "carousel": "feed", "video": "feed"},
    "youtube": {"short": "feed", "video": "feed"},
    "bluesky": {"text": "feed", "images": "feed", "video": "feed"},
    "telegram": {"text": "feed", "photo": "feed", "album": "feed", "video": "feed"},
}

DEFAULTS = {
    "general": {
        "timezone": "Africa/Nairobi",
        "mode": "dry-run",          # dry-run | live
        "approval": "required",     # required | auto
        "window_days": 7,
        "lead_minutes": 45,         # earliest slot after "now": the publisher runs every 30 min
        "slot_minutes": 30,
        "quiet_hours": [0, 6],      # local hours [start, end) with no posts
        "max_attempts": 3,
    },
    "github": {"media_retention_days": 14},
    "kaggle": {"username": "", "autopilot_kernel": "hermes-studio-autopilot", "chat_kernel": "hermes-studio-chat",
               "keys_dataset": "", "brain": "qwen3.5:9b", "autopilot_max_hours": 6, "chat_idle_minutes": 30,
               "chat_max_hours": 6},
    "hosting": {
        # Instagram images/stories and Threads media must be fetched by Meta from a public URL.
        # "s3": any S3-compatible bucket (Backblaze B2 free 10 GB, Cloudflare R2, ...) via presigned URLs.
        # "facebook": images only, via an unpublished photo on your Page (no bucket needed).
        "provider": "facebook",
        "endpoint": "",
        "region": "us-east-1",
        "bucket": "",
        "url_ttl_minutes": 60,
    },
    "platforms": {
        "facebook": {"enabled": False, "daily_cap": 2, "min_gap_minutes": 180, "story_cap": 5,
                     "graph_version": "v25.0"},
        "instagram": {"enabled": False, "daily_cap": 2, "min_gap_minutes": 180, "story_cap": 5,
                      "max_hashtags": 5},
        "threads": {"enabled": False, "daily_cap": 4, "min_gap_minutes": 120,
                    "host": "https://graph.threads.net/v1.0"},
        "youtube": {"enabled": False, "daily_cap": 1, "min_gap_minutes": 240, "audited": False,
                    "category_id": "22", "made_for_kids": False},
        "bluesky": {"enabled": False, "daily_cap": 4, "min_gap_minutes": 90, "pds": "https://bsky.social"},
        "telegram": {"enabled": False, "daily_cap": 4, "min_gap_minutes": 90},
    },
    "learning": {"rate": 0.3, "min_weight": 0.5, "max_weight": 2.0, "evaluate_after_hours": 72,
                 "youtube_evaluate_after_hours": 168},
}

TEMPLATE = """# Hermes Social Studio settings. No secrets here: those live in state/vault.enc.
[general]
timezone = "{tz}"          # your AUDIENCE's timezone (IANA name)
mode = "dry-run"           # dry-run: plan and log only. Switch to "live" after a good dry run.
approval = "required"      # required: nothing posts until you approve; auto: post as planned
window_days = 7            # how far ahead the scheduler looks for a good slot
quiet_hours = [0, 6]       # no posts between these local hours

[kaggle]
username = ""              # your Kaggle username (for daily runs and /studio from Telegram)
chat_idle_minutes = 30     # a session started from Telegram ends after this many quiet minutes

[hosting]
provider = "facebook"      # facebook (images only, free, no setup) | s3 (Backblaze B2 / R2 bucket)
endpoint = ""              # s3 only, e.g. "https://s3.us-west-004.backblazeb2.com"
region = "us-west-004"
bucket = ""

[platforms.facebook]
enabled = false
daily_cap = 2

[platforms.instagram]
enabled = false
daily_cap = 2

[platforms.threads]
enabled = false
daily_cap = 4

[platforms.youtube]
enabled = false
daily_cap = 1
audited = false            # true once Google has audited your API project (uploads can be public)

[platforms.bluesky]
enabled = false
daily_cap = 4

[platforms.telegram]
enabled = false
daily_cap = 4
"""


def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


POINTER = Path.home() / ".social-studio.json"  # written by the Kaggle notebook: {"repo": "/kaggle/working/..."}


def repo_root(start: Path | None = None) -> Path:
    env = os.environ.get("SOCIAL_REPO_DIR")
    if env:
        return Path(env).resolve()
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "studio.toml").exists() or (cand / ".git").exists() and (cand / "queue").exists():
            return cand
    if start is None and POINTER.exists():
        try:
            import json
            return Path(json.loads(POINTER.read_text())["repo"]).resolve()
        except (OSError, ValueError, KeyError):
            pass
    return p


class Config:
    def __init__(self, root: Path | None = None):
        self.root = repo_root(root)
        self.path = self.root / "studio.toml"
        data = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            with open(self.path, "rb") as f:
                _merge(data, tomllib.load(f))
        self.data = data

    # convenience accessors
    @property
    def general(self) -> dict:
        return self.data["general"]

    @property
    def tz(self) -> str:
        return self.general["timezone"]

    @property
    def live(self) -> bool:
        return self.general["mode"] == "live"

    def platform(self, name: str) -> dict:
        return self.data["platforms"][name]

    def enabled(self) -> list[str]:
        return [p for p in PLATFORMS if self.platform(p).get("enabled")]

    @property
    def queue_dir(self) -> Path:
        return self.root / "queue" / "posts"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def vault_path(self) -> Path:
        return self.state_dir / "vault.enc"
