#!/usr/bin/env python3
"""Offline self-test: proves the install, ffmpeg and the whole queue flow work, without any account.

It builds a throwaway studio in a temp folder, makes a test video and image with ffmpeg, then runs
the real `social` CLI: init -> enable -> add (with platform variants) -> approve -> run-due (dry run).
Nothing touches the network or your repo.  usage: python scripts/selftest.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is missing (Kaggle and GitHub runners have it; locally: install ffmpeg)")
    tmp = Path(tempfile.mkdtemp(prefix="social-selftest-"))
    env = dict(os.environ, SOCIAL_REPO_DIR=str(tmp / "repo"), SOCIAL_NO_GIT="1",
               SOCIAL_LOCAL_STORE=str(tmp / "store"), SOCIAL_VAULT_PASSPHRASE="selftest passphrase only",
               PYTHONPATH=str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    (tmp / "repo").mkdir()

    def social(*args, ok=True):
        p = subprocess.run([sys.executable, "-m", "socialstudio.cli", "--json", *args], capture_output=True,
                           text=True, env=env)
        if ok and p.returncode != 0:
            sys.exit(f"FAILED: social {' '.join(args)}\n{p.stdout}\n{p.stderr}")
        return json.loads(p.stdout) if p.stdout.strip() else {}

    def ff(*a):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, a)], check=True)

    print("1. init + enable platforms")
    social("init", "--timezone", "Africa/Nairobi")
    for p in ("facebook", "instagram", "youtube", "bluesky", "telegram"):
        social("enable", p)
    print("2. make test media")
    ff("-f", "lavfi", "-i", "testsrc2=size=832x480:rate=16:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       tmp / "clip.mp4")
    spec = {"title": "Self-test clip", "media": [str(tmp / "clip.mp4")], "caption": "Self-test #studio",
            "language": "en",
            "targets": [{"platform": "instagram", "format": "reel"}, {"platform": "facebook", "format": "reel"},
                        {"platform": "youtube", "format": "short", "title": "Self-test"},
                        {"platform": "bluesky", "format": "video"}, {"platform": "telegram", "format": "video"}]}
    (tmp / "post.json").write_text(json.dumps(spec))
    print("3. add: variants, upload (local store), best-time plan")
    post = social("add", str(tmp / "post.json"))
    for t in post["targets"]:
        print(f"   {t['target']:<18} {t['when_local']:<18} {t['why']}")
    variants = list((tmp / "store" / post["id"]).iterdir())
    print(f"   {len(variants)} platform file(s): " + ", ".join(sorted(v.name.split('__')[1] for v in variants)))
    print("4. approve + dry run of the publisher")
    social("approve", post["id"])
    social("now", post["id"])
    rep = social("run-due", "--dry-run")
    assert len(rep) == 5 and all("would publish" in r["action"] for r in rep), rep
    print(f"   {len(rep)} target(s) would publish now")
    shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST PASSED")


if __name__ == "__main__":
    main()
