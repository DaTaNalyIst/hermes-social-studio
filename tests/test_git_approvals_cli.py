import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from socialstudio import approvals, gitsync, ops
from socialstudio.cli import set_toml
from socialstudio.config import Config
from socialstudio.queue import Queue
from socialstudio.storage import LocalStore

from conftest import make_repo
from fakehttp import FakeHttp

SUNDAY = datetime(2026, 10, 4, 5, 0, tzinfo=timezone.utc)


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def two_clones(tmp_path, monkeypatch):
    """A bare 'GitHub' remote, a Kaggle clone and an Actions clone."""
    monkeypatch.delenv("SOCIAL_NO_GIT", raising=False)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    cfg = make_repo(seed)
    (seed / "queue" / "posts" / ".gitkeep").touch()
    git(seed, "init", "-q", "-b", "main")
    git(seed, "config", "user.email", "t@t")
    git(seed, "config", "user.name", "t")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "origin", "main")
    clones = []
    for name in ("kaggle", "actions"):
        d = tmp_path / name
        subprocess.run(["git", "clone", "-q", str(remote), str(d)], check=True)
        git(d, "config", "user.email", f"{name}@t")
        git(d, "config", "user.name", name)
        clones.append(Config(d))
    return clones


def test_concurrent_writers_never_lose_changes(two_clones, media, tmp_path):
    kaggle, actions = two_clones
    store = LocalStore(tmp_path / "store")
    post = ops.add(kaggle, {"title": "A", "media": [str(media / "img0.png")], "caption": "Hello",
                            "targets": [{"platform": "telegram", "format": "photo"}]},
                   tmp_path, store=store, now=SUNDAY)
    # Actions approves from an issue comment while Kaggle adds another post at the same time
    errors = []

    def a():
        try:
            ops.approve(actions, post["id"])
        except Exception as e:  # pragma: no cover
            errors.append(e)

    def b():
        try:
            ops.add(kaggle, {"title": "B", "media": [str(media / "img1.png")], "caption": "World",
                             "targets": [{"platform": "bluesky", "format": "images"}]},
                    tmp_path, store=store, now=SUNDAY)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ta, tb = threading.Thread(target=a), threading.Thread(target=b)
    ta.start(); tb.start(); ta.join(); tb.join()
    assert not errors
    gitsync.pull(actions.root)
    posts = Queue(actions.queue_dir).all()
    assert len(posts) == 2
    assert next(p for p in posts if p["id"] == post["id"])["approval"] == "approved"
    log = git(actions.root, "log", "--oneline")
    assert "approve" in log and "queue" in log


def test_foreign_edits_are_protected(two_clones):
    kaggle, _ = two_clones
    (kaggle.root / "README.md").write_text("local edit")
    git(kaggle.root, "add", "README.md")
    with pytest.raises(gitsync.GitError, match="README.md"):
        gitsync.transact(kaggle.root, "x", lambda: None)


# --- approvals -------------------------------------------------------------------
def test_issue_lifecycle_and_trusted_comments(repo, media, tmp_path):
    post = ops.add(repo, {"title": "Coffee", "media": [str(media / "img0.png")], "caption": "Hi #coffee",
                          "targets": [{"platform": "instagram", "format": "image"},
                                      {"platform": "telegram", "format": "photo"}]},
                   tmp_path, store=LocalStore(tmp_path / "s"), now=SUNDAY)
    h = FakeHttp()
    h.on("GET", r"/labels/social-approval$", {"name": "social-approval"})
    h.on("POST", r"/issues$", {"number": 7})
    h.on("PATCH", r"/issues/7$", {})
    h.on("POST", r"/issues/7/comments$", {})
    gh = approvals.GitHub("me/studio", h, "ghs_testtoken")
    assert approvals.sync(repo, gh) == [f"opened #7 for {post['id']}"]
    body = h.find("POST", r"/issues$")[0]["json"]["body"]
    assert "`instagram:image`" in body and "/approve" in body and f"<!-- social-studio:{post['id']} -->" in body
    assert "releases/download/media-" + post["id"] in body

    event = {"issue": {"number": 7, "body": body},
             "comment": {"body": "/approve instagram", "author_association": "NONE", "user": {"type": "User"}}}
    assert "ignored" in approvals.handle_comment(repo, event, gh)
    event["comment"]["author_association"] = "OWNER"
    msg = approvals.handle_comment(repo, event, gh)
    assert "approved (instagram only)" in msg
    p = Queue(repo.queue_dir).get(post["id"])
    assert p["approval"] == "approved"
    assert {t["platform"]: t["status"] for t in p["targets"]} == {"instagram": "planned", "telegram": "cancelled"}
    assert approvals.sync(repo, gh) == [f"closed #7 for {post['id']}"]
    assert h.find("PATCH", r"/issues/7$")[-1]["json"]["state"] == "closed"


def test_move_command(repo, media, tmp_path):
    post = ops.add(repo, {"title": "C", "media": [str(media / "img0.png")], "caption": "Hi",
                          "targets": [{"platform": "telegram", "format": "photo"}]},
                   tmp_path, store=LocalStore(tmp_path / "s"), now=SUNDAY)
    gh = approvals.GitHub("me/studio", FakeHttp().on("POST", r"/comments$", {}), "ghs_x")
    ev = {"issue": {"number": 1, "body": f"x <!-- social-studio:{post['id']} -->"},
          "comment": {"body": "/move telegram:photo 2026-10-10 18:30\n/approve", "author_association": "OWNER",
                      "user": {"type": "User"}}}
    msg = approvals.handle_comment(repo, ev, gh)
    assert "moved telegram:photo" in msg and "approved" in msg
    t = Queue(repo.queue_dir).get(post["id"])["targets"][0]
    assert t["slot"] == "2026-10-10T15:30:00+00:00" and t["pinned"]


# --- CLI -----------------------------------------------------------------------
def test_set_toml_keeps_comments(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[general]\nmode = "dry-run"           # comment stays\n\n[platforms.youtube]\nenabled = false\n')
    set_toml(p, "general", "mode", '"live"')
    set_toml(p, "platforms.youtube", "enabled", "true")
    set_toml(p, "platforms.bluesky", "enabled", "true")
    text = p.read_text()
    assert 'mode = "live"           # comment stays' in text
    assert "[platforms.youtube]\nenabled = true" in text and "[platforms.bluesky]\nenabled = true" in text
    import tomllib
    assert tomllib.loads(text)["platforms"]["bluesky"]["enabled"] is True


def test_cli_add_preview_json(repo, media, tmp_path):
    spec = tmp_path / "post.json"
    spec.write_text(json.dumps({"title": "P", "media": [str(media / "portrait.mp4")], "caption": "Short",
                                "targets": [{"platform": "youtube", "format": "short", "title": "Short one"}]}))
    env = dict(os.environ, SOCIAL_REPO_DIR=str(repo.root), PYTHONPATH=str(Path(__file__).parent.parent / "src"))
    r = subprocess.run([sys.executable, "-m", "socialstudio.cli", "--json", "add", str(spec), "--preview"],
                       capture_output=True, text=True, env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["preview"] and out["targets"][0]["target"] == "youtube:short"
    assert "heuristic" in out["targets"][0]["why"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"title": "P", "media": ["nope.mp4"], "targets": "auto"}))
    r = subprocess.run([sys.executable, "-m", "socialstudio.cli", "--json", "add", str(bad)],
                       capture_output=True, text=True, env=dict(env, SOCIAL_LOCAL_STORE=str(tmp_path / "st")))
    assert r.returncode == 2 and "media not found" in json.loads(r.stdout)["problems"][0]


def test_cli_doctor_offline(repo):
    env = dict(os.environ, SOCIAL_REPO_DIR=str(repo.root), PYTHONPATH=str(Path(__file__).parent.parent / "src"))
    r = subprocess.run([sys.executable, "-m", "socialstudio.cli", "--json", "doctor"], capture_output=True,
                       text=True, env=env)
    rows = json.loads(r.stdout)
    areas = {x["area"] for x in rows if x["level"] == "ok"}
    assert {"config", "vault", "facebook", "telegram"} <= areas
    assert any(x["area"] == "youtube" and "audited" in x["message"] for x in rows)


def test_cli_init_commits_vault_and_config(tmp_path, monkeypatch):
    monkeypatch.delenv("SOCIAL_NO_GIT", raising=False)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    (work / "README.md").write_text("repo")
    git(work, "-c", "user.email=a@b", "-c", "user.name=a", "add", "README.md")
    git(work, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "first")
    git(work, "push", "-q", "origin", "main")
    env = dict(os.environ, SOCIAL_REPO_DIR=str(work), PYTHONPATH=str(Path(__file__).parent.parent / "src"),
               SOCIAL_VAULT_PASSPHRASE="correct horse battery staple")
    env.pop("SOCIAL_NO_GIT", None)
    r = subprocess.run([sys.executable, "-m", "socialstudio.cli", "--json", "init", "--timezone", "Europe/London"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert set(json.loads(r.stdout)["created"]) == {"studio.toml", "state/vault.enc"}
    files = git(remote, "ls-tree", "-r", "--name-only", "main").split()
    assert "state/vault.enc" in files and "studio.toml" in files and "queue/posts/.gitkeep" in files
    assert 'timezone = "Europe/London"' in git(remote, "show", "main:studio.toml")


def test_actions_clone_publishes_and_pushes_status(two_clones, media, tmp_path):
    from socialstudio.vault import Vault
    from conftest import PASS
    kaggle, actions = two_clones
    store = LocalStore(tmp_path / "store")
    post = ops.add(kaggle, {"title": "T", "media": [str(media / "img0.png")], "caption": "Hi",
                            "targets": [{"platform": "telegram", "format": "photo", "when": "asap"}]},
                   tmp_path, store=store, approve=True, now=SUNDAY)
    h = FakeHttp().on("POST", r"/sendPhoto$", {"ok": True, "result": {"message_id": 9}})
    gitsync.pull(actions.root)
    rep = ops.Runner(actions, Vault(actions.vault_path, PASS), h, store, sleep=lambda s: None).run(
        now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert rep[0]["status"] == "published"
    gitsync.pull(kaggle.root)
    t = Queue(kaggle.queue_dir).get(post["id"])["targets"][0]
    assert t["status"] == "published" and t["result"]["url"] == "https://t.me/mychannel/9"
    assert "publishing" in git(kaggle.root, "log", "--format=%s")
