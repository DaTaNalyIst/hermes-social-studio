"""Platform-ready media variants, made once at `social add` time with ffmpeg/Pillow.

Every (platform, format) maps to a variant recipe. Identical recipes are shared, so one
1080x1920 MP4 can serve Facebook Reels, Instagram Reels, YouTube Shorts and Bluesky.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


class MediaError(ValueError):
    pass


# ---------------------------------------------------------------------------
# What each target accepts
# ---------------------------------------------------------------------------
# kind: "image" | "video" | "images" (1..max) | "text" | "image_or_video"
RULES = {
    ("facebook", "post"): {"kind": "text"},
    ("facebook", "photo"): {"kind": "image", "img": "jpeg_2048"},
    ("facebook", "album"): {"kind": "images", "min": 2, "max": 10, "img": "jpeg_2048"},
    ("facebook", "video"): {"kind": "video", "vid": "mp4_any", "max_s": 14400},
    ("facebook", "reel"): {"kind": "video", "vid": "mp4_vertical", "min_s": 3, "max_s": 90},
    ("facebook", "story"): {"kind": "image_or_video", "img": "jpeg_story", "vid": "mp4_vertical",
                            "min_s": 3, "max_s": 60},
    ("instagram", "image"): {"kind": "image", "img": "jpeg_ig"},
    ("instagram", "carousel"): {"kind": "images", "min": 2, "max": 10, "img": "jpeg_ig_square"},
    ("instagram", "reel"): {"kind": "video", "vid": "mp4_vertical", "min_s": 3, "max_s": 900, "max_mb": 300},
    ("instagram", "story"): {"kind": "image_or_video", "img": "jpeg_story", "vid": "mp4_vertical",
                             "min_s": 3, "max_s": 60},
    ("threads", "text"): {"kind": "text"},
    ("threads", "image"): {"kind": "image", "img": "jpeg_1440"},
    ("threads", "carousel"): {"kind": "images", "min": 2, "max": 20, "img": "jpeg_1440"},
    ("threads", "video"): {"kind": "video", "vid": "mp4_any", "max_s": 300, "max_mb": 1000},
    ("youtube", "short"): {"kind": "video", "vid": "mp4_vertical", "min_s": 1, "max_s": 180},
    ("youtube", "video"): {"kind": "video", "vid": "mp4_any", "max_s": 43200},
    ("bluesky", "text"): {"kind": "text"},
    ("bluesky", "images"): {"kind": "images", "min": 1, "max": 4, "img": "jpeg_bsky"},
    ("bluesky", "video"): {"kind": "video", "vid": "mp4_bsky", "max_s": 180, "max_mb": 100},
    ("telegram", "text"): {"kind": "text"},
    ("telegram", "photo"): {"kind": "image", "img": "jpeg_1440"},
    ("telegram", "album"): {"kind": "images", "min": 2, "max": 10, "img": "jpeg_1440"},
    ("telegram", "video"): {"kind": "video", "vid": "mp4_tg", "max_s": 7200},
}


@dataclass
class Info:
    kind: str
    width: int
    height: int
    duration: float = 0.0
    audio: bool = False


def kind_of(path: Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    raise MediaError(f"unsupported media type: {path}")


def probe(path: Path) -> Info:
    path = Path(path)
    if not path.exists():
        raise MediaError(f"file not found: {path}")
    k = kind_of(path)
    if k == "image":
        from PIL import Image
        with Image.open(path) as im:
            return Info("image", im.width, im.height)
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    if p.returncode != 0:
        raise MediaError(f"ffprobe could not read {path}: {p.stderr[-300:]}")
    d = json.loads(p.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise MediaError(f"{path} has no video stream")
    audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    return Info("video", int(v["width"]), int(v["height"]), float(d["format"].get("duration", 0)), audio)


def check_target(platform: str, fmt: str, media: list[Path]) -> dict:
    """Validate the source media for a target; returns the rule. Raises MediaError with a fix hint."""
    rule = RULES.get((platform, fmt))
    if not rule:
        raise MediaError(f"{platform} has no format {fmt!r}")
    k = rule["kind"]
    infos = [probe(m) for m in media]
    if k == "text":
        return rule
    if k in ("image", "video", "image_or_video") and len(infos) != 1:
        raise MediaError(f"{platform}:{fmt} takes exactly one media file (got {len(infos)})")
    if k == "images":
        if not rule["min"] <= len(infos) <= rule["max"]:
            raise MediaError(f"{platform}:{fmt} takes {rule['min']}-{rule['max']} images (got {len(infos)})")
        if any(i.kind != "image" for i in infos):
            raise MediaError(f"{platform}:{fmt} takes images only")
    if k == "image" and infos[0].kind != "image":
        raise MediaError(f"{platform}:{fmt} needs an image")
    if k == "video" and infos[0].kind != "video":
        raise MediaError(f"{platform}:{fmt} needs a video")
    for i in infos:
        if i.kind == "video":
            if i.duration < rule.get("min_s", 0) - 0.05:
                raise MediaError(f"{platform}:{fmt} videos must be at least {rule['min_s']} s (is {i.duration:.1f} s)")
            if i.duration > rule.get("max_s", 1e9) + 0.05:
                raise MediaError(f"{platform}:{fmt} videos can be at most {rule['max_s']} s (is {i.duration:.1f} s). "
                                 f"Use a shorter cut or another format.")
    if platform == "youtube" and fmt == "short" and infos[0].width > infos[0].height * 1.05:
        pass  # landscape sources are padded to vertical by the mp4_vertical recipe
    return rule


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------
def _ff(args):
    p = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise MediaError(f"ffmpeg failed: {p.stderr[-800:]}")


def _jpeg(im, out: Path, max_bytes: int | None = None, quality: int = 90):
    im = im.convert("RGB")
    q = quality
    while True:
        im.save(out, "JPEG", quality=q, optimize=True, progressive=True)
        if not max_bytes or out.stat().st_size <= max_bytes or q <= 40:
            break
        q -= 8
    if max_bytes and out.stat().st_size > max_bytes:
        im.thumbnail((int(im.width * 0.8), int(im.height * 0.8)))
        _jpeg(im, out, max_bytes, quality)


def _fit(im, max_side):
    im = im.copy()
    im.thumbnail((max_side, max_side))
    return im


def _clamp_aspect(im, lo: float, hi: float):
    """Center-crop so width/height lies in [lo, hi]."""
    r = im.width / im.height
    if r < lo:  # too tall
        h = int(im.width / lo)
        top = (im.height - h) // 2
        return im.crop((0, top, im.width, top + h))
    if r > hi:  # too wide
        w = int(im.height * hi)
        left = (im.width - w) // 2
        return im.crop((left, 0, left + w, im.height))
    return im


def _story_canvas(im, W=1080, H=1920):
    from PIL import ImageFilter
    bg = im.copy().convert("RGB")
    s = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * s) + 1, int(bg.height * s) + 1))
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(28))
    fg = im.copy().convert("RGB")
    f = min(W / fg.width, H / fg.height)
    fg = fg.resize((int(fg.width * f), int(fg.height * f)))
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    return bg


def make_image(src: Path, recipe: str, out: Path) -> Path:
    from PIL import Image, ImageOps
    with Image.open(src) as im0:
        im = ImageOps.exif_transpose(im0).convert("RGB")
    if recipe == "jpeg_2048":
        _jpeg(_fit(im, 2048), out, 8_000_000)
    elif recipe == "jpeg_1440":
        _jpeg(_fit(im, 1440), out, 8_000_000)
    elif recipe == "jpeg_ig":
        im = _clamp_aspect(im, 0.8, 1.91)
        w = min(1440, max(320, im.width))
        im = im.resize((w, int(im.height * w / im.width)))
        _jpeg(im, out, 8_000_000)
    elif recipe == "jpeg_ig_square":  # carousel items all share the first item's crop; square is safest
        side = min(im.width, im.height)
        im = im.crop(((im.width - side) // 2, (im.height - side) // 2,
                      (im.width - side) // 2 + side, (im.height - side) // 2 + side)).resize((1080, 1080))
        _jpeg(im, out, 8_000_000)
    elif recipe == "jpeg_story":
        _jpeg(_story_canvas(im), out, 8_000_000)
    elif recipe == "jpeg_bsky":
        _jpeg(_fit(im, 2000), out, 950_000)
    else:
        raise MediaError(f"unknown image recipe {recipe}")
    return out


AUDIO = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
H264 = ["-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p", "-preset", "medium",
        "-movflags", "+faststart"]


def make_video(src: Path, recipe: str, out: Path, info: Info | None = None) -> Path:
    info = info or probe(src)
    audio_in = [] if info.audio else ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    amap = ["-map", "0:a:0"] if info.audio else ["-map", "1:a:0", "-shortest"]
    if recipe == "mp4_vertical":
        fc = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2[bg];"
              "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p,setsar=1[v]")
        _ff(["-i", src, *audio_in, "-filter_complex", fc, "-map", "[v]", *amap, *H264, "-crf", "20",
             "-maxrate", "8M", "-bufsize", "16M", *AUDIO, out])
    elif recipe in ("mp4_any", "mp4_bsky", "mp4_tg"):
        # 30 fps always: Wan clips are 16 fps and Threads/Instagram want 23-60 fps.
        vf = "scale='trunc(min(1920,iw)/2)*2':-2,fps=30,format=yuv420p,setsar=1"
        rate = ["-crf", "20", "-maxrate", "10M", "-bufsize", "20M"]
        cap_mb = {"mp4_bsky": 95, "mp4_tg": 48}.get(recipe)
        if cap_mb and info.duration > 0:
            kbps = int(cap_mb * 8192 * 0.93 / info.duration) - 128
            kbps = max(300, min(kbps, 8000))
            rate = ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k"]
        _ff(["-i", src, *audio_in, "-map", "0:v:0", *amap, "-vf", vf, *H264, *rate, *AUDIO, out])
    else:
        raise MediaError(f"unknown video recipe {recipe}")
    return out


def recipe_for(rule: dict, src_kind: str) -> str | None:
    if rule["kind"] == "text":
        return None
    return rule["vid"] if src_kind == "video" else rule["img"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class VariantMaker:
    """Builds and de-duplicates variants for one post in a work folder."""

    def __init__(self, post_id: str, work: Path):
        self.post_id, self.work = post_id, Path(work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.cache: dict[tuple, dict] = {}

    def variant(self, src: Path, recipe: str) -> dict:
        key = (str(Path(src).resolve()), recipe)
        if key in self.cache:
            return self.cache[key]
        info = probe(src)
        n = len(self.cache) + 1
        ext = ".mp4" if recipe.startswith("mp4") else ".jpg"
        out = self.work / f"{self.post_id}__{recipe}_{n}{ext}"
        if ext == ".mp4":
            make_video(src, recipe, out, info)
        else:
            make_image(src, recipe, out)
        oi = probe(out)
        rec = {"name": out.name, "path": str(out), "kind": oi.kind, "width": oi.width, "height": oi.height,
               "duration": round(oi.duration, 3), "size": out.stat().st_size, "sha256": sha256(out)}
        self.cache[key] = rec
        return rec
