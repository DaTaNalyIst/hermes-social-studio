#!/usr/bin/env python3
"""studio.py - the one command Hermes uses to make images, videos and films on Kaggle.

Quick use (Hermes calls these through its terminal tool):
  studio.py image "PROMPT" [--preset klein4b|zimage] [--n 4] [--aspect square|portrait|landscape]
  studio.py video "PROMPT" [--preset wan14b|wan5b|wan14b_hq] [--n 1] [--aspect landscape|portrait]
                           [--seconds 5] [--smooth]
  studio.py wait JOB_ID            # keeps waiting (max ~150 s per call); repeat until status is done
  studio.py review JOB_ID          # vision scores, best item, suggested prompt fixes
  studio.py rerun JOB_ID --item ID [--prompt "..."] [--seed N] [--preset P]
  studio.py film FILM.json [--only s04,s09]
  studio.py deliver PATH [PATH ...] # prints MEDIA: lines that Telegram turns into attachments
  studio.py status | presets | jobs | disk
Every command prints JSON. Paths are absolute.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studiolib import common as C  # noqa: E402
from studiolib import jobs as J  # noqa: E402
from studiolib import presets as P  # noqa: E402

DEFAULT_WAIT = float(os.environ.get("STUDIO_WAIT", "150"))
CMD = f"{sys.executable} {Path(__file__).resolve()}"  # exact command line for the agent to reuse

IMAGE_SIZES = {"square": (1024, 1024), "portrait": (768, 1344), "landscape": (1344, 768)}
VIDEO_SIZES = {
    "wan14b": {"landscape": (832, 480), "portrait": (480, 832), "square": (624, 624)},
    "wan14b_hq": {"landscape": (832, 480), "portrait": (480, 832), "square": (624, 624)},
    "wan5b": {"landscape": (1280, 704), "portrait": (704, 1280), "square": (960, 960)},
}


def deliver_lines(st: dict) -> list:
    lines = []
    for o in st.get("outputs", []):
        if o["type"] == "image":
            lines.append(C.media_line(o["path"]))
        elif o["type"] == "video":
            from studiolib import media
            lines.append(C.media_line(media.fit_for_telegram(o.get("smooth") or o["path"])))
        elif o["type"] == "film":
            lines.append(C.media_line(o.get("telegram") or o["path"]))
    return lines


def report(st: dict, extra: dict | None = None):
    s = J.summary(st)
    if st["status"] in ("done", "partial"):
        s["deliver"] = deliver_lines(st)
        s["next"] = (f"review with: {CMD} review {st['job_id']}  (or send the MEDIA lines to the user)"
                     if st.get("preset") != "film" else "send the MEDIA line to the user")
    elif st["status"] == "failed":
        s["next"] = "read the message; fix the job or report it. Logs: " + str(J.job_dir(st["job_id"]) / "worker.log")
    else:
        s["next"] = f"still working - run: {CMD} wait {st['job_id']}"
    if extra:
        s.update(extra)
    C.jprint(s)


def _submit_and_wait(job: dict, wait_s: float):
    try:
        st = J.submit(job)
    except (ValueError, KeyError) as e:
        C.fail(str(e))
    st = J.wait(st["job_id"], wait_s)
    report(st)


def cmd_image(a):
    preset = a.preset or "klein4b"
    if P.PRESETS.get(preset, {}).get("kind") != "image":
        C.fail(f"{preset} is not an image preset (use klein4b or zimage)")
    w, h = (a.width, a.height) if a.width and a.height else IMAGE_SIZES[a.aspect]
    seed0 = a.seed if a.seed is not None else int(time.time()) % 100000
    items = [{"id": f"v{k + 1}", "prompt": a.prompt, "seed": seed0 + k} for k in range(a.n)]
    _submit_and_wait({"name": a.name or a.prompt[:40], "preset": preset,
                      "defaults": {"width": w, "height": h}, "items": items}, a.wait)


def cmd_video(a):
    preset = a.preset or "wan14b"
    if P.PRESETS.get(preset, {}).get("kind") != "video":
        C.fail(f"{preset} is not a video preset (use wan14b, wan5b or wan14b_hq)")
    w, h = (a.width, a.height) if a.width and a.height else VIDEO_SIZES[preset][a.aspect]
    fps = P.PRESETS[preset]["defaults"]["fps"]
    length = int(round(a.seconds * fps)) + 1
    seed0 = a.seed if a.seed is not None else int(time.time()) % 100000
    items = [{"id": f"v{k + 1}", "prompt": a.prompt, "seed": seed0 + k, "interpolate": a.smooth}
             for k in range(a.n)]
    defaults = {"width": w, "height": h, "length": length}
    if a.negative:
        defaults["negative"] = a.negative
    if a.quant:
        defaults["quant"] = a.quant
    _submit_and_wait({"name": a.name or a.prompt[:40], "preset": preset, "defaults": defaults, "items": items},
                     a.wait)


def cmd_run(a):
    try:
        job = json.load(open(a.job, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        C.fail(f"cannot read job file: {e}")
    _submit_and_wait(job, a.wait)


def cmd_wait(a):
    try:
        st = J.wait(a.job_id, a.wait)
    except FileNotFoundError as e:
        C.fail(str(e))
    report(st)


def cmd_review(a):
    from studiolib import review

    try:
        st = J.get_state(a.job_id)
    except FileNotFoundError as e:
        C.fail(str(e))
    if st["status"] not in ("done", "partial"):
        C.fail(f"job is {st['status']}; wait for it first")
    res = review.review_job(st, a.threshold)
    C.write_json(J.job_dir(a.job_id) / "review.json", res)
    keep = [r for r in res["results"] if r["verdict"] == "keep"]
    res["next"] = ("deliver the kept items" if keep else
                   "all below threshold: rerun the worst items with the prompt_fix and a new seed")
    C.jprint(res)


def cmd_rerun(a):
    try:
        st = J.get_state(a.job_id)
    except FileNotFoundError as e:
        C.fail(str(e))
    job = C.read_json(J.job_dir(a.job_id) / "job.json")
    if job.get("type") == "film":
        C.fail("for films use: studio.py film FILM.json --only SCENE_IDS")
    src = next((it for it in job["items"] if it["id"] == a.item), None)
    if not src:
        C.fail(f"no item {a.item} in {a.job_id}; items: {[it['id'] for it in job['items']]}")
    item = dict(src)
    item["id"] = f"{a.item}-r"
    if a.prompt:
        item["prompt"] = a.prompt
    item["seed"] = a.seed if a.seed is not None else src["seed"] + 1
    preset = a.preset or job["preset"]
    if P.PRESETS[preset]["kind"] != P.PRESETS[job["preset"]]["kind"]:
        C.fail("rerun must keep the same kind (image or video)")
    if preset != job["preset"]:  # let the new preset pick its own steps/cfg/sizes
        for k in ("steps", "cfg", "shift"):
            item.pop(k, None)
        if P.PRESETS[preset]["kind"] == "video":
            item.pop("fps", None)
            item.pop("length", None)
            if preset in VIDEO_SIZES:
                orient = "portrait" if src["height"] > src["width"] else "landscape"
                item["width"], item["height"] = VIDEO_SIZES[preset][orient]
    _submit_and_wait({"name": job["name"], "preset": preset, "items": [item]}, a.wait)


def cmd_film(a):
    from studiolib import film

    try:
        spec = json.load(open(a.spec, encoding="utf-8"))
        only = [s.strip() for s in a.only.split(",")] if a.only else None
        st = film.submit(spec, only)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        C.fail(str(e))
    st = J.wait(st["job_id"], a.wait)
    report(st)


def cmd_deliver(a):
    from studiolib import media

    lines = []
    for p in a.paths:
        path = Path(p)
        if not path.exists():
            C.fail(f"not found: {p}")
        lines.append(C.media_line(media.fit_for_telegram(path)))
    C.jprint({"deliver": lines, "next": "put each MEDIA line on its own line in your reply"})


def gpu_info():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        gpus = []
        for line in out.strip().splitlines():
            i, name, used, total, util = [x.strip() for x in line.split(",")]
            gpus.append({"gpu": int(i), "name": name, "mem_used_gb": round(int(used) / 1024, 1),
                         "mem_total_gb": round(int(total) / 1024, 1), "util_pct": int(util)})
        return gpus
    except Exception as e:
        return [{"error": str(e)}]


def cmd_status(a):
    from studiolib import comfy, weights

    boot = C.read_json(C.STATE / "session.json", {}) or {}
    started = boot.get("started")
    st = {
        "brain": {"model": C.BRAIN_MODEL, "ollama_up": C.alive(C.OLLAMA_URL + "/api/tags")},
        "comfyui_up": comfy.ready(),
        "gpus": gpu_info(),
        "running_jobs": [J.summary(s) for s in J.running_jobs()],
        "disk": weights.disk_report(),
        "outputs": str(C.OUT),
    }
    if started:
        st["session_hours_used"] = round((time.time() - started) / 3600, 2)
        st["session_mode"] = boot.get("mode")
    C.jprint(st)


def cmd_presets(a):
    C.jprint({k: {"kind": v["kind"], "about": v["about"], "defaults": v["defaults"],
                  "est_seconds_per_item": v["est_seconds"]} for k, v in P.PRESETS.items()})


def cmd_jobs(a):
    rows = []
    if C.JOBS.exists():
        for d in sorted((x for x in C.JOBS.iterdir() if x.is_dir()), reverse=True)[: a.limit]:
            st = C.read_json(d / "state.json")
            if st:
                rows.append({k: st.get(k) for k in ("job_id", "status", "preset", "done", "total", "message")})
    C.jprint(rows)


def cmd_disk(a):
    from studiolib import weights

    if a.free_gb:
        weights.evict(int(a.free_gb * 1024 ** 3), keep=set())
    C.jprint(weights.disk_report())


class JsonArgParser(argparse.ArgumentParser):
    """Usage errors come back as JSON too, so the agent can read and fix them."""

    def error(self, message):
        C.fail(f"{message}. Usage: {self.format_usage().strip()}")


def main(argv=None):
    ap = JsonArgParser(prog="studio.py", description=__doc__,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=JsonArgParser)

    s = sub.add_parser("image", help="render still images")
    s.add_argument("prompt")
    s.add_argument("--preset", choices=["klein4b", "zimage"])
    s.add_argument("--n", type=int, default=4)
    s.add_argument("--aspect", choices=list(IMAGE_SIZES), default="square")
    s.add_argument("--width", type=int)
    s.add_argument("--height", type=int)
    s.add_argument("--seed", type=int)
    s.add_argument("--name")
    s.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    s.set_defaults(fn=cmd_image)

    s = sub.add_parser("video", help="render video clips")
    s.add_argument("prompt")
    s.add_argument("--preset", choices=["wan14b", "wan5b", "wan14b_hq"])
    s.add_argument("--n", type=int, default=1)
    s.add_argument("--aspect", choices=["landscape", "portrait", "square"], default="landscape")
    s.add_argument("--seconds", type=float, default=5.0)
    s.add_argument("--width", type=int)
    s.add_argument("--height", type=int)
    s.add_argument("--seed", type=int)
    s.add_argument("--smooth", action="store_true", help="also make a 2x fps interpolated copy")
    s.add_argument("--negative")
    s.add_argument("--quant", choices=sorted(P.WAN14_QUANT_SIZES))
    s.add_argument("--name")
    s.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    s.set_defaults(fn=cmd_video)

    s = sub.add_parser("run", help="run a job JSON file")
    s.add_argument("job")
    s.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("wait", help="wait for a job (returns after --wait seconds even if not done)")
    s.add_argument("job_id")
    s.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    s.set_defaults(fn=cmd_wait)

    s = sub.add_parser("review", help="score a finished job with the vision model")
    s.add_argument("job_id")
    s.add_argument("--threshold", type=int, default=7)
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("rerun", help="re-render one item with changes")
    s.add_argument("job_id")
    s.add_argument("--item", required=True)
    s.add_argument("--prompt")
    s.add_argument("--seed", type=int)
    s.add_argument("--preset", choices=list(P.PRESETS))
    s.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    s.set_defaults(fn=cmd_rerun)

    s = sub.add_parser("film", help="voice, render and cut a narrated film")
    s.add_argument("spec")
    s.add_argument("--only", help="comma-separated scene ids to re-shoot")
    s.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    s.set_defaults(fn=cmd_film)

    s = sub.add_parser("deliver", help="print MEDIA: lines for files")
    s.add_argument("paths", nargs="+")
    s.set_defaults(fn=cmd_deliver)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("presets").set_defaults(fn=cmd_presets)
    s = sub.add_parser("jobs")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(fn=cmd_jobs)
    s = sub.add_parser("disk", help="show weights on disk; --free-gb N evicts least-recently-used weights")
    s.add_argument("--free-gb", type=float)
    s.set_defaults(fn=cmd_disk)

    s = sub.add_parser("_worker")
    s.add_argument("job_id")
    s.set_defaults(fn=lambda a: J.worker(a.job_id))
    s = sub.add_parser("_film_worker")
    s.add_argument("job_id")
    s.set_defaults(fn=lambda a: __import__("studiolib.film", fromlist=["worker"]).worker(a.job_id))

    a = ap.parse_args(argv)
    if getattr(a, "n", 1) is not None and hasattr(a, "n") and not 1 <= a.n <= J.MAX_ITEMS:
        C.fail(f"--n must be 1..{J.MAX_ITEMS}")
    a.fn(a)


if __name__ == "__main__":
    main()
