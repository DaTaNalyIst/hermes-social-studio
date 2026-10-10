"""Render jobs: validate, run in a detached worker, track state, post-process.

A job file looks like:
{
  "name": "fox-poster",            # folder name under outputs/ (optional)
  "preset": "klein4b",
  "defaults": {"width": 1024, "height": 1024},
  "items": [{"id": "a", "prompt": "...", "seed": 1}, ...]
}
Video items may add "interpolate": true for a second, 2x-fps MP4.
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import comfy
from . import common as C
from . import media
from . import presets as P
from . import weights

MAX_ITEMS = 24


def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return (s[:n].strip("-") or "job")


def validate(job: dict) -> dict:
    if not isinstance(job, dict):
        raise ValueError("job must be a JSON object")
    preset = job.get("preset")
    if preset not in P.PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose one of {', '.join(P.PRESETS)}")
    items = job.get("items") or []
    if not items:
        raise ValueError("job has no items")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"at most {MAX_ITEMS} items per job")
    defaults = job.get("defaults") or {}
    norm, seen = [], set()
    for i, it in enumerate(items):
        it = dict(it)
        it.setdefault("id", f"item{i + 1}")
        it["id"] = slug(str(it["id"]), 32)
        if it["id"] in seen:
            it["id"] = f"{it['id']}-{i + 1}"
        seen.add(it["id"])
        norm.append(P.normalise_item(preset, it, defaults))
    first = norm[0].get("prompt", "")
    name = slug(job.get("name") or first[:40])
    return {"name": name, "preset": preset, "defaults": defaults, "items": norm,
            "quant": defaults.get("quant") or P.PRESETS[preset]["defaults"].get("quant")}


def new_job_id(name: str) -> str:
    return time.strftime("%m%d-%H%M%S") + "-" + name[:24]


def job_dir(job_id: str) -> Path:
    return C.JOBS / job_id


def state_path(job_id: str) -> Path:
    return job_dir(job_id) / "state.json"


def get_state(job_id: str) -> dict:
    st = C.read_json(state_path(job_id))
    if st is None:
        raise FileNotFoundError(f"no job {job_id}")
    return st


def set_state(_jid: str, **kw):
    st = C.read_json(state_path(_jid), {}) or {}
    st.update(kw)
    st["updated"] = time.time()
    C.write_json(state_path(_jid), st)
    return st


def estimate_seconds(job: dict) -> int:
    p = P.PRESETS[job["preset"]]
    per = p["est_seconds"]
    if p["kind"] == "video":
        it = job["items"][0]
        per = per * (it["width"] * it["height"] * it["length"]) / (832 * 480 * 81)
    return int(per * len(job["items"]))


def submit(job_raw: dict) -> dict:
    """Create the job folder and launch a detached worker. Returns the initial state."""
    C.ensure_dirs()
    job = validate(job_raw)
    job_id = new_job_id(job["name"])
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    C.write_json(d / "job.json", job)
    set_state(job_id, job_id=job_id, status="queued", preset=job["preset"], name=job["name"],
              total=len(job["items"]), done=0, created=time.time(), message="waiting for the GPU",
              estimate_s=estimate_seconds(job), outputs=[], errors=[])
    log = open(d / "worker.log", "a")
    subprocess.Popen([sys.executable, str(C.CODE / "studio.py"), "_worker", job_id],
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     start_new_session=True, cwd=str(C.CODE))
    return get_state(job_id)


def running_jobs() -> list:
    out = []
    if not C.JOBS.exists():
        return out
    for d in sorted(x for x in C.JOBS.iterdir() if x.is_dir()):
        st = C.read_json(d / "state.json")
        if st and st.get("status") in ("queued", "downloading", "rendering", "finishing"):
            out.append(st)
    return out


def _postprocess_image(job, it, files, dest: Path) -> list:
    outs = []
    for k, f in enumerate(files):
        suffix = f"_{k}" if len(files) > 1 else ""
        target = dest / f"{it['id']}_s{it['seed']}{suffix}.png"
        shutil.copy2(f, target)
        outs.append({"item": it["id"], "type": "image", "path": str(target), "seed": it["seed"],
                     "prompt": it["prompt"], "width": it["width"], "height": it["height"]})
    return outs


def _postprocess_video(job, it, files, dest: Path) -> list:
    base = dest / f"{it['id']}_s{it['seed']}"
    mp4 = media.frames_to_mp4(files, it["fps"], base.with_suffix(".mp4"))
    sheet = media.contact_sheet_from_frames(files, str(base) + "_sheet.jpg")
    rec = {"item": it["id"], "type": "video", "path": str(mp4), "sheet": str(sheet), "seed": it["seed"],
           "prompt": it["prompt"], "fps": it["fps"], "frames": len(files),
           "seconds": round(len(files) / it["fps"], 2), "width": it["width"], "height": it["height"]}
    if it.get("interpolate"):
        smooth = media.interpolate(mp4, str(base) + f"_{int(it['fps'] * 2)}fps.mp4")
        rec["smooth"] = str(smooth)
    poster = media.preview_jpeg(files[len(files) // 2], str(base) + "_poster.jpg")
    rec["poster"] = str(poster)
    return [rec]


def gpu_lock():
    """Exclusive lock so only one job/film renders at a time; others wait in line."""
    C.JOBS.mkdir(parents=True, exist_ok=True)
    lock = open(C.JOBS / ".gpu.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    return lock


def render_items(job_id: str, preset: str, items: list, dest: Path, quant=None, log=print, progress=None,
                 on_item=None):
    """Render items one by one on ComfyUI and post-process them into dest.
    Returns (outputs, errors). Caller must hold the GPU lock."""
    weights.ensure_preset(preset, quant, log=log)
    comfy.wait_ready(900)
    # Switching model families: drop the previous models from VRAM and RAM first. ComfyUI would
    # otherwise keep them cached in the 29 GB of system RAM, and Wan + Z-Image together do not fit.
    last = C.read_json(C.JOBS / ".last_preset.json", {}) or {}
    if last.get("preset") and last.get("preset") != preset:
        comfy.free_vram()
        time.sleep(3)
    C.write_json(C.JOBS / ".last_preset.json", {"preset": preset})
    dest.mkdir(parents=True, exist_ok=True)
    kind = P.PRESETS[preset]["kind"]
    outputs, errors = [], []
    for n, it in enumerate(items):
        prefix = f"{job_id}/{it['id']}/f"
        wf = P.build(preset, it, prefix)
        if progress:
            progress(n, it)
        item_t0 = time.time()
        try:
            pid = comfy.submit(wf)
            h = comfy.wait(pid, timeout=4 * 3600)
            files = comfy.output_files(h)
            if not files:
                raise RuntimeError("ComfyUI returned no images")
            log(f"encoding {it['id']}")
            recs = (_postprocess_video if kind == "video" else _postprocess_image)(None, it, files, dest)
            for r in recs:
                r["render_s"] = round(time.time() - item_t0, 1)
            outputs.extend(recs)
            if kind == "video":
                shutil.rmtree(C.COMFY_DIR / "output" / job_id / it["id"], ignore_errors=True)
        except Exception as e:  # keep going with the other items
            errors.append({"item": it["id"], "error": str(e)[:800]})
            print(traceback.format_exc(), flush=True)
        if on_item:
            on_item(n + 1, outputs, errors)
    return outputs, errors


def worker(job_id: str):
    """Runs detached, started by submit()."""
    d = job_dir(job_id)
    job = C.read_json(d / "job.json")
    set_state(job_id, status="queued", message="waiting for the GPU (another job is rendering)")
    lock = gpu_lock()
    t0 = time.time()
    try:
        set_state(job_id, status="downloading", message="checking model weights", started=t0)

        def log(msg):
            print(msg, flush=True)
            set_state(job_id, message=msg)

        def progress(n, it):
            set_state(job_id, status="rendering", current=it["id"],
                      message=f"rendering {it['id']} ({n + 1}/{len(job['items'])})")

        def on_item(done, outputs, errors):
            set_state(job_id, done=done, outputs=list(outputs), errors=list(errors))

        dest = C.OUT / job["name"]
        outputs, errors = render_items(job_id, job["preset"], job["items"], dest, job.get("quant"), log,
                                       progress, on_item)
        set_state(job_id, done=len(job["items"]), outputs=outputs, errors=errors)
        manifest = {"job_id": job_id, "name": job["name"], "preset": job["preset"], "folder": str(dest),
                    "outputs": outputs, "errors": errors, "seconds": round(time.time() - t0, 1)}
        C.write_json(dest / f"manifest_{job_id}.json", manifest)
        status = "done" if outputs and not errors else ("partial" if outputs else "failed")
        set_state(job_id, status=status, finished=time.time(), folder=str(dest),
                  message=f"{len(outputs)} output(s), {len(errors)} error(s) in {time.time() - t0:.0f}s")
    except Exception as e:
        print(traceback.format_exc(), flush=True)
        set_state(job_id, status="failed", finished=time.time(), message=str(e)[:800])
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)


def summary(st: dict) -> dict:
    """Short, model-friendly view of a job state."""
    s = {k: st.get(k) for k in ("job_id", "status", "preset", "done", "total", "message", "folder")}
    elapsed = (st.get("finished") or time.time()) - (st.get("started") or st.get("created") or time.time())
    s["elapsed_s"] = int(elapsed)
    if st.get("status") in ("queued", "downloading", "rendering", "finishing"):
        s["estimate_total_s"] = st.get("estimate_s")
    if st.get("outputs"):
        s["outputs"] = [{k: o[k] for k in ("item", "type", "path", "seed", "sheet", "smooth", "poster", "telegram", "seconds")
                         if k in o}
                        for o in st["outputs"]]
    if st.get("errors"):
        s["errors"] = st["errors"]
    return s


def wait(job_id: str, max_wait: float) -> dict:
    t0 = time.time()
    while True:
        st = get_state(job_id)
        if st["status"] in ("done", "partial", "failed"):
            return st
        if time.time() - t0 >= max_wait:
            return st
        time.sleep(3)
