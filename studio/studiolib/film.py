"""Narrated films: voice every line, render a shot per scene, cut with ffmpeg.

Film spec (JSON):
{
  "title": "Kenyan coffee",
  "format": "short" | "long",          # short = 1080x1920 vertical, long = 1920x1080
  "quality": "draft" | "high",         # stills with klein4b (draft) or zimage (high)
  "voice": {"engine": "turbo" | "multilingual", "ref": "/kaggle/input/.../me.wav" | null,
            "exaggeration": 0.5},
  "music": "/kaggle/input/.../bed.mp3" | null,
  "subtitles": true,
  "scenes": [
    {"id": "s01", "narration": "...", "lang": "en", "visual": "image/video prompt",
     "type": "still" | "video", "seed": 1}
  ]
}
Every scene gets a still (cheap; also the fallback). "video" scenes also get a
Wan 2.2 clip. Re-shoot only some scenes with only=["s04", "s09"].
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import common as C
from . import jobs
from . import media

FPS = 30
FORMATS = {
    "short": {"W": 1080, "H": 1920, "still": (768, 1344), "clip": (480, 832), "lufs": -14},
    "long": {"W": 1920, "H": 1080, "still": (1344, 768), "clip": (832, 480), "lufs": -16},
}
LEAD_IN = 0.3
TAIL_PAD = 0.45
MAX_STRETCH = 1.6


# ---------------------------------------------------------------------------
# Spec handling
# ---------------------------------------------------------------------------
def validate(spec: dict) -> dict:
    spec = dict(spec)
    fmt = spec.get("format", "short")
    if fmt not in FORMATS:
        raise ValueError("format must be 'short' or 'long'")
    scenes = spec.get("scenes") or []
    if not scenes:
        raise ValueError("film has no scenes")
    if len(scenes) > 60:
        raise ValueError("at most 60 scenes per film; split long films into parts")
    ids = set()
    norm = []
    for i, s in enumerate(scenes):
        s = dict(s)
        s["id"] = jobs.slug(str(s.get("id") or f"s{i + 1:02d}"), 16)
        if s["id"] in ids:
            raise ValueError(f"duplicate scene id {s['id']}")
        ids.add(s["id"])
        if not str(s.get("narration", "")).strip():
            raise ValueError(f"scene {s['id']} has no narration")
        if not str(s.get("visual", "")).strip():
            raise ValueError(f"scene {s['id']} has no visual prompt")
        s["type"] = "video" if s.get("type") == "video" else "still"
        s["lang"] = (s.get("lang") or spec.get("language") or "en").lower()
        s["seed"] = int(s.get("seed", 100 + i))
        norm.append(s)
    for key in ("music",):
        if spec.get(key) and not Path(spec[key]).exists():
            raise ValueError(f"{key} file not found: {spec[key]}")
    v = spec.get("voice") or {}
    if v.get("ref") and not Path(v["ref"]).exists():
        raise ValueError(f"voice ref not found: {v['ref']}")
    spec["scenes"] = norm
    spec["format"] = fmt
    spec["title"] = spec.get("title") or norm[0]["narration"][:40]
    spec["name"] = jobs.slug(spec["title"])
    return spec


def _hash(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Subtitles
# ---------------------------------------------------------------------------
def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def caption_chunks(text: str, max_chars: int = 42, max_lines: int = 2):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return ["\n".join(lines[i:i + max_lines]) for i in range(0, len(lines), max_lines)]


def write_srt(timeline, path, max_chars=42):
    n, out = 1, []
    for sc in timeline:
        chunks = caption_chunks(sc["narration"], max_chars=max_chars)
        total_chars = sum(len(c) for c in chunks) or 1
        t = sc["start"] + sc["voice_offset"]
        for c in chunks:
            d = sc["voice_s"] * len(c) / total_chars
            out.append(f"{n}\n{_srt_time(t)} --> {_srt_time(t + d - 0.05)}\n{c}\n")
            n += 1
            t += d
    Path(path).write_text("\n".join(out), encoding="utf-8")
    return Path(path)


# ---------------------------------------------------------------------------
# Cutting (pure ffmpeg; tested locally by tests/selftest_film.py)
# ---------------------------------------------------------------------------
def _kenburns(still, seconds, W, H, idx, out):
    frames = max(1, int(round(seconds * FPS)))
    zoom_in = idx % 2 == 0
    z = "min(1+0.10*on/{n},1.10)".format(n=frames) if zoom_in else "max(1.10-0.10*on/{n},1.0)".format(n=frames)
    pans = ["iw/2-(iw/zoom/2)", "(iw-iw/zoom)*on/{n}", "(iw-iw/zoom)*(1-on/{n})"]
    x = pans[idx % 3].format(n=frames)
    y = "ih/2-(ih/zoom/2)"
    vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},setsar=1,format=yuv420p")
    media.ff(["-loop", "1", "-i", still, "-vf", vf, "-frames:v", frames, "-r", FPS,
              "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-an", out])
    return out


def _clip_fit(clip, seconds, W, H, out):
    dur = media.probe_duration(clip)
    factor = min(MAX_STRETCH, max(1.0, seconds / dur))
    vf = (f"setpts={factor:.4f}*PTS,fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H},setsar=1,format=yuv420p")
    use = min(seconds, dur * factor)
    media.ff(["-i", clip, "-vf", vf, "-t", f"{use:.3f}", "-r", FPS, "-c:v", "libx264", "-preset", "medium",
              "-crf", "18", "-an", out])
    return out, use


def _concat(parts, out):
    lst = Path(out).with_suffix(".txt")
    lst.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in parts))
    media.ff(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    lst.unlink(missing_ok=True)
    return out


def _fade(seg, seconds, out, fade=0.25):
    media.ff(["-i", seg, "-vf", f"fade=t=in:st=0:d={fade},fade=t=out:st={max(0, seconds - fade):.3f}:d={fade}",
              "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", FPS, "-an", out])
    return out


def cut(assets: list, fmt: str, out_dir, title: str, music=None, subtitles=True, log=print) -> dict:
    """assets: [{id, narration, voice_wav, voice_s, still, clip|None}] in order."""
    F = FORMATS[fmt]
    W, H = F["W"], F["H"]
    out_dir = Path(out_dir)
    work = out_dir / "_cut"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    timeline, segs, t = [], [], 0.0
    for i, a in enumerate(assets):
        offset = LEAD_IN if i == 0 else 0.15
        dur = round(offset + a["voice_s"] + TAIL_PAD, 3)
        parts = []
        if a.get("clip"):
            seg, used = _clip_fit(a["clip"], dur, W, H, work / f"{a['id']}_clip.mp4")
            parts.append(seg)
            if used < dur - 0.05:
                parts.append(_kenburns(a["still"], dur - used, W, H, i, work / f"{a['id']}_kb.mp4"))
        else:
            parts.append(_kenburns(a["still"], dur, W, H, i, work / f"{a['id']}_kb.mp4"))
        raw = parts[0] if len(parts) == 1 else _concat(parts, work / f"{a['id']}_raw.mp4")
        segs.append(_fade(raw, dur, work / f"{a['id']}_seg.mp4"))
        timeline.append({"id": a["id"], "start": t, "dur": dur, "voice_offset": offset, "voice_s": a["voice_s"],
                         "narration": a["narration"]})
        t += dur
    total = t
    log(f"cut {len(segs)} scenes, {total:.1f}s")
    video = _concat(segs, work / "video.mp4")

    # Narration track: each line placed at its scene start + offset.
    inputs, filters = [], []
    for k, (a, tl) in enumerate(zip(assets, timeline)):
        inputs += ["-i", a["voice_wav"]]
        ms = int(round((tl["start"] + tl["voice_offset"]) * 1000))
        filters.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,adelay={ms}:all=1[v{k}]")
    mix = "".join(f"[v{k}]" for k in range(len(assets)))
    filters.append(f"{mix}amix=inputs={len(assets)}:normalize=0,apad,atrim=0:{total:.3f}[narr]")
    narr = work / "narration.wav"
    media.ff([*inputs, "-filter_complex", ";".join(filters), "-map", "[narr]", "-ac", "1", "-ar", "48000", narr])

    final_audio = work / "audio.wav"
    lufs = F["lufs"]
    if music:
        fc = (f"[1:a]aresample=48000,volume=0.35,atrim=0:{total:.3f},asetpts=PTS-STARTPTS[bed];"
              f"[0:a]asplit=2[n1][n2];"
              f"[bed][n1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck];"
              f"[duck][n2]amix=inputs=2:normalize=0,afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5,"
              f"loudnorm=I={lufs}:TP=-1.5:LRA=11[a]")
        media.ff(["-i", narr, "-stream_loop", "-1", "-i", music, "-filter_complex", fc, "-map", "[a]",
                  "-t", f"{total:.3f}", "-ar", "48000", "-ac", "2", final_audio])
    else:
        media.ff(["-i", narr, "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "2",
                  final_audio])

    # vertical frames are narrow: shorter caption lines
    srt = write_srt(timeline, out_dir / f"{jobs_slug(title)}.srt", max_chars=26 if fmt == "short" else 42)
    final = out_dir / f"{jobs_slug(title)}.mp4"
    burned = False
    if subtitles and media.has_filter("subtitles"):
        # libass scales SRT styles from a 288-px-high script, so these are ~6.7x (short) / 3.75x (long)
        margin = 60 if fmt == "short" else 18
        size = 9 if fmt == "short" else 14
        style = (f"FontName=DejaVu Sans,FontSize={size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                 f"BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV={margin}")
        srt_esc = str(srt.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        p = media.ff(["-i", video, "-i", final_audio, "-vf", f"subtitles='{srt_esc}':force_style='{style}'",
                      "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                      "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", final], check=False)
        burned = p.returncode == 0
    if not burned:
        media.ff(["-i", video, "-i", final_audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                  "-b:a", "192k", "-shortest", "-movflags", "+faststart", final])
    thumb = out_dir / f"{jobs_slug(title)}_thumb.jpg"
    media.ff(["-ss", f"{min(1.0, total / 2):.2f}", "-i", final, "-frames:v", "1", "-q:v", "2", thumb])
    sheet = media.contact_sheet_from_video(final, out_dir / f"{jobs_slug(title)}_sheet.jpg",
                                           n=min(16, max(4, len(assets))))
    tg = media.fit_for_telegram(final)
    shutil.rmtree(work, ignore_errors=True)
    return {"film": str(final), "telegram": str(tg), "srt": str(srt), "thumb": str(thumb), "sheet": str(sheet),
            "seconds": round(total, 2), "subtitles_burned": burned, "timeline": timeline}


def jobs_slug(s):
    return jobs.slug(s, 48)


# ---------------------------------------------------------------------------
# Job wrapper
# ---------------------------------------------------------------------------
def submit(spec_raw: dict, only=None) -> dict:
    C.ensure_dirs()
    spec = validate(spec_raw)
    job_id = jobs.new_job_id("film-" + spec["name"])
    d = jobs.job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    C.write_json(d / "job.json", {"type": "film", "spec": spec, "only": only or []})
    n_video = sum(1 for s in spec["scenes"] if s["type"] == "video")
    est = 240 + 25 * len(spec["scenes"]) + 840 * n_video
    jobs.set_state(job_id, job_id=job_id, status="queued", preset="film", name=spec["name"],
                   total=len(spec["scenes"]), done=0, created=time.time(), estimate_s=est,
                   message="waiting for the GPU", outputs=[], errors=[])
    log = open(d / "worker.log", "a")
    subprocess.Popen([sys.executable, str(C.CODE / "studio.py"), "_film_worker", job_id], stdout=log,
                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                     cwd=str(C.CODE))
    return jobs.get_state(job_id)


def worker(job_id: str):
    d = jobs.job_dir(job_id)
    cfg = C.read_json(d / "job.json")
    spec, only = cfg["spec"], set(cfg.get("only") or [])
    film_dir = C.OUT / "films" / spec["name"]
    assets_dir = film_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    index = C.read_json(film_dir / "assets.json", {}) or {}

    def log(msg):
        print(msg, flush=True)
        jobs.set_state(job_id, message=msg)

    lock = jobs.gpu_lock()
    t0 = time.time()
    try:
        jobs.set_state(job_id, status="rendering", started=t0)
        F = FORMATS[spec["format"]]
        voice = spec.get("voice") or {}
        # 1. Voice (re-voice only lines whose text/voice changed)
        todo = []
        for s in spec["scenes"]:
            key = _hash(s["narration"], s["lang"], voice.get("engine"), voice.get("ref"), voice.get("exaggeration"))
            rec = index.get(s["id"], {})
            if rec.get("voice_key") != key or not Path(rec.get("voice_wav", "")).exists():
                todo.append({"id": s["id"], "text": s["narration"], "lang": s["lang"], "key": key})
        if todo:
            log(f"voicing {len(todo)} line(s) with Chatterbox")
            vspec = {"engine": voice.get("engine", "turbo"), "voice_ref": voice.get("ref"),
                     "exaggeration": voice.get("exaggeration", 0.5), "lines": todo}
            C.write_json(assets_dir / "voice_spec.json", vspec)
            from . import voice as V
            man = V.synthesize(assets_dir / "voice_spec.json", assets_dir / "voice", log=log)
            by_id = {r["id"]: r for r in man["lines"]}
            for t in todo:
                r = by_id[t["id"]]
                index.setdefault(t["id"], {}).update(voice_wav=r["path"], voice_s=r["seconds"], voice_key=t["key"])
            C.write_json(film_dir / "assets.json", index)

        # 2. Stills for every scene (new or changed or explicitly re-shot)
        still_preset = "zimage" if spec.get("quality") == "high" else "klein4b"
        sw, sh = F["still"]
        stills = []
        for s in spec["scenes"]:
            key = _hash(s["visual"], s["seed"], still_preset, sw, sh)
            rec = index.get(s["id"], {})
            if s["id"] in only or rec.get("still_key") != key or not Path(rec.get("still", "")).exists():
                seed = s["seed"] + (1000 if s["id"] in only and rec.get("still_key") == key else 0)
                stills.append({"id": s["id"], "prompt": s["visual"], "seed": seed, "width": sw, "height": sh,
                               "_key": key})
        if stills:
            log(f"rendering {len(stills)} still(s) with {still_preset}")
            from . import presets as P
            items = [P.normalise_item(still_preset, {k: v for k, v in it.items() if k != "_key"}) for it in stills]
            outs, errs = jobs.render_items(job_id, still_preset, items, assets_dir / "stills", log=log)
            keys = {it["id"]: it["_key"] for it in stills}
            for o in outs:
                index.setdefault(o["item"], {}).update(still=o["path"], still_key=keys[o["item"]])
            if errs:
                jobs.set_state(job_id, errors=errs)
            C.write_json(film_dir / "assets.json", index)

        # 3. Video clips for video scenes
        cw, ch = F["clip"]
        clips = []
        for s in spec["scenes"]:
            if s["type"] != "video":
                continue
            key = _hash(s["visual"], s["seed"], cw, ch)
            rec = index.get(s["id"], {})
            if s["id"] in only or rec.get("clip_key") != key or not Path(rec.get("clip", "")).exists():
                seed = s["seed"] + (1000 if s["id"] in only and rec.get("clip_key") == key else 0)
                clips.append({"id": s["id"], "prompt": s["visual"], "seed": seed, "width": cw, "height": ch,
                              "_key": key})
        if clips:
            log(f"rendering {len(clips)} video clip(s) with wan14b (~{len(clips) * 14} min)")
            from . import presets as P
            items = [P.normalise_item("wan14b", {k: v for k, v in it.items() if k != "_key"}) for it in clips]
            outs, errs = jobs.render_items(job_id, "wan14b", items, assets_dir / "clips", log=log)
            keys = {it["id"]: it["_key"] for it in clips}
            for o in outs:
                index.setdefault(o["item"], {}).update(clip=o["path"], clip_key=keys[o["item"]])
            if errs:
                jobs.set_state(job_id, errors=errs)
            C.write_json(film_dir / "assets.json", index)

        # 4. Cut
        assets = []
        missing = []
        for s in spec["scenes"]:
            rec = index.get(s["id"], {})
            if not rec.get("still") or not rec.get("voice_wav"):
                missing.append(s["id"])
                continue
            assets.append({"id": s["id"], "narration": s["narration"], "voice_wav": rec["voice_wav"],
                           "voice_s": rec["voice_s"], "still": rec["still"],
                           "clip": rec.get("clip") if s["type"] == "video" else None})
        if not assets:
            raise RuntimeError(f"nothing to cut; missing assets for {missing}")
        log("cutting the film")
        res = cut(assets, spec["format"], film_dir, spec["title"], spec.get("music"), spec.get("subtitles", True),
                  log=log)
        res["missing_scenes"] = missing
        res["scene_assets"] = {k: {kk: v for kk, v in rec.items() if not kk.endswith("_key")}
                               for k, rec in index.items()}
        C.write_json(film_dir / "film.json", res)
        jobs.set_state(job_id, status="done" if not missing else "partial", finished=time.time(),
                       folder=str(film_dir), done=len(assets),
                       outputs=[{"item": "film", "type": "film", "path": res["film"], "seed": 0,
                                 "sheet": res["sheet"], "poster": res["thumb"], "telegram": res["telegram"],
                                 "seconds": res["seconds"]}],
                       message=f"film {res['seconds']:.0f}s ready in {time.time() - t0:.0f}s"
                               + (f"; missing scenes {missing}" if missing else ""))
    except Exception as e:
        print(traceback.format_exc(), flush=True)
        jobs.set_state(job_id, status="failed", finished=time.time(), message=str(e)[:800])
    finally:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_UN)
