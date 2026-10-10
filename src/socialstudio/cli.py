"""`social` - the command line of Hermes Social Studio.

Where things run:
  Kaggle notebook (Hermes):  add, queue, show, approve, reject, move, now, cancel, retry, report
  You, once per account:     init, auth <platform>, secrets ...
  GitHub Actions:            run-due, insights, cleanup, approvals-sync, approvals-comment
Add --json to any command for machine-readable output.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, approvals, auth, doctor, gitsync, insights, ops
from .config import TEMPLATE, Config
from .http import Http
from .queue import Queue, parse
from .redact import install_logging, redact
from .scheduler import Scheduler
from .vault import ENV, Vault, VaultError

OUT_JSON = False


def emit(obj, text: str | None = None):
    if OUT_JSON:
        print(redact(json.dumps(obj, indent=1, ensure_ascii=False, default=str)))
    else:
        print(redact(text if text is not None else json.dumps(obj, indent=1, ensure_ascii=False, default=str)))


def die(msg: str, code: int = 2):
    if OUT_JSON:
        print(redact(json.dumps({"ok": False, "error": msg})))
    else:
        print(redact(f"error: {msg}"), file=sys.stderr)
    sys.exit(code)


def set_toml(path: Path, section: str, key: str, value: str):
    """Set `key = value` inside [section] of studio.toml, keeping comments and layout."""
    text = path.read_text() if path.exists() else ""
    header = f"[{section}]"
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == header)
    except StopIteration:
        lines += ["", header, f"{key} = {value}"]
        path.write_text("\n".join(lines) + "\n")
        return
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"^\s*{re.escape(key)}\s*=", lines[i]):
            comment = ""
            m = re.search(r"\s+#.*$", lines[i])
            if m:
                comment = m.group(0)
            lines[i] = f"{key} = {value}{comment}"
            break
    else:
        lines.insert(start + 1, f"{key} = {value}")
    path.write_text("\n".join(lines) + "\n")


def local(cfg, ts):
    return parse(ts).astimezone(ZoneInfo(cfg.tz)).strftime("%a %d %b %H:%M") if ts else "-"


# ---------------------------------------------------------------------------
def cmd_init(a, cfg: Config):
    root = cfg.root
    v = Vault(cfg.vault_path)
    pw = None
    if not v.exists():
        pw = os.environ.get(ENV)
        if not pw:
            print("Choose a vault passphrase (16+ characters; four random words work well).\n"
                  "Save it as the SOCIAL_VAULT_PASSPHRASE secret on GitHub and Kaggle.")
            pw = getpass.getpass("New passphrase: ")
            if getpass.getpass("Repeat: ") != pw:
                die("passphrases differ")
        from .vault import passphrase_strength_problem
        if passphrase_strength_problem(pw):
            die(f"weak passphrase: {passphrase_strength_problem(pw)}")

    def apply():
        made = []
        if not cfg.path.exists():
            cfg.path.write_text(TEMPLATE.format(tz=a.timezone or "Africa/Nairobi"))
            made.append("studio.toml")
        elif a.timezone:
            set_toml(cfg.path, "general", "timezone", f'"{a.timezone}"')
        (root / "queue" / "posts").mkdir(parents=True, exist_ok=True)
        (root / "queue" / "posts" / ".gitkeep").touch()
        (root / "state").mkdir(exist_ok=True)
        vv = Vault(cfg.vault_path)
        if not vv.exists():
            vv.create(pw)
            made.append("state/vault.enc")
        return made

    try:
        made = gitsync.transact(root, "social init", apply)
    except VaultError as e:
        die(str(e))
    emit({"ok": True, "created": made}, (f"created {', '.join(made)}. " if made else "already set up. ")
         + "Next: social auth <facebook|threads|youtube|bluesky|telegram>")


def cmd_auth(a, cfg: Config):
    flow = auth.FLOWS.get(a.platform)
    if not flow:
        die(f"unknown platform {a.platform}; choose {', '.join(auth.FLOWS)}")
    v = Vault(cfg.vault_path)
    try:
        v.load()
        enabled = flow(v, Http())
    except (auth.AuthError, VaultError) as e:
        die(str(e))

    def apply():
        v.merge_save()
        for p in enabled:
            if p not in ("hosting", "control", "hermes-chat"):
                set_toml(cfg.path, f"platforms.{p}", "enabled", "true")
        if a.platform == "hosting":
            set_toml(cfg.path, "hosting", "provider", '"s3"')
    gitsync.transact(cfg.root, f"connect {a.platform}", apply)
    emit({"ok": True, "enabled": enabled}, f"saved to the vault and enabled: {', '.join(enabled)}")


def cmd_secrets(a, cfg: Config):
    v = Vault(cfg.vault_path)
    try:
        if a.action == "list":
            emit(v.names(), "\n".join(v.names()) or "(empty)")
            return
        if a.action == "set":
            val = getpass.getpass(f"Value for {a.name} (hidden): ")
            v.set(a.name, val)
            msg = f"set {a.name}"
        elif a.action == "delete":
            v.delete(a.name)
            msg = f"deleted {a.name}"
        elif a.action == "change-passphrase":
            v.load()
            new = getpass.getpass("New passphrase: ")
            if getpass.getpass("Repeat: ") != new:
                die("passphrases differ")
            v.change_passphrase(new)
            gitsync.transact(cfg.root, "vault: new passphrase", lambda: v.save(force=True))
            emit({"ok": True}, "passphrase changed: update SOCIAL_VAULT_PASSPHRASE on GitHub and Kaggle NOW")
            return
        else:
            die("unknown action")
        gitsync.transact(cfg.root, f"vault: {a.action} secret", v.merge_save)
        emit({"ok": True}, msg)
    except VaultError as e:
        die(str(e))


def cmd_doctor(a, cfg: Config):
    rows = doctor.run(cfg, Http(), online=a.online)
    icons = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
    emit([{"level": l, "area": ar, "message": m} for l, ar, m in rows],
         "\n".join(f"{icons[l]} {ar:<10} {m}" for l, ar, m in rows))
    if any(l == "fail" for l, _, _ in rows):
        sys.exit(1)


def _post_summary(cfg, post, sched=None):
    sched = sched or Scheduler(cfg)
    rows = []
    for t in post["targets"]:
        why = sched.explain(t["slot"], t["platform"], t["lane"]) if t.get("slot") else ""
        rows.append({"target": t["tid"], "when_local": local(cfg, t.get("slot")), "slot_utc": t.get("slot"),
                     "status": t["status"], "url": (t.get("result") or {}).get("url"), "error": t.get("error"),
                     "why": why.split("|", 1)[-1].strip() if why else ""})
    return {"id": post["id"], "title": post["title"], "approval": post["approval"], "targets": rows}


def _text(s):
    lines = [f"{s['id']}  {s['title']}  [{s['approval']}]"]
    for r in s["targets"]:
        extra = r["url"] or r["error"] or r["why"]
        lines.append(f"   {r['target']:<20} {r['when_local']:<18} {r['status']:<12} {extra or ''}")
    return "\n".join(lines)


def cmd_add(a, cfg: Config):
    spec_path = Path(a.spec)
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"cannot read {spec_path}: {e}")
    from . import outbox
    box = None if a.preview else outbox.outbox_dir()
    if box:  # on a Kaggle run started from GitHub: stage it; the publisher queues it afterwards
        try:
            post = outbox.stage(cfg, spec, spec_path.parent, box)
        except ops.AddError as e:
            if OUT_JSON:
                print(json.dumps({"ok": False, "problems": e.problems}, indent=1))
                sys.exit(2)
            die("the post spec has problems:\n  - " + "\n  - ".join(e.problems))
        s = _post_summary(cfg, post)
        s.update(ok=True, staged=post["staged"], approval="decided after this session",
                 next="staged: it is queued automatically after this Kaggle session ends (planned times may "
                      "shift a little). Tell the user it will come to them for approval.")
        emit(s, _text(s) + "\nStaged: queued automatically after this session.")
        return
    store = None
    if not a.preview:
        from .storage import store_for
        try:
            store = store_for(cfg.root)
        except RuntimeError as e:
            die(str(e))
    try:
        post = ops.add(cfg, spec, spec_path.parent, store=store, approve=a.approve, preview=a.preview)
    except ops.AddError as e:
        if OUT_JSON:
            print(json.dumps({"ok": False, "problems": e.problems}, indent=1))
            sys.exit(2)
        die("the post spec has problems:\n  - " + "\n  - ".join(e.problems))
    s = _post_summary(cfg, post)
    s["preview"] = bool(a.preview)
    nxt = ("Approve with: social approve " + post["id"]) if post["approval"] == "pending" and not a.preview else ""
    if post["approval"] == "pending" and not a.preview:
        s["next"] = f"ask the user, then: social approve {post['id']} (or they comment /approve on the GitHub issue)"
    emit(s, _text(s) + (f"\n{nxt}" if nxt else ""))


def cmd_queue(a, cfg: Config):
    gitsync.pull(cfg.root)
    sched = Scheduler(cfg)
    posts = Queue(cfg.queue_dir).all()
    if not a.all:
        posts = [p for p in posts if p["approval"] != "rejected"
                 and any(t["status"] in ("planned", "publishing", "failed_retry", "failed", "unknown")
                         for t in p["targets"])]
    sums = [_post_summary(cfg, p, sched) for p in sorted(posts, key=lambda p: p["created"])]
    emit(sums, "\n\n".join(_text(s) for s in sums) or "queue is empty")


def cmd_show(a, cfg: Config):
    gitsync.pull(cfg.root)
    try:
        post = Queue(cfg.queue_dir).get(a.id)
    except KeyError as e:
        die(str(e))
    emit(post)


def _edit(fn, *args):
    try:
        post = fn(*args)
    except (KeyError, ValueError) as e:
        die(str(e))
    return post


def cmd_approve(a, cfg):
    only = [x.strip() for x in a.only.split(",")] if a.only else None
    p = _edit(ops.approve, cfg, a.id, only)
    s = _post_summary(cfg, p)
    emit(s, _text(s))


def cmd_reject(a, cfg):
    p = _edit(ops.reject, cfg, a.id)
    emit(_post_summary(cfg, p), f"rejected {a.id}")


def cmd_move(a, cfg):
    p = _edit(ops.move, cfg, a.id, a.target, a.when)
    s = _post_summary(cfg, p)
    emit(s, _text(s))


def cmd_now(a, cfg):
    p = _edit(ops.publish_now, cfg, a.id, a.target)
    s = _post_summary(cfg, p)
    emit(s, _text(s) + "\nGoes out at the next publisher run (every 30 min).")


def cmd_cancel(a, cfg):
    p = _edit(ops.cancel, cfg, a.id, a.target)
    s = _post_summary(cfg, p)
    emit(s, _text(s))


def cmd_retry(a, cfg):
    p = _edit(ops.retry, cfg, a.id, a.target)
    s = _post_summary(cfg, p)
    emit(s, _text(s))


def _runner(cfg):
    from .storage import store_for
    v = Vault(cfg.vault_path)
    try:
        v.load()
    except VaultError as e:
        die(str(e))
    return ops.Runner(cfg, v, Http(), store_for(cfg.root))


def cmd_run_due(a, cfg):
    r = _runner(cfg)
    rep = r.run(dry_run=True if a.dry_run else None)
    from . import control
    bad = [x for x in rep if x.get("status") == "failed"]
    if bad and control.configured(r.vault):
        control.notify(r.vault, "Publishing failed:\n" + "\n".join(
            f"- {x['post']} {x['target']}: {x.get('error', '')[:200]}" for x in bad)
            + "\nSee the queue (/queue) or retry with: social retry <post> <target>")
    if getattr(r, "auto_approved", None) and control.configured(r.vault):
        control.notify(r.vault, "No rejection received, so these are now approved: " + ", ".join(r.auto_approved))
    emit(rep, "\n".join(f"{x['post']} {x['target']}: {x.get('status') or x.get('action')} "
                        f"{x.get('url') or x.get('error') or ''}" for x in rep) or "nothing due")
    if any(x.get("status") == "failed" for x in rep):
        sys.exit(3)


def cmd_insights(a, cfg):
    s = insights.evaluate(cfg, _runner(cfg))
    emit(s, f"evaluated {s['evaluated']} post(s); weight changes: {len(s['nudges'])}")


def cmd_report(a, cfg):
    gitsync.pull(cfg.root)
    rep = insights.report(cfg, a.days)
    txt = [f"Last {a.days} days: {len(rep['published'])} published"]
    for r in rep["published"]:
        txt.append(f"  {local(cfg, r['published_at'])}  {r['target']:<20} {r['url'] or ''}  "
                   f"engagement {r['engagement'] if r['engagement'] is not None else '(pending)'}")
    txt.append("status: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["status_counts"].items())))
    emit(rep, "\n".join(txt))


def cmd_cleanup(a, cfg):
    from .storage import store_for
    done = ops.cleanup(cfg, store_for(cfg.root))
    emit(done, f"deleted media of {len(done)} post(s)")


def _vault_or_none(cfg):
    if not os.environ.get(ENV):
        return None
    v = Vault(cfg.vault_path)
    try:
        v.load()
        return v
    except VaultError:
        return None


def _store_or_none(cfg):
    from .storage import store_for
    try:
        return store_for(cfg.root)
    except RuntimeError:
        return None


def cmd_approvals_sync(a, cfg):
    gitsync.pull(cfg.root)
    emit(approvals.sync(cfg, vault=_vault_or_none(cfg), store=_store_or_none(cfg)), None)


# --- autopilot, Kaggle and the control bot ------------------------------------
def cmd_autopilot(a, cfg):
    from . import autopilot as AP
    if a.action == "init":
        if AP.path(cfg).exists():
            die("autopilot.toml already exists")
        gitsync.transact(cfg.root, "autopilot: plan template", lambda: AP.path(cfg).write_text(AP.TEMPLATE))
        emit({"ok": True}, f"wrote {AP.path(cfg)}; edit it, then set enabled = true")
        return
    from datetime import date as _date
    day = _date.fromisoformat(a.date) if a.date else AP.local_today(cfg)
    plan = AP.load(cfg)
    out = {"problems": AP.validate(cfg, plan), "brief": AP.brief(cfg, day),
           "enabled": plan["autopilot"].get("enabled", False), "run_at": plan["autopilot"].get("run_at")}
    lines = [f"autopilot {'on' if out['enabled'] else 'OFF'}, daily at {out['run_at']} {cfg.tz}",
             f"{day:%A %d %b}: " + (", ".join(f"{s['id']} ({s['kind']} -> {', '.join(s['targets'])})"
                                             for s in out["brief"]["slots"]) or "nothing planned")]
    lines += [f"problem: {p}" for p in out["problems"]]
    emit(out, "\n".join(lines))


def _report_collect(cfg, vault, reps):
    from . import control
    for rep in reps:
        r = rep["run"]
        if rep["posts"]:
            msg = (f"Kaggle {r['mode']} run {r['run_id']} finished: {len(rep['posts'])} post(s) queued.")
        elif r["mode"] == "chat" and not rep["errors"]:
            msg = None
        else:
            msg = f"Kaggle {r['mode']} run {r['run_id']} finished with nothing queued."
        if rep["errors"]:
            msg = (msg or f"Kaggle {r['mode']} run {r['run_id']}:") + "\nProblems:\n" + \
                "\n".join("- " + e[:300] for e in rep["errors"][:6]) + f"\nLog: {r.get('url', '')}"
        if msg and vault:
            control.notify(vault, msg)


def cmd_kaggle_collect(a, cfg):
    from . import kaggle as K
    if not K.active_runs(cfg):
        emit([], "no Kaggle run to collect")
        return
    store = _store_or_none(cfg)
    reps = K.collect(cfg, store)
    vault = _vault_or_none(cfg)
    _report_collect(cfg, vault, reps)
    if vault and any(rep["posts"] for rep in reps):
        approvals.sync(cfg, vault=vault, store=store)
    emit([{"run": r["run"]["run_id"], "status": r["status"], "posts": [p["id"] for p in r["posts"]],
           "errors": r["errors"]} for r in reps], None)


def cmd_kaggle_launch(a, cfg):
    from . import control, kaggle as K
    try:
        r = K.launch(cfg, a.mode, day=a.date, idle_minutes=a.idle, reason="manual")
    except K.KaggleError as e:
        die(str(e))
    v = _vault_or_none(cfg)
    if v and a.mode == "chat":
        control.notify(v, f"Starting Hermes on Kaggle (run {r['run_id']}); it will message you in about 10 minutes.")
    emit(r, f"launched {a.mode} run {r['run_id']}: {r['url']}")


def cmd_autopilot_launch(a, cfg):
    from . import autopilot as AP, control, kaggle as K
    from .queue import now_utc
    if a.if_due:
        due, why = AP.is_due(cfg, K.load_state(cfg), now_utc())
        if not due:
            emit({"launched": False, "why": why}, f"autopilot: {why}")
            return
    try:
        r = K.launch(cfg, "autopilot", day=a.date, reason="schedule" if a.if_due else "manual")
    except K.KaggleError as e:
        v = _vault_or_none(cfg)
        if v and a.if_due:
            control.notify(v, f"Daily autopilot could not start: {e}")
        die(str(e))
    emit(r, f"autopilot run {r['run_id']} for {r['date']} launched: {r['url']}")


def cmd_kaggle_keys(a, cfg):
    from . import kaggle as K
    v = _vault_or_none(cfg)
    if not v:
        die(f"needs the vault ({ENV})")
    try:
        slug = K.keys_sync(cfg, v)
    except (K.KaggleError, VaultError) as e:
        die(str(e))
    emit({"ok": True, "dataset": slug}, f"private dataset {slug} updated; launched sessions attach it")


def cmd_telegram_handle(a, cfg):
    from . import control
    raw = a.update or os.environ.get("TELEGRAM_UPDATE", "")
    try:
        upd = json.loads(raw)
    except ValueError:
        die("no update JSON")
    if "update_id" in upd:
        upd = control.normalize(upd)
    v = _vault_or_none(cfg)
    if not v:
        die(f"needs the vault ({ENV})")
    gitsync.pull(cfg.root)
    emit({"result": control.handle(cfg, v, upd, _store_or_none(cfg))}, None)


def cmd_telegram_poll(a, cfg):
    from . import control
    v = _vault_or_none(cfg)
    if not v or not control.configured(v):
        emit([], "control bot not configured")
        return
    emit(control.poll(cfg, v, _store_or_none(cfg)), None)


def cmd_telegram_webhook(a, cfg):
    from . import control
    v = _vault_or_none(cfg)
    if not v:
        die(f"needs the vault ({ENV})")
    emit({"ok": True, "result": control.set_webhook(v, a.url)}, "webhook set; send /help to your control bot")


def cmd_approvals_comment(a, cfg):
    emit({"result": approvals.handle_comment(cfg, approvals.load_event())}, None)


def cmd_enable(a, cfg, value="true"):
    from .config import PLATFORMS
    if a.platform not in PLATFORMS:
        die(f"unknown platform {a.platform}")
    gitsync.transact(cfg.root, f"{'enable' if value == 'true' else 'disable'} {a.platform}",
                     lambda: set_toml(cfg.path, f"platforms.{a.platform}", "enabled", value))
    emit({"ok": True}, f"{a.platform} {'enabled' if value == 'true' else 'disabled'}")


def cmd_mode(a, cfg):
    gitsync.transact(cfg.root, f"mode {a.mode}", lambda: set_toml(cfg.path, "general", "mode", f'"{a.mode}"'))
    emit({"ok": True, "mode": a.mode}, f"mode is now {a.mode}")


def build_parser():
    ap = argparse.ArgumentParser(prog="social", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--version", action="version", version=f"social {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create studio.toml and the encrypted vault")
    s.add_argument("--timezone", help="your audience's IANA timezone, e.g. Africa/Nairobi")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("auth", help="connect an account (interactive, hidden input)")
    s.add_argument("platform")
    s.set_defaults(fn=cmd_auth)
    s = sub.add_parser("secrets", help="list|set|delete|change-passphrase")
    s.add_argument("action", choices=["list", "set", "delete", "change-passphrase"])
    s.add_argument("name", nargs="?")
    s.set_defaults(fn=cmd_secrets)
    s = sub.add_parser("doctor")
    s.add_argument("--online", action="store_true")
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("enable")
    s.add_argument("platform")
    s.set_defaults(fn=cmd_enable)
    s = sub.add_parser("disable")
    s.add_argument("platform")
    s.set_defaults(fn=lambda a, c: cmd_enable(a, c, "false"))
    s = sub.add_parser("mode", help="switch between dry-run and live")
    s.add_argument("mode", choices=["dry-run", "live"])
    s.set_defaults(fn=cmd_mode)

    s = sub.add_parser("add", help="queue a post from a JSON spec (see examples/posts)")
    s.add_argument("spec")
    s.add_argument("--approve", action="store_true", help="approve right away (only when the user said so)")
    s.add_argument("--preview", action="store_true", help="validate and show planned times; change nothing")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("queue")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_queue)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("approve")
    s.add_argument("id")
    s.add_argument("--only", help="comma-separated targets/platforms to keep; others are cancelled")
    s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("reject")
    s.add_argument("id")
    s.set_defaults(fn=cmd_reject)
    s = sub.add_parser("move", help='pin a target: social move ID instagram:reel "2026-10-09 19:00"')
    s.add_argument("id")
    s.add_argument("target")
    s.add_argument("when")
    s.set_defaults(fn=cmd_move)
    s = sub.add_parser("now", help="publish at the next run")
    s.add_argument("id")
    s.add_argument("target", nargs="?")
    s.set_defaults(fn=cmd_now)
    s = sub.add_parser("cancel")
    s.add_argument("id")
    s.add_argument("target", nargs="?")
    s.set_defaults(fn=cmd_cancel)
    s = sub.add_parser("retry")
    s.add_argument("id")
    s.add_argument("target")
    s.set_defaults(fn=cmd_retry)
    s = sub.add_parser("report")
    s.add_argument("--days", type=int, default=7)
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("run-due", help="(GitHub Actions) publish everything that is due")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_run_due)
    sub.add_parser("insights", help="(GitHub Actions) learn from engagement").set_defaults(fn=cmd_insights)
    sub.add_parser("cleanup", help="(GitHub Actions) delete old media releases").set_defaults(fn=cmd_cleanup)
    sub.add_parser("approvals-sync").set_defaults(fn=cmd_approvals_sync)
    sub.add_parser("approvals-comment").set_defaults(fn=cmd_approvals_comment)

    s = sub.add_parser("autopilot", help="check the daily plan (or `autopilot init` to create it)")
    s.add_argument("action", nargs="?", choices=["show", "init"], default="show")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s.set_defaults(fn=cmd_autopilot)
    s = sub.add_parser("autopilot-launch", help="(GitHub Actions) start today's autopilot run on Kaggle")
    s.add_argument("--if-due", action="store_true", help="only when run_at has passed and it has not run today")
    s.add_argument("--date")
    s.set_defaults(fn=cmd_autopilot_launch)
    s = sub.add_parser("kaggle-launch", help="(GitHub Actions) start a Kaggle run")
    s.add_argument("--mode", choices=["autopilot", "chat"], required=True)
    s.add_argument("--date")
    s.add_argument("--idle", type=int)
    s.set_defaults(fn=cmd_kaggle_launch)
    sub.add_parser("kaggle-collect", help="(GitHub Actions) queue posts from finished Kaggle runs").set_defaults(
        fn=cmd_kaggle_collect)
    sub.add_parser("kaggle-keys", help="(GitHub Actions) copy the Hermes chat bot token to a private Kaggle dataset"
                   ).set_defaults(fn=cmd_kaggle_keys)
    s = sub.add_parser("telegram-handle", help="(GitHub Actions) handle one control-bot update")
    s.add_argument("--update")
    s.set_defaults(fn=cmd_telegram_handle)
    sub.add_parser("telegram-poll", help="(GitHub Actions) process control-bot messages in poll mode").set_defaults(
        fn=cmd_telegram_poll)
    s = sub.add_parser("telegram-webhook", help="(GitHub Actions) point the control bot at your Worker")
    s.add_argument("url")
    s.set_defaults(fn=cmd_telegram_webhook)
    return ap


def main(argv=None):
    global OUT_JSON
    install_logging(logging.DEBUG if os.environ.get("SOCIAL_DEBUG") == "1" else logging.INFO)
    a = build_parser().parse_args(argv)
    OUT_JSON = a.json
    cfg = Config()
    try:
        a.fn(a, cfg)
    except SystemExit:
        raise
    except Exception as e:
        if os.environ.get("SOCIAL_DEBUG") == "1":
            raise
        die(f"{type(e).__name__}: {e}", 1)


if __name__ == "__main__":
    main()
