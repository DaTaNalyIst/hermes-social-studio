#!/usr/bin/env python3
"""Kaggle side of autopilot: ask Hermes to make and stage one post per slot in today's brief.

usage: python autopilot.py brief.json result.json

For each slot Hermes gets a precise, self-contained task (it cannot ask questions: nobody is
watching). Success is checked, not trusted: a slot counts as done only when a post for it has
been staged in the outbox by `social add`. One repair round is allowed per slot, with the exact
validation problems fed back.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from studiolib import common as C  # noqa: E402

HERMES = str(Path.home() / ".local" / "bin" / "hermes")
POINTER = Path.home() / ".social-studio.json"
SLOT_TIMEOUT = {"short": 3 * 3600, "clip": 5400, "image": 2400, "carousel": 3000, "text": 900}


def social_cmd() -> list:
    exe = shutil.which("social")
    return [exe] if exe else [sys.executable, "-m", "socialstudio.cli"]


def set_tag(tag: dict | None):
    data = json.loads(POINTER.read_text()) if POINTER.exists() else {}
    if tag:
        data["tag"] = tag
    else:
        data.pop("tag", None)
    POINTER.write_text(json.dumps(data))


def staged_for(outbox: Path, date: str, slot: str) -> list[str]:
    hits = []
    for spec in outbox.glob("*/spec.json"):
        try:
            ap = json.loads(spec.read_text()).get("autopilot") or {}
        except ValueError:
            continue
        if ap.get("date") == date and ap.get("slot") == slot:
            hits.append(spec.parent.name)
    return hits


def ask_hermes(text: str, timeout: float) -> str:
    cmd = [HERMES, "chat", "--query-file", "-", "--format", "stream-json"]
    try:
        p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=timeout, cwd=str(C.WORK))
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    final = ""
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "tool_use":
            print(f"   -> {ev.get('name')} {json.dumps(ev.get('input', ''))[:200]}", flush=True)
        elif ev.get("type") == "result":
            final = ev.get("text") or ev.get("error") or ""
    return final


def task_text(b: dict, slot: dict, post_file: Path) -> str:
    social = " ".join(social_cmd())
    targets = json.dumps([{"platform": t.split(":")[0], "format": t.split(":")[1]} for t in slot["targets"]])
    recent = ", ".join(b.get("recent_topics", [])[-15:]) or "none yet"
    topics = "; ".join(b.get("topics", [])) or "anything that fits the brand"
    avoid = ", ".join(b.get("avoid", [])) or "nothing special"
    media_step = ("This is a TEXT post: no media. Skip step 1." if slot["kind"] == "text" else
                  f"1. Make {slot['kind_text']} with the kaggle-studio skill. Review it and redo it once if it "
                  f"is weak. Note the absolute file path(s) of the final media.")
    return f"""AUTOPILOT TASK. Nobody is watching: do not ask questions, decide yourself and finish the task.

Brand: {b.get('brand')}
Audience: {b.get('audience')}
Visual style: {b.get('style')}
Voice: {b.get('voice')}
Language for captions and narration: {b.get('language')}
Never include: {avoid}
Topic ideas: {topics}
Topics used recently (do NOT repeat): {recent}
Date the post goes out: {b['date']} ({b['timezone']})
Extra notes for this slot: {slot.get('notes') or 'none'}

Choose ONE fresh topic that fits the brand. Then:
{media_step}
2. With the file tool, write the post file {post_file} following the social-publisher skill. It must
   contain "title", "topic" (the topic you chose), "language", "media" (the absolute paths, or [] for
   text), a caption written for each target in its own style and limits, and exactly these targets:
   {targets}
3. Run: {social} --json add {post_file}
   If it reports problems, fix the post file and run it again until it succeeds.
   (On autopilot, `add` stages the post; the publisher queues it after this run. Do not use --approve.)
4. Reply with one line: DONE <topic>, or FAILED <short reason>.
"""


def fix_text(post_file: Path, problems: str) -> str:
    social = " ".join(social_cmd())
    return f"""AUTOPILOT REPAIR. The post file {post_file} was not accepted:
{problems}
Fix the file (keep the same media and targets; shorten captions or remove hashtags as needed), then run:
{social} --json add {post_file}
Repeat until it succeeds. Reply DONE or FAILED <reason>."""


def preview_problems(post_file: Path) -> str:
    p = subprocess.run([*social_cmd(), "--json", "add", str(post_file), "--preview"], capture_output=True, text=True)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        return (p.stdout + p.stderr)[-800:] or "could not validate the file"
    if out.get("problems"):
        return "\n".join("- " + x for x in out["problems"])
    if out.get("ok") is False:
        return out.get("error", "unknown problem")
    return ""


def main(brief_path, result_path):
    b = json.loads(Path(brief_path).read_text())
    outbox = Path(json.loads(POINTER.read_text())["outbox"])
    posts_dir = C.OUT / "posts"
    posts_dir.mkdir(parents=True, exist_ok=True)
    result = {"date": b["date"], "staged": [], "failed": []}
    for slot in b["slots"]:
        sid = slot["id"]
        post_file = posts_dir / f"{b['date']}-{sid}.json"
        set_tag({"autopilot": {"date": b["date"], "slot": sid}})
        t0 = time.time()
        timeout = SLOT_TIMEOUT.get(slot["kind"], 3600)
        print(f"\n=== slot {sid} ({slot['kind']}) -> {', '.join(slot['targets'])}", flush=True)
        reply = ask_hermes(task_text(b, slot, post_file), timeout)
        print(f"   Hermes: {reply[-300:]}", flush=True)
        if not staged_for(outbox, b["date"], sid):
            remaining = max(600, timeout - (time.time() - t0))
            if post_file.exists():
                probs = preview_problems(post_file)
                if not probs:  # the file is fine; Hermes just did not run add
                    subprocess.run([*social_cmd(), "--json", "add", str(post_file)], capture_output=True, text=True)
                else:
                    reply = ask_hermes(fix_text(post_file, probs), min(remaining, 1800))
            else:
                reply = ask_hermes("You did not finish the AUTOPILOT TASK. Continue from where you stopped "
                                   "and complete every step:\n\n" + task_text(b, slot, post_file), remaining)
            print(f"   Hermes (retry): {reply[-300:]}", flush=True)
        staged = staged_for(outbox, b["date"], sid)
        if staged:
            result["staged"].append({"slot": sid, "items": staged, "seconds": int(time.time() - t0)})
        else:
            reason = reply[-200:] or "no post was staged"
            if post_file.exists():
                reason = preview_problems(post_file)[:400] or reason
            result["failed"].append({"slot": sid, "reason": reason})
        Path(result_path).write_text(json.dumps(result, indent=1))
    set_tag(None)
    Path(result_path).write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
