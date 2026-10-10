"""ffmpeg / Pillow helpers: MP4 encoding, interpolation, contact sheets, previews."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

from . import common as C

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def ff(args, check=True):
    return C.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=check)


def probe_duration(path) -> float:
    p = C.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(p.stdout)["format"]["duration"])


def has_filter(name: str) -> bool:
    p = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True)
    return re.search(rf"\s{re.escape(name)}\s", p.stdout) is not None


def frames_to_mp4(frames: list, fps: float, out_path, crf: int = 17) -> Path:
    """Encode an ordered list of PNG frames (any names) into an H.264 MP4."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lst = out_path.with_suffix(".frames.txt")
    dur = 1.0 / fps
    with open(lst, "w") as f:
        for fr in frames:
            f.write(f"file '{Path(fr).resolve()}'\nduration {dur:.6f}\n")
        f.write(f"file '{Path(frames[-1]).resolve()}'\n")  # concat demuxer needs the last frame twice
    ff(["-f", "concat", "-safe", "0", "-i", lst, "-vf", f"fps={fps},format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", crf, "-movflags", "+faststart", out_path])
    lst.unlink(missing_ok=True)
    return out_path


def interpolate(src, out_path, factor: int = 2) -> Path:
    """Motion-interpolate to factor x fps (ffmpeg minterpolate). Can smear fast motion."""
    fps = fps_of(src) * factor
    ff(["-i", src, "-vf", f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1,format=yuv420p",
        "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-movflags", "+faststart", out_path])
    return Path(out_path)


def fps_of(path) -> float:
    p = C.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
               "-of", "json", str(path)])
    num, den = json.loads(p.stdout)["streams"][0]["r_frame_rate"].split("/")
    return float(num) / float(den)


def contact_sheet_from_frames(frames: list, out_path, n: int = 8, cols: int = 4, width: int = 1280) -> Path:
    """Tile n evenly spaced frames into one JPEG so a vision model can judge motion."""
    from PIL import Image, ImageDraw

    frames = list(frames)
    if not frames:
        raise ValueError("no frames")
    idx = sorted({round(i * (len(frames) - 1) / max(1, n - 1)) for i in range(n)})
    picks = [frames[i] for i in idx]
    first = Image.open(picks[0])
    tw = width // cols
    th = round(first.height * tw / first.width)
    rows = math.ceil(len(picks) / cols)
    sheet = Image.new("RGB", (tw * cols, th * rows), "black")
    draw = ImageDraw.Draw(sheet)
    for k, (i, fp) in enumerate(zip(idx, picks)):
        im = Image.open(fp).convert("RGB").resize((tw, th))
        x, y = (k % cols) * tw, (k // cols) * th
        sheet.paste(im, (x, y))
        draw.rectangle([x, y, x + 54, y + 18], fill="black")
        draw.text((x + 4, y + 3), f"f{i + 1}", fill="white")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=88)
    return Path(out_path)


def contact_sheet_from_video(video, out_path, n: int = 12, cols: int = 4, width: int = 1600) -> Path:
    tmp = Path(out_path).with_suffix("")
    tmp = tmp.parent / (tmp.name + "_frames")
    tmp.mkdir(parents=True, exist_ok=True)
    dur = probe_duration(video)
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        fp = tmp / f"{i:03d}.jpg"
        ff(["-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-q:v", "3", fp])
        if fp.exists():
            frames.append(fp)
    res = contact_sheet_from_frames(frames, out_path, n=len(frames), cols=cols, width=width)
    shutil.rmtree(tmp, ignore_errors=True)
    return res


def preview_jpeg(src, out_path, max_side: int = 1280) -> Path:
    from PIL import Image

    im = Image.open(src).convert("RGB")
    im.thumbnail((max_side, max_side))
    im.save(out_path, quality=90)
    return Path(out_path)


def fit_for_telegram(src, max_mb: float | None = None) -> Path:
    """Return src, or a smaller re-encode next to it if it exceeds the bot upload cap."""
    max_mb = max_mb or C.TELEGRAM_MAX_MB
    src = Path(src)
    if src.stat().st_size <= max_mb * 1024 * 1024 or src.suffix.lower() not in (".mp4", ".mov", ".mkv"):
        return src
    dur = max(1.0, probe_duration(src))
    kbps = int(max_mb * 8 * 1024 * 0.92 / dur) - 128
    out = src.with_name(src.stem + "_tg.mp4")
    ff(["-i", src, "-c:v", "libx264", "-b:v", f"{max(kbps, 300)}k", "-maxrate", f"{max(kbps, 300)}k",
        "-bufsize", f"{2 * max(kbps, 300)}k", "-vf", "scale='min(1280,iw)':-2", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", out])
    return out
