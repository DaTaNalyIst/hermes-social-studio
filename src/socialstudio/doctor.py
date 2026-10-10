"""`social doctor`: find setup problems before they cost a missed post."""

from __future__ import annotations

import os
import shutil
import time
from zoneinfo import ZoneInfo

from . import gitsync
from .config import Config
from .platforms import adapter
from .vault import ENV, Vault, VaultError


def run(cfg: Config, http, online: bool = False, vault: Vault | None = None) -> list[tuple[str, str, str]]:
    """Returns (level, area, message) rows; level is ok | warn | fail."""
    rows = []

    def add(level, area, msg):
        rows.append((level, area, msg))

    g = cfg.general
    try:
        ZoneInfo(cfg.tz)
        add("ok", "config", f"timezone {cfg.tz}, mode {g['mode']}, approval {g['approval']}")
    except Exception:
        add("fail", "config", f"unknown timezone {cfg.tz!r} (use an IANA name like Africa/Nairobi)")
    if g["mode"] not in ("dry-run", "live"):
        add("fail", "config", "general.mode must be dry-run or live")
    if g["approval"] not in ("required", "auto"):
        add("fail", "config", "general.approval must be required or auto")
    if g["mode"] == "dry-run":
        add("warn", "config", "dry-run: nothing is posted. Set mode = \"live\" after reviewing a dry run.")
    for tool in ("ffmpeg", "ffprobe", "git"):
        add("ok" if shutil.which(tool) else "fail", "tools", f"{tool} {'found' if shutil.which(tool) else 'missing'}")
    if gitsync.enabled(cfg.root):
        add("ok", "git", f"repo {cfg.root} on branch {gitsync.branch(cfg.root)}")
    else:
        add("warn", "git", "not a git clone with a remote: queue changes stay local")
    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        add("warn", "github", "GITHUB_TOKEN not set: `social add` cannot upload media (Kaggle: add the secret)")

    enabled = cfg.enabled()
    if not enabled:
        add("warn", "platforms", "no platform enabled yet: run `social auth <platform>`")
    vault = vault or Vault(cfg.vault_path)
    if not vault.exists():
        add("fail", "vault", "no state/vault.enc: run `social init`")
        return rows
    try:
        vault.load()
        add("ok", "vault", f"opened ({len(vault.names())} secrets)")
    except VaultError as e:
        add("fail", "vault", f"{e} (set {ENV})")
        return rows

    for p in enabled:
        a = adapter(p, cfg, vault, http)
        miss = a.missing()
        add("fail" if miss else "ok", p, f"missing {', '.join(miss)}" if miss else "credentials present")
    if "youtube" in enabled and vault.get("youtube.authorized_at"):
        age = (time.time() - float(vault.get("youtube.authorized_at"))) / 86400
        if age > 5:
            add("warn", "youtube", f"login is {age:.0f} days old; if the OAuth app is in 'Testing' it stops "
                                   "working at 7 days (re-run `social auth youtube` or publish the app)")
    if "youtube" in enabled and not cfg.platform("youtube").get("audited"):
        add("warn", "youtube", "API project not audited: uploads will be PRIVATE (docs/setup-youtube.md)")
    if "threads" in enabled and vault.get("threads.token_created"):
        age = (time.time() - float(vault.get("threads.token_created"))) / 86400
        if age > 45:
            add("warn", "threads", f"token is {age:.0f} days old (expires at 60); is the publisher running?")
    hp = cfg.data["hosting"].get("provider")
    needs_host = any(p in enabled for p in ("instagram", "threads"))
    if needs_host:
        if hp == "s3":
            h = cfg.data["hosting"]
            if not (h.get("endpoint") and h.get("bucket")):
                add("fail", "hosting", "provider s3 needs endpoint and bucket in studio.toml")
            if not (vault.get("hosting.key_id") and vault.get("hosting.secret")):
                add("fail", "hosting", "run `social auth hosting` to store the bucket keys")
        elif hp == "facebook":
            if not vault.get("facebook.page_token"):
                add("fail", "hosting", "provider facebook needs Facebook connected")
            if "threads" in enabled:
                add("warn", "hosting", "Threads videos need provider s3; images work with facebook")
        else:
            add("warn", "hosting", "no hosting: Instagram images and Threads media will be refused")

    from . import autopilot as AP, control
    plan = AP.load(cfg)
    if plan["autopilot"].get("enabled"):
        if not cfg.data["kaggle"].get("username"):
            add("fail", "autopilot", "set [kaggle] username in studio.toml")
        for p_ in AP.validate(cfg, plan):
            add("warn", "autopilot", p_)
        add("ok", "autopilot", f"daily at {plan['autopilot'].get('run_at')} {cfg.tz}, approval "
                               f"{plan['autopilot'].get('approval', 'timeout')}")
    else:
        add("warn", "autopilot", "off (autopilot.toml enabled = false, or /resume on the control bot)")
    if control.configured(vault):
        add("ok", "control", f"control bot set up ({vault.get('control.mode') or 'poll'} mode)")
    else:
        add("warn", "control", "no control bot: run `social auth control` (docs/telegram-control.md)")
    if online:
        from .ops import Runner
        r = Runner(cfg, vault, http)
        for p in enabled:
            try:
                add("ok", p, r.adapter(p).check())
            except Exception as e:
                add("fail", p, f"online check failed: {e}")
        if needs_host and r.hosting():
            try:
                add("ok", "hosting", r.hosting().check())
            except Exception as e:
                add("fail", "hosting", f"check failed: {e}")
    return rows
