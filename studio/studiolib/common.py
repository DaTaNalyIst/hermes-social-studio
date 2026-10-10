"""Paths, settings and small helpers shared by every studio command.

All locations can be overridden with environment variables so the same code
runs on Kaggle and in the local self-test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ON_KAGGLE = os.path.isdir("/kaggle/working")


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


# Choices made once by setup_studio.py (scratch dir, brain model) so every later
# process - Hermes' terminal tool, detached workers - agrees on them.
PINNED_FILE = Path(os.environ.get("STUDIO_PINNED", str(Path.home() / ".studio.json")))


def _pinned() -> dict:
    try:
        with open(PINNED_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def pick_big_dir() -> Path:
    """Largest writable scratch volume for model weights (not /kaggle/working,
    which is capped and saved with the notebook version)."""
    if os.environ.get("STUDIO_BIG"):
        return Path(os.environ["STUDIO_BIG"])
    if _pinned().get("big"):
        return Path(_pinned()["big"])
    best, best_free = None, -1
    for cand in ("/kaggle/tmp", "/tmp", "/root"):
        try:
            if cand == "/kaggle/tmp" and os.path.isdir("/kaggle"):
                os.makedirs(cand, exist_ok=True)
            if not os.path.isdir(cand) or not os.access(cand, os.W_OK):
                continue
            free = shutil.disk_usage(cand).free
        except OSError:
            continue
        if free > best_free:
            best, best_free = cand, free
    return Path(best or "/tmp") / "studio"


WORK = _env_path("STUDIO_WORK", "/kaggle/working" if ON_KAGGLE else str(Path.home() / "studio-work"))
CODE = Path(__file__).resolve().parent.parent          # .../studio
OUT = _env_path("STUDIO_OUT", str(WORK / "outputs"))
JOBS = OUT / "_jobs"
STATE = _env_path("STUDIO_STATE", str(WORK / "studio_state"))
BIG = pick_big_dir()
COMFY_DIR = _env_path("STUDIO_COMFY_DIR", str(BIG / "ComfyUI"))
HF_DIR = _env_path("STUDIO_HF_DIR", str(BIG / "hf"))
VENVS = _env_path("STUDIO_VENVS", str(BIG / "venvs"))
LOGS = _env_path("STUDIO_LOGS", str(BIG / "logs"))

COMFY_URL = os.environ.get("STUDIO_COMFY_URL", "http://127.0.0.1:8188")
OLLAMA_URL = os.environ.get("STUDIO_OLLAMA_URL", "http://127.0.0.1:11434")
BRAIN_MODEL = os.environ.get("STUDIO_BRAIN_MODEL") or _pinned().get("brain") or "qwen3.5:9b"
REVIEW_MODEL = os.environ.get("STUDIO_REVIEW_MODEL", BRAIN_MODEL)
TELEGRAM_MAX_MB = float(os.environ.get("STUDIO_TELEGRAM_MAX_MB", "45"))

SETTINGS_FILE = STATE / "settings.json"


def pin(**kw):
    data = _pinned()
    data.update({k: str(v) for k, v in kw.items()})
    PINNED_FILE.write_text(json.dumps(data, indent=1))


def ensure_dirs():
    for d in (OUT, JOBS, STATE, LOGS):
        d.mkdir(parents=True, exist_ok=True)


def now() -> float:
    return time.time()


def jprint(obj):
    print(json.dumps(obj, indent=1, ensure_ascii=False))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def http_json(url: str, payload=None, timeout: float = 30.0, method: str | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body) if body else None


def alive(url: str, timeout: float = 3.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout).read()
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def run(cmd, check=True, **kw):
    """Run a command, return CompletedProcess; raise with stderr tail on failure."""
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if check and p.returncode != 0:
        tail = (p.stderr or p.stdout or "")[-1500:]
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(map(str, cmd))[:300]}\n{tail}")
    return p


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def fail(msg: str, code: int = 2):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


def media_line(path) -> str:
    """Hermes' gateway turns 'MEDIA:/abs/path' in a reply into a native attachment."""
    return f"MEDIA:{Path(path).resolve()}"
