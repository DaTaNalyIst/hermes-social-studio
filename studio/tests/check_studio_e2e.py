"""End-to-end test of studio.py against fake ComfyUI/Ollama and a fake Chatterbox.

Runs every user-facing command the skill relies on, with real ffmpeg encoding,
contact sheets, interpolation, film cutting, subtitles and music ducking.
usage: python3 tests/test_end_to_end.py
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
STUDIO = ROOT / "studio" / "studio.py"
sys.path.insert(0, str(ROOT / "studio"))

tmp = Path(tempfile.mkdtemp(prefix="studio-e2e-"))
env = dict(os.environ)
env.update({
    "STUDIO_WORK": str(tmp / "work"),
    "STUDIO_BIG": str(tmp / "big"),
    "STUDIO_COMFY_DIR": str(tmp / "big" / "ComfyUI"),
    "STUDIO_HF_DIR": str(tmp / "big" / "hf"),
    "STUDIO_VENVS": str(tmp / "venvs"),
    "STUDIO_COMFY_URL": "http://127.0.0.1:18188",
    "STUDIO_OLLAMA_URL": "http://127.0.0.1:21434",
    "STUDIO_TELEGRAM_MAX_MB": "0.001",  # force the Telegram re-encode path
})
os.environ.update(env)

from studiolib import presets as P  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def studio(*args, timeout=600):
    p = subprocess.run([sys.executable, str(STUDIO), *map(str, args)], capture_output=True, text=True,
                       env=env, timeout=timeout)
    try:
        return json.loads(p.stdout), p
    except json.JSONDecodeError:
        print(p.stdout[-2000:], p.stderr[-2000:])
        raise


def probe(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate",
                        "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(p.stdout)


def make_sparse_weights():
    hf = Path(env["STUDIO_HF_DIR"])
    specs = dict(P.FILES)
    specs.update(P.wan14_files("Q4_K_M"))
    for spec in specs.values():
        f = hf / spec[0] / spec[1]
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "wb") as fh:
            fh.truncate(spec[3])  # sparse: right size, no disk use


def make_fake_chatterbox():
    venv = Path(env["STUDIO_VENVS"]) / "chatterbox" / "bin"
    venv.mkdir(parents=True)
    worker = ROOT / "studio" / "tests" / "fake_voice_worker.py"
    (venv / "python").write_text(f'#!/bin/bash\nif [ "$1" = "-c" ]; then exit 0; fi\n'
                                 f'shift\nexec {sys.executable} {worker} "$@"\n')
    (venv / "python").chmod(0o755)


def main():
    make_sparse_weights()
    make_fake_chatterbox()
    svc = subprocess.Popen([sys.executable, str(ROOT / "studio" / "tests" / "fake_services.py"), "18188", "21434"],
                           env=env, stdout=subprocess.PIPE, text=True)
    svc.stdout.readline()
    try:
        print("image job")
        r, _ = studio("image", "a red fox in fresh snow, golden hour", "--n", "2", "--aspect", "landscape",
                      "--wait", "60")
        check(r["status"] == "done", f"image job done ({r['status']}: {r.get('message')})")
        check(len(r.get("outputs", [])) == 2, "two image outputs")
        check(all(l.startswith("MEDIA:/") for l in r.get("deliver", [])), "deliver lines are MEDIA:/abs")
        check(all(Path(o["path"]).exists() for o in r["outputs"]), "image files exist")
        img_job = r["job_id"]

        print("review")
        rv, _ = studio("review", img_job)
        check(len(rv["results"]) == 2 and rv["results"][0]["verdict"] == "keep", "review scores and verdicts")

        print("video job with smooth copy")
        r, _ = studio("video", "a fox running through snow, tracking shot", "--seconds", "1", "--smooth",
                      "--wait", "120")
        check(r["status"] == "done", f"video job done ({r['status']}: {r.get('message')})")
        o = r["outputs"][0]
        info = probe(o["path"])
        check(abs(float(info["format"]["duration"]) - 17 / 16) < 0.15, "mp4 duration = 17 frames @16fps")
        check(Path(o["sheet"]).exists() and Path(o["smooth"]).exists(), "contact sheet and 32 fps copy exist")
        sm = probe(o["smooth"])
        check(sm["streams"][0]["r_frame_rate"] == "32/1", "interpolated copy is 32 fps")
        check("_tg.mp4" in r["deliver"][0], "oversized video gets a Telegram re-encode")
        vid_job = r["job_id"]

        print("video review uses the contact sheet")
        rv, _ = studio("review", vid_job)
        check(rv["results"][0]["overall"] == 6, "video reviewed via contact sheet")

        print("rerun with another preset")
        r, _ = studio("rerun", img_job, "--item", "v1", "--prompt", "a red fox, sharper", "--preset", "zimage",
                      "--wait", "60")
        check(r["status"] == "done" and r["preset"] == "zimage", "image rerun with zimage")
        r, _ = studio("rerun", vid_job, "--item", "v1", "--preset", "wan5b", "--wait", "120")
        check(r["status"] == "done", f"video rerun with wan5b ({r.get('message')})")

        print("wait returns early while running")
        r, _ = studio("video", "slow job", "--seconds", "1", "--wait", "0")
        check(r["status"] in ("queued", "downloading", "rendering", "finishing", "done"), "submit returns state")
        check("wait" in r["next"] or r["status"] == "done", "next tells Hermes to wait")
        r, _ = studio("wait", r["job_id"], "--wait", "120")
        check(r["status"] == "done", "wait finishes the job")

        print("failure is reported, not crashed")
        r, _ = studio("image", "FAILME please", "--n", "1", "--wait", "60")
        check(r["status"] == "failed" and "out of memory" in json.dumps(r).lower(), "render error surfaces")

        print("bad input")
        r, p = studio("video", "x", "--preset", "klein4b")
        check(p.returncode != 0 or r.get("ok") is False, "argparse/validation rejects wrong preset")

        print("film")
        music = tmp / "bed.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=4",
                        "-c:a", "libmp3lame", str(music)], check=True)
        spec = {"title": "Kenyan coffee test", "format": "short", "music": str(music),
                "voice": {"engine": "turbo"}, "subtitles": True,
                "scenes": [
                    {"id": "s01", "narration": "Coffee grows on the slopes of Mount Kenya.",
                     "visual": "coffee farm on a misty mountain slope", "type": "still"},
                    {"id": "s02", "narration": "Pickers choose only the ripest red cherries by hand.",
                     "visual": "hands picking red coffee cherries", "type": "video"},
                    {"id": "s03", "narration": "Kahawa tamu sana.", "lang": "sw",
                     "visual": "cup of coffee on a wooden table", "type": "still"}]}
        sp = tmp / "film.json"
        sp.write_text(json.dumps(spec))
        r, _ = studio("film", sp, "--wait", "300")
        check(r["status"] == "done", f"film done ({r.get('message')})")
        film_json = json.load(open(Path(r["folder"]) / "film.json"))
        info = probe(film_json["film"])
        kinds = sorted(s["codec_type"] for s in info["streams"])
        check(kinds == ["audio", "video"], "film has audio + video")
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        check((v["width"], v["height"]) == (1080, 1920), "short film is 1080x1920")
        check(abs(float(info["format"]["duration"]) - film_json["seconds"]) < 0.4,
              f"film length matches timeline ({info['format']['duration']} vs {film_json['seconds']})")
        check(Path(film_json["srt"]).read_text().count("-->") >= 3, "subtitles written")
        print("   subtitles burned:", film_json["subtitles_burned"])
        assets_before = json.load(open(Path(r["folder"]) / "assets.json"))

        print("film re-shoot of one scene")
        r, _ = studio("film", sp, "--only", "s02", "--wait", "300")
        check(r["status"] == "done", "re-shoot done")
        assets_after = json.load(open(Path(r["folder"]) / "assets.json"))
        check(assets_after["s01"]["still"] == assets_before["s01"]["still"], "untouched scene kept its still")
        check(assets_after["s02"]["clip"] != assets_before["s02"]["clip"], "re-shot scene got a new clip")
        log = (tmp / "voice-calls.log").read_text().splitlines()
        check(len(log) == 1, "voice was not regenerated for unchanged narration")

        print("deliver + status + jobs")
        r, _ = studio("deliver", film_json["film"])
        check(r["deliver"][0].startswith("MEDIA:/"), "deliver prints MEDIA line")
        r, _ = studio("status")
        check(r["comfyui_up"] and r["brain"]["ollama_up"], "status sees both services")
        r, _ = studio("jobs")
        check(len(r) >= 8, "jobs lists recent jobs")
    finally:
        svc.send_signal(signal.SIGTERM)
    print()
    print("RESULT:", "ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
    if not FAILS:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("kept", tmp)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
