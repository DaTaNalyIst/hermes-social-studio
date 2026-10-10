"""Chatterbox voice: a separate venv (its torch pin must not touch ComfyUI's) run on GPU 0."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import common as C

CHATTERBOX_REF = os.environ.get("STUDIO_CHATTERBOX_REF", "5de7a54aa4e5e2baadb0182dde554908b48b85c2")
VENV = C.VENVS / "chatterbox"
PY = VENV / "bin" / "python"
WORKER = C.CODE / "voice_worker.py"


def installed() -> bool:
    if not PY.exists():
        return False
    p = subprocess.run([str(PY), "-c", "import chatterbox, torch; print(torch.cuda.is_available())"],
                       capture_output=True, text=True)
    return p.returncode == 0


def install(log=print):
    if installed():
        return
    log("installing Chatterbox in its own environment (first time only, ~3 min) ...")
    C.VENVS.mkdir(parents=True, exist_ok=True)
    C.run([sys.executable, "-m", "pip", "install", "-q", "uv"])
    C.run([sys.executable, "-m", "uv", "venv", "--seed", "--python", sys.executable, str(VENV)])
    C.run([sys.executable, "-m", "uv", "pip", "install", "--python", str(PY),
           f"chatterbox-tts @ git+https://github.com/resemble-ai/chatterbox.git@{CHATTERBOX_REF}"],
          timeout=3600)
    if not installed():
        raise RuntimeError("Chatterbox installed but does not import; see the pip output above")
    log("Chatterbox ready")


def synthesize(spec_path, out_dir, log=print) -> dict:
    install(log)
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = os.environ.get("STUDIO_VOICE_GPU", "0")
    env["HF_HOME"] = str(C.HF_DIR / "_hf_home")
    log_path = Path(out_dir) / "voice.log"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as lf:
        p = subprocess.run([str(PY), str(WORKER), str(spec_path), str(out_dir)], stdout=lf,
                           stderr=subprocess.STDOUT, env=env, timeout=4 * 3600)
    if p.returncode != 0:
        tail = log_path.read_text(errors="replace")[-1500:]
        raise RuntimeError(f"voice failed (see {log_path}):\n{tail}")
    return C.read_json(Path(out_dir) / "voice_manifest.json")
