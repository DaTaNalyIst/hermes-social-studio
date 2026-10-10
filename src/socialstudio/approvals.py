"""Approve posts from your phone: one GitHub issue per pending post, answered with comments.

`social approvals-sync` (run by the approvals workflow after every queue change) opens an issue
for each post awaiting approval, keeps its schedule table current and closes it once decided.
`social approvals-comment` handles a new comment: only the repo owner and collaborators count.

Commands (one per line):
  /approve                    approve every target as planned
  /approve instagram,youtube  approve these only; cancel the rest
  /reject                     cancel the whole post
  /move instagram:reel 2026-10-09 19:00   pin a target to a local time
  /now [target]               publish at the next publisher run
"""

from __future__ import annotations

import json
import os
from zoneinfo import ZoneInfo

from . import ops
from .config import Config
from .http import Http
from .queue import Queue, parse
from .scheduler import Scheduler
from .storage import API, detect_repo, token

LABEL = "social-approval"
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}


class GitHub:
    def __init__(self, repo: str, http: Http | None = None, tok: str | None = None):
        self.repo, self.http, self.tok = repo, http or Http(), tok

    def _h(self):
        return {"Authorization": f"Bearer {self.tok or token()}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}

    def req(self, method, path, body=None):
        return self.http.request(method, f"{API}/repos/{self.repo}/{path}", json_body=body, headers=self._h(),
                                 platform="github")

    def ensure_label(self):
        try:
            self.req("GET", f"labels/{LABEL}")
        except Exception:
            self.req("POST", "labels", {"name": LABEL, "color": "0e8a16",
                                        "description": "Social post waiting for approval"})

    def create_issue(self, title, body):
        return self.req("POST", "issues", {"title": title, "body": body, "labels": [LABEL]})

    def update_issue(self, number, **fields):
        return self.req("PATCH", f"issues/{number}", fields)

    def comment(self, number, body):
        return self.req("POST", f"issues/{number}/comments", {"body": body})


def issue_body(cfg: Config, post: dict, repo: str) -> str:
    tz = ZoneInfo(cfg.tz)
    sched = Scheduler(cfg)
    lines = [f"**{post['title'] or post['id']}** · `{post['id']}` · approval: **{post['approval']}**", "",
             f"| Target | When ({cfg.tz}) | Status | Why this time |", "|---|---|---|---|"]
    for t in post["targets"]:
        when = parse(t["slot"]).astimezone(tz).strftime("%a %d %b %H:%M") if t.get("slot") else "-"
        why = sched.explain(t["slot"], t["platform"], t["lane"]).split("|", 1)[-1].strip() \
            if t.get("slot") and not t.get("pinned") else ("pinned" if t.get("pinned") else "")
        lines.append(f"| `{t['tid']}` | {when} | {t['status']} | {why} |")
    lines += ["", "### Captions"]
    for t in post["targets"]:
        cap = (t.get("title") + "\n\n" if t.get("title") else "") + (t.get("caption") or "(no text)")
        quoted = "\n".join("> " + line for line in cap.splitlines()) or "> "
        lines += [f"<details><summary><code>{t['tid']}</code></summary>", "", quoted, "", "</details>"]
    names = {}
    for t in post["targets"]:
        for a in t.get("assets", []):
            names[a["name"]] = a
    if names:
        lines += ["", "### Media"]
        for n, a in names.items():
            dur = f", {a['duration']:.0f} s" if a.get("duration") else ""
            url = f"https://github.com/{repo}/releases/download/media-{post['id']}/{n}"
            lines.append(f"- [{n}]({url}) ({a.get('width')}x{a.get('height')}{dur})")
    lines += ["", "### Reply with one command", "",
              "- `/approve` post everything as planned",
              "- `/approve instagram,youtube` only these targets; cancel the rest",
              "- `/move instagram:reel 2026-10-09 19:00` pin a target to a local time",
              "- `/now` or `/now youtube:short` publish at the next run (within ~30 min)",
              "- `/reject` cancel the post", "",
              "<!-- social-studio:" + post["id"] + " -->"]
    return "\n".join(lines)


def sync(cfg: Config, gh: GitHub | None = None, vault=None, store=None, now=None) -> list[str]:
    """Keep approval issues current; with the vault, also send new posts to the control bot."""
    try:
        repo = gh.repo if gh else detect_repo(cfg.root)
        gh = gh or GitHub(repo)
    except RuntimeError:  # not a GitHub clone (local use, tests): Telegram only
        repo, gh = "", None
    done = []
    pending_issue = {}
    notified = []
    gh_label_ready = False
    from datetime import timedelta
    from . import control
    from .queue import now_utc
    now = now or now_utc()
    tg = control.configured(vault)
    for post in Queue(cfg.queue_dir).all():
        if tg and not post.get("telegram_notified") and post.get("approval") != "rejected" \
                and any(t["status"] == "planned" for t in post["targets"]) \
                and now - parse(post["created"]) < timedelta(days=2):
            control.send_post(cfg, vault, post, store)
            notified.append(post["id"])
            done.append(f"sent {post['id']} to Telegram")
        num = post.get("approval_issue")
        if gh is None:
            continue
        if post["approval"] == "pending" and any(t["status"] == "planned" for t in post["targets"]):
            body = issue_body(cfg, post, repo)
            if not num:
                if not gh_label_ready:
                    gh.ensure_label()
                    gh_label_ready = True
                r = gh.create_issue(f"Approve: {post['title'] or post['id']} ({post['id']})", body)
                pending_issue[post["id"]] = r["number"]
                done.append(f"opened #{r['number']} for {post['id']}")
            else:
                gh.update_issue(num, body=body)
        elif num and not post.get("approval_issue_closed"):
            gh.update_issue(num, state="closed", body=issue_body(cfg, post, repo))
            pending_issue[post["id"]] = -num
            done.append(f"closed #{num} for {post['id']}")
    if pending_issue or notified:
        def apply():
            q = Queue(cfg.queue_dir)
            for pid, n in pending_issue.items():
                p = q.get(pid)
                if n > 0:
                    p["approval_issue"] = n
                else:
                    p["approval_issue_closed"] = True
                q.put(p)
            for pid in notified:
                p = q.get(pid)
                p["telegram_notified"] = True
                q.put(p)
        from . import gitsync
        gitsync.transact(cfg.root, "approval issues", apply)
    return done


def parse_commands(text: str) -> list[list[str]]:
    cmds = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("/"):
            parts = line.split()
            cmds.append([parts[0][1:].lower(), *parts[1:]])
    return cmds


def handle_comment(cfg: Config, event: dict, gh: GitHub | None = None) -> str:
    issue, comment = event.get("issue", {}), event.get("comment", {})
    if comment.get("user", {}).get("type") == "Bot":
        return "ignored: bot comment"
    if comment.get("author_association") not in TRUSTED:
        return "ignored: comment from someone without write access"
    body = issue.get("body") or ""
    marker = body.rsplit("<!-- social-studio:", 1)
    if len(marker) != 2:
        return "ignored: not a social-studio issue"
    pid = marker[1].split("-->")[0].strip()
    gh = gh or GitHub(detect_repo(cfg.root))
    results = []
    for cmd in parse_commands(comment.get("body", "")):
        name, args = cmd[0], cmd[1:]
        try:
            if name == "approve":
                only = [a.strip() for a in " ".join(args).split(",") if a.strip()] or None
                ops.approve(cfg, pid, only)
                results.append("approved" + (f" ({', '.join(only)} only)" if only else ""))
            elif name == "reject":
                ops.reject(cfg, pid)
                results.append("rejected; nothing will be posted")
            elif name == "move" and len(args) >= 3:
                ops.move(cfg, pid, args[0], " ".join(args[1:3]))
                results.append(f"moved {args[0]} to {' '.join(args[1:3])} ({cfg.tz})")
            elif name == "now":
                ops.publish_now(cfg, pid, args[0] if args else None)
                results.append("will publish at the next run" + (" once approved" if
                               Queue(cfg.queue_dir).get(pid)["approval"] != "approved" else ""))
            else:
                results.append(f"unknown command /{name}")
        except Exception as e:
            results.append(f"/{name} failed: {e}")
    if not results:
        return "no commands"
    msg = "\n".join(f"- {r}" for r in results)
    gh.comment(issue["number"], msg)
    return msg


def load_event() -> dict:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise RuntimeError("GITHUB_EVENT_PATH is not set (this runs inside GitHub Actions)")
    with open(path) as f:
        return json.load(f)
