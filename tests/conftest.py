import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from socialstudio.config import TEMPLATE, Config
from socialstudio.vault import Vault

PASS = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SOCIAL_VAULT_PASSPHRASE", PASS)
    monkeypatch.delenv("SOCIAL_REPO_DIR", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    yield


def make_repo(root: Path, enable=("facebook", "instagram", "threads", "youtube", "bluesky", "telegram"),
              hosting="s3", approval="required", mode="live", tz="Africa/Nairobi") -> Config:
    root.mkdir(parents=True, exist_ok=True)
    text = TEMPLATE.format(tz=tz)
    text = text.replace('mode = "dry-run"', f'mode = "{mode}"').replace('approval = "required"',
                                                                       f'approval = "{approval}"')
    text = text.replace('provider = "facebook"', f'provider = "{hosting}"')
    text = text.replace('endpoint = ""', 'endpoint = "https://s3.us-west-004.backblazeb2.com"')
    text = text.replace('bucket = ""', 'bucket = "social-media"')
    for p in enable:
        text = text.replace(f"[platforms.{p}]\nenabled = false", f"[platforms.{p}]\nenabled = true")
    (root / "studio.toml").write_text(text)
    (root / "queue" / "posts").mkdir(parents=True, exist_ok=True)
    cfg = Config(root)
    v = Vault(cfg.vault_path)
    v.create(PASS)
    for k, val in SECRETS.items():
        v.set(k, val)
    v.save()
    return cfg


SECRETS = {
    "facebook.app_secret": "appsecret123456", "facebook.page_id": "111", "facebook.page_token": "EAApagetoken1234567890abcdefghij",
    "instagram.user_id": "17841400000000000", "threads.token": "THtoken" + "a" * 40, "threads.user_id": "999",
    "threads.token_created": "0", "youtube.client_id": "cid.apps.googleusercontent.com",
    "youtube.client_secret": "GOCSPX-secretsecret", "youtube.refresh_token": "1//refresh-token-abcdefghijklmnop",
    "bluesky.handle": "me.bsky.social", "bluesky.app_password": "abcd-efgh-ijkl-mnop",
    "telegram.bot_token": "123456789:AAEexampleexampleexampleexample123", "telegram.chat_id": "-1001234567890",
    "telegram.chat_username": "mychannel", "hosting.key_id": "K004example", "hosting.secret": "s3secretsecretsecret",
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_NO_GIT", "1")
    cfg = make_repo(tmp_path / "repo")
    monkeypatch.chdir(cfg.root)
    return cfg


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=832x480:rate=16:duration=5", "-c:v", "libx264", "-pix_fmt",
           "yuv420p", d / "landscape.mp4")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=480x832:rate=16:duration=4", "-f", "lavfi", "-i",
           "sine=frequency=440:duration=4", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           d / "portrait.mp4")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=480x832:rate=30:duration=120", "-c:v", "libx264", "-preset",
           "ultrafast", "-pix_fmt", "yuv420p", d / "long.mp4")
    from PIL import Image
    for i, size in enumerate([(1344, 768), (768, 1344), (1024, 1024), (3000, 600)]):
        Image.new("RGB", size, (40 * i, 120, 200)).save(d / f"img{i}.png")
    return d


def has_ffmpeg():
    return shutil.which("ffmpeg") is not None
