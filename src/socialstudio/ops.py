"""The operations behind the CLI: add, approve/reject/move/cancel/retry, run-due and cleanup.

Every write goes through gitsync.transact so the Kaggle notebook, GitHub Actions and the
approval workflow can all change the queue without stepping on each other.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from . import captions, gitsync
from . import media as M
from .config import FORMATS, PLATFORMS, Config
from .http import Http
from .platforms import PublishError, adapter
from .queue import Queue, find_target, iso, new_id, now_utc, parse
from .scheduler import Scheduler, parse_local

log = logging.getLogger("social")
STALE_PUBLISHING = timedelta(minutes=40)
TERMINAL = ("published", "failed", "cancelled", "unknown")


class AddError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------
def auto_targets(cfg: Config, media: list[Path], hosting_videos: bool) -> list[dict]:
    """Sensible formats on every enabled platform for the given media."""
    out = []
    infos = [M.probe(m) for m in media]
    kinds = {i.kind for i in infos}
    for p in cfg.enabled():
        fmt = None
        if not media:
            fmt = {"facebook": "post", "threads": "text", "bluesky": "text", "telegram": "text"}.get(p)
        elif kinds == {"video"} and len(media) == 1:
            i = infos[0]
            vertical = i.height >= i.width
            fmt = {
                "facebook": "reel" if 3 <= i.duration <= 90 else "video",
                "instagram": "reel" if i.duration <= 900 else None,
                "threads": "video" if hosting_videos and i.duration <= 300 else None,
                "youtube": "short" if (vertical and i.duration <= 180) else "video",
                "bluesky": "video" if i.duration <= 180 else None,
                "telegram": "video",
            }[p]
        elif kinds == {"image"}:
            n = len(media)
            fmt = {
                "facebook": "photo" if n == 1 else "album",
                "instagram": "image" if n == 1 else ("carousel" if n <= 10 else None),
                "threads": "image" if n == 1 else ("carousel" if n <= 20 else None),
                "youtube": None,
                "bluesky": "images" if n <= 4 else None,
                "telegram": "photo" if n == 1 else ("album" if n <= 10 else None),
            }[p]
        if fmt:
            out.append({"platform": p, "format": fmt})
    return out


def _resolve(spec: dict, base: Path) -> list[Path]:
    paths = []
    for m in spec.get("media", []):
        p = Path(m["path"] if isinstance(m, dict) else m).expanduser()
        if not p.is_absolute():
            p = (base / p).resolve()
        paths.append(p)
    return paths


def validate(cfg: Config, spec: dict, base: Path) -> tuple[list[Path], list[dict]]:
    problems, targets = [], []
    media = _resolve(spec, base)
    for m in media:
        if not m.exists():
            problems.append(f"media not found: {m}")
    if problems:
        raise AddError(problems)
    hosting = cfg.data["hosting"].get("provider", "facebook")
    raw = spec.get("targets")
    if raw in (None, "all", "auto"):
        raw = auto_targets(cfg, media, hosting == "s3")
        if not raw:
            raise AddError(["no enabled platform can take this media; enable platforms in studio.toml"])
    for i, t in enumerate(raw):
        t = dict(t)
        p, fmt = t.get("platform"), t.get("format")
        where = f"target {i + 1} ({p}:{fmt})"
        if p not in PLATFORMS:
            problems.append(f"{where}: unknown platform; use one of {', '.join(PLATFORMS)}")
            continue
        if fmt not in FORMATS[p]:
            problems.append(f"{where}: unknown format; {p} supports {', '.join(FORMATS[p])}")
            continue
        if not cfg.platform(p).get("enabled"):
            problems.append(f"{where}: {p} is not enabled in studio.toml (connect it with `social auth {p}`)")
            continue
        idx = t.get("media")
        tm = [media[j] for j in idx] if idx is not None else (media if M.RULES[(p, fmt)]["kind"] != "text" else [])
        try:
            M.check_target(p, fmt, tm)
        except M.MediaError as e:
            problems.append(f"{where}: {e}")
            continue
        is_video = bool(tm) and M.kind_of(tm[0]) == "video"
        if p == "instagram" and not is_video and fmt in ("image", "carousel", "story") and hosting == "none":
            problems.append(f"{where}: Instagram images need [hosting] provider = \"facebook\" or \"s3\"")
        if p == "threads" and tm:
            if hosting == "none" or (is_video and hosting != "s3"):
                problems.append(f"{where}: Threads fetches media from a URL: set [hosting] provider = "
                                f"\"{'s3' if is_video else 'facebook or s3'}\"")
        if "caption" not in t:
            t["caption"] = captions.compose(spec.get("caption", ""), spec.get("hashtags", []), p,
                                            cfg.platform(p).get("max_hashtags"))
        if p == "youtube":
            t.setdefault("title", spec.get("title", ""))
            t.setdefault("tags", spec.get("hashtags", []))
        if spec.get("link") and "link" not in t:
            t["link"] = spec["link"]
        for prob in captions.problems(p, t["caption"], has_media=bool(tm),
                                      max_tags=cfg.platform(p).get("max_hashtags"), title=t.get("title")):
            problems.append(f"{where}: {prob}")
        t["_media"] = tm
        targets.append(t)
    if problems:
        raise AddError(problems)
    return media, targets


def add(cfg: Config, spec: dict, base: Path, *, store=None, approve: bool = False, preview: bool = False,
        now: datetime | None = None, work: Path | None = None, auto_approve_at: datetime | None = None,
        source: str | None = None) -> dict:
    gitsync.pull(cfg.root)
    media, targets = validate(cfg, spec, base)
    pid = spec.get("id") if preview and spec.get("id") else new_id()
    now = now or now_utc()
    not_before = parse_local(spec["not_before"], cfg.tz) if spec.get("not_before") else None
    not_after = parse_local(spec["not_after"], cfg.tz) if spec.get("not_after") else None
    if auto_approve_at and (not_before is None or not_before < auto_approve_at):
        not_before = auto_approve_at  # never plan a slot before the post can be approved
    own_work = work is None
    work = Path(work or tempfile.mkdtemp(prefix=f"social-{pid}-"))
    maker = None if preview else M.VariantMaker(pid, work)
    post_targets = []
    for t in targets:
        rule = M.RULES[(t["platform"], t["format"])]
        assets = []
        for src in t.pop("_media"):
            recipe = M.recipe_for(rule, M.kind_of(src))
            assets.append(dict(maker.variant(src, recipe)) if maker else {"name": src.name, "recipe": recipe})
        tgt = {"tid": f"{t['platform']}:{t['format']}", "platform": t["platform"], "format": t["format"],
               "lane": FORMATS[t["platform"]][t["format"]], "caption": t.get("caption", ""),
               "assets": assets, "slot": None, "pinned": False, "status": "planned", "attempts": 0,
               "error": None, "result": None, "published_at": None, "metrics": {}, "evaluated": False}
        for k in ("title", "tags", "link", "alt", "category_id"):
            if t.get(k):
                tgt[k] = t[k]
        if t.get("at"):
            tgt["slot"], tgt["pinned"] = iso(parse_local(t["at"], cfg.tz)), True
        if t.get("when") == "asap":
            tgt["when"] = "asap"
        # one target per platform+format per post
        if any(x["tid"] == tgt["tid"] for x in post_targets):
            tgt["tid"] += f"#{sum(1 for x in post_targets if x['tid'].startswith(tgt['tid'])) + 1}"
        post_targets.append(tgt)

    post = {"id": pid, "title": spec.get("title", "")[:200], "created": iso(now),
            "approval": "approved" if (approve or cfg.general["approval"] == "auto") else "pending",
            "language": spec.get("language"), "notes": spec.get("notes", ""), "targets": post_targets,
            "release": None}
    if auto_approve_at and post["approval"] == "pending":
        post["auto_approve_at"] = iso(auto_approve_at)
    for k in ("topic", "autopilot"):
        if spec.get(k):
            post[k] = spec[k]
    if source:
        post["source"] = source
    sched = Scheduler(cfg)

    if preview:
        q = Queue(cfg.queue_dir)
        taken = [(t["platform"], t["lane"], parse(t["slot"])) for _, t in q.scheduled()]
        sched.plan(post["targets"], taken, now, not_before, not_after)
        post["_preview"] = True
        return post

    if store is not None:
        rel = store.ensure_release(pid, post["title"])
        uploaded: dict[str, object] = {}
        for t in post["targets"]:
            for a in t["assets"]:
                if a["name"] not in uploaded:
                    uploaded[a["name"]] = store.upload(rel, Path(a["path"]), a["name"])["id"]
                a["asset_id"] = uploaded[a["name"]]
        post["release"] = {"tag": f"media-{pid}", "id": rel.get("id")}
    for t in post["targets"]:
        for a in t["assets"]:
            a.pop("path", None)
    if own_work and store is not None:
        import shutil
        shutil.rmtree(work, ignore_errors=True)  # the files now live in the release

    def apply():
        q = Queue(cfg.queue_dir)
        taken = [(t["platform"], t["lane"], parse(t["slot"])) for _, t in q.scheduled()]
        for t in post["targets"]:
            if not t["pinned"]:
                t["slot"] = None
        sched.plan(post["targets"], taken, now, not_before, not_after)
        q.put(post)
        return post

    return gitsync.transact(cfg.root, f"queue {pid}: {post['title'][:60]}", apply)


# ---------------------------------------------------------------------------
# edits
# ---------------------------------------------------------------------------
def _edit(cfg: Config, pid: str, message: str, fn):
    def apply():
        q = Queue(cfg.queue_dir)
        post = q.get(pid)
        out = fn(post)
        q.put(post)
        return out if out is not None else post
    return gitsync.transact(cfg.root, message, apply)


def approve(cfg, pid, only: list[str] | None = None):
    def fn(post):
        if post.get("approval") == "rejected":
            raise ValueError(f"{pid} was rejected; queue it again instead")
        post["approval"] = "approved"
        post.pop("auto_approve_at", None)
        if only:
            keep = set(only)
            for t in post["targets"]:
                if t["tid"] not in keep and t["platform"] not in keep and t["status"] == "planned":
                    t["status"] = "cancelled"
    return _edit(cfg, pid, f"approve {pid}", fn)


def reject(cfg, pid):
    def fn(post):
        post["approval"] = "rejected"
        post.pop("auto_approve_at", None)
        for t in post["targets"]:
            if t["status"] in ("planned", "failed_retry"):
                t["status"] = "cancelled"
    return _edit(cfg, pid, f"reject {pid}", fn)


def move(cfg, pid, tid, when_local: str):
    when = parse_local(when_local, cfg.tz)

    def fn(post):
        t = find_target(post, tid)
        if t["status"] not in ("planned", "failed_retry"):
            raise ValueError(f"{t['tid']} is {t['status']}; only planned targets can move")
        t["slot"], t["pinned"] = iso(when), True
    return _edit(cfg, pid, f"move {pid} {tid}", fn)


def publish_now(cfg, pid, tid: str | None = None, now: datetime | None = None):
    now = now or now_utc()

    def fn(post):
        for t in post["targets"]:
            if (tid is None or t["tid"] == tid or t["platform"] == tid) and t["status"] in ("planned", "failed_retry"):
                t["slot"], t["pinned"] = iso(now), True
    return _edit(cfg, pid, f"publish {pid} now", fn)


def cancel(cfg, pid, tid: str | None = None):
    def fn(post):
        for t in post["targets"]:
            if (tid is None or t["tid"] == tid or t["platform"] == tid) and t["status"] in ("planned", "failed_retry"):
                t["status"] = "cancelled"
    return _edit(cfg, pid, f"cancel {pid} {tid or 'all'}", fn)


def retry(cfg, pid, tid, now: datetime | None = None):
    now = now or now_utc()

    def fn(post):
        t = find_target(post, tid)
        if t["status"] not in ("failed", "unknown", "cancelled"):
            raise ValueError(f"{t['tid']} is {t['status']}")
        t.update(status="planned", attempts=0, error=None)
        if not t.get("slot") or parse(t["slot"]) < now:
            t["slot"], t["pinned"] = iso(now + timedelta(minutes=5)), True
    return _edit(cfg, pid, f"retry {pid} {tid}", fn)


# ---------------------------------------------------------------------------
# run-due (GitHub Actions)
# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, cfg: Config, vault, http: Http | None = None, store=None, sleep=None):
        self.cfg, self.vault, self.http, self.store = cfg, vault, http or Http(), store
        self.sleep = sleep
        self._adapters = {}
        self._hosting = None

    def adapter(self, name):
        if name not in self._adapters:
            self._adapters[name] = adapter(name, self.cfg, self.vault, self.http, None, self.sleep)
        a = self._adapters[name]
        if name in ("instagram", "threads") and a.hosting is None:
            a.hosting = self.hosting()
        return a

    def hosting(self):
        if self._hosting is None:
            from .hosting import host_for
            fb = self.adapter("facebook") if self.vault.get("facebook.page_token") else None
            self._hosting = host_for(self.cfg, self.vault, self.http, fb)
        return self._hosting

    def refresh_tokens(self) -> bool:
        changed = False
        for p in self.cfg.enabled():
            try:
                changed |= bool(self.adapter(p).refresh())
            except Exception as e:  # never block publishing on a refresh hiccup
                log.warning("token refresh for %s failed: %s", p, e)
        return changed

    def _set(self, pid, tid, **fields):
        def apply():
            q = Queue(self.cfg.queue_dir)
            post = q.get(pid)
            t = find_target(post, tid)
            t.update(fields)
            q.put(post)
        gitsync.transact(self.cfg.root, f"{fields.get('status', 'update')} {pid} {tid}", apply)

    def recover_stale(self, now):
        for post in Queue(self.cfg.queue_dir).all():
            for t in post["targets"]:
                if t["status"] == "publishing" and t.get("started_at") and now - parse(t["started_at"]) > STALE_PUBLISHING:
                    self._set(post["id"], t["tid"], status="unknown",
                              error="publishing was interrupted; check the platform, then `social retry` if it "
                                    "did not go out")

    def run(self, now: datetime | None = None, dry_run: bool | None = None, limit: int = 20) -> list[dict]:
        now = now or now_utc()
        dry = (not self.cfg.live) if dry_run is None else dry_run
        gitsync.pull(self.cfg.root)
        self.auto_approved = auto_approve_due(self.cfg, now)
        q = Queue(self.cfg.queue_dir)
        due = q.due(now, self.cfg.general["approval"])[:limit]
        report = []
        if dry:
            for post, t in due:
                report.append({"post": post["id"], "target": t["tid"], "action": "would publish (dry run)",
                               "slot": t["slot"]})
                log.info("DRY RUN: would publish %s %s (slot %s)", post["id"], t["tid"], t["slot"])
            return report
        self.recover_stale(now)
        if self.refresh_tokens() and self.vault.dirty:
            gitsync.transact(self.cfg.root, "refresh tokens", self.vault.merge_save)
        max_attempts = int(self.cfg.general.get("max_attempts", 3))
        for post, t in due:
            pid, tid = post["id"], t["tid"]
            if t["platform"] not in self.cfg.enabled():
                self._set(pid, tid, status="failed", error=f"{t['platform']} is disabled in studio.toml")
                report.append({"post": pid, "target": tid, "status": "failed", "error": "platform disabled"})
                continue
            attempts = int(t.get("attempts", 0)) + 1
            self._set(pid, tid, status="publishing", attempts=attempts, started_at=iso(now_utc()))
            try:
                with tempfile.TemporaryDirectory() as d:
                    files = []
                    for a in t["assets"]:
                        dest = Path(d) / a["name"]
                        self.store.download(a["asset_id"], dest)
                        if a.get("sha256") and M.sha256(dest) != a["sha256"]:
                            raise PublishError(f"downloaded {a['name']} does not match its checksum", transient=True)
                        files.append(dest)
                    res = self.adapter(t["platform"]).publish(t, files, post)
                self._set(pid, tid, status="published", result=res, published_at=iso(now_utc()), error=None)
                report.append({"post": pid, "target": tid, "status": "published", "url": res.get("url"),
                               "note": res.get("note")})
                log.info("published %s %s %s", pid, tid, res.get("url") or res.get("id"))
            except Exception as e:
                transient = isinstance(e, PublishError) and e.transient
                status = "failed_retry" if transient and attempts < max_attempts else "failed"
                self._set(pid, tid, status=status, error=str(e)[:1000])
                report.append({"post": pid, "target": tid, "status": status, "error": str(e)[:300]})
                log.error("%s %s %s: %s", status, pid, tid, e)
        return report


def auto_approve_due(cfg: Config, now: datetime) -> list[str]:
    """Approve pending posts whose rejection window (auto_approve_at) has passed."""
    due = [p["id"] for p in Queue(cfg.queue_dir).all()
           if p.get("approval") == "pending" and p.get("auto_approve_at") and parse(p["auto_approve_at"]) <= now
           and any(t["status"] == "planned" for t in p["targets"])]
    if not due:
        return []

    def apply():
        q = Queue(cfg.queue_dir)
        for pid in due:
            p = q.get(pid)
            if p.get("approval") == "pending":
                p["approval"] = "approved"
                p["approved_by"] = "timeout"
                q.put(p)
    gitsync.transact(cfg.root, f"auto-approve {len(due)} post(s)", apply)
    return due


def cleanup(cfg: Config, store, now: datetime | None = None) -> list[str]:
    """Delete media releases of finished posts after the retention period."""
    now = now or now_utc()
    days = int(cfg.data["github"].get("media_retention_days", 14))
    done = []
    for post in Queue(cfg.queue_dir).all():
        if post.get("release_deleted") or not post.get("release"):
            continue
        if any(t["status"] not in TERMINAL for t in post["targets"]) and post.get("approval") != "rejected":
            continue
        last = max([parse(t["published_at"]) for t in post["targets"] if t.get("published_at")] or [parse(post["created"])])
        if now - last < timedelta(days=days):
            continue
        store.delete_release(post["id"])
        done.append(post["id"])
    if done:
        def apply():
            q = Queue(cfg.queue_dir)
            for pid in done:
                p = q.get(pid)
                p["release_deleted"] = True
                q.put(p)
        gitsync.transact(cfg.root, f"clean up media of {len(done)} post(s)", apply)
    return done
