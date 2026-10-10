#!/usr/bin/env python3
"""Install and start everything Hermes Studio needs inside a Kaggle T4 x2 session.

GPU 0: Ollama serving the brain model (+ Chatterbox voice when a film needs it)
GPU 1: ComfyUI render server
Then Hermes Agent is installed and pointed at the local brain, with the
kaggle-studio skill. Every step is idempotent: re-running the cell skips work
that is already done and restarts only services that are down.

usage: python3 setup_studio.py [--brain qwen3.5:9b] [--ctx 65536] [--voice-now] [--skip-hermes]
Settings also come from env vars set by the notebook (STUDIO_*).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studiolib import common as C  # noqa: E402

OLLAMA_VERSION = os.environ.get("STUDIO_OLLAMA_VERSION", "0.40.0")
COMFY_TAG = os.environ.get("STUDIO_COMFY_TAG", "v0.38.2")
GGUF_REF = os.environ.get("STUDIO_GGUF_REF", "6ea2651e7df66d7585f6ffee804b20e92fb38b8a")
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES_BIN = Path.home() / ".local" / "bin" / "hermes"
COMFY_VENV = C.BIG / "venvs" / "comfyui"
# Skills live in the repo's skills/ folder (kaggle-studio, social-publisher, ...).
SKILLS_SRC = C.CODE.parent / "skills"
SKILLS_DST = HERMES_HOME / "skills" / "creative"

T0 = time.time()


def say(msg):
    print(f"[{time.time() - T0:6.0f}s] {msg}", flush=True)


def sh(cmd, log_name=None, check=True, env=None, timeout=3600):
    """Run a shell command, logging to LOGS/<log_name>.log; raise with the tail on failure."""
    C.LOGS.mkdir(parents=True, exist_ok=True)
    log = C.LOGS / f"{log_name or 'setup'}.log"
    with open(log, "a") as f:
        f.write(f"\n$ {cmd}\n")
        f.flush()
        p = subprocess.run(cmd, shell=True, stdout=f, stderr=subprocess.STDOUT, env=env, timeout=timeout,
                           executable="/bin/bash")
    if check and p.returncode != 0:
        tail = log.read_text(errors="replace")[-2500:]
        raise SystemExit(f"\nFAILED: {cmd}\n--- last lines of {log} ---\n{tail}")
    return p.returncode


def spawn(cmd: list, log_name: str, env: dict):
    log = open(C.LOGS / f"{log_name}.log", "a")
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                            start_new_session=True)


# ---------------------------------------------------------------------------
def preflight():
    say("checking the machine")
    gpus = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                          capture_output=True, text=True).stdout.strip().splitlines()
    if not gpus:
        raise SystemExit("No GPU found. In the notebook sidebar set Accelerator to 'GPU T4 x2', then restart.")
    say(f"GPUs: {gpus}")
    if len(gpus) < 2:
        say("WARNING: only one GPU. Brain and renderer will share it (slower, video may not fit). "
            "Choose 'GPU T4 x2' for the intended setup.")
    if subprocess.run("curl -sI --max-time 10 https://github.com >/dev/null", shell=True).returncode != 0:
        raise SystemExit("No internet. In the notebook sidebar turn Internet ON "
                         "(needs a phone-verified Kaggle account).")
    for d in (C.BIG, C.LOGS, C.OUT, C.STATE):
        d.mkdir(parents=True, exist_ok=True)
    C.pin(big=C.BIG)
    say(f"scratch disk for models: {C.BIG} ({C.human_bytes(shutil.disk_usage(C.BIG).free)} free)")
    say(f"logs: {C.LOGS}")
    return len(gpus)


def gpu_ids(n_gpus):
    brain = os.environ.get("STUDIO_BRAIN_GPU", "0")
    render = os.environ.get("STUDIO_RENDER_GPU", "1" if n_gpus >= 2 else "0")
    return brain, render


# ---------------------------------------------------------------------------
def install_ollama():
    if shutil.which("ollama"):
        return
    say(f"installing Ollama {OLLAMA_VERSION}")
    if not shutil.which("zstd"):
        sh("apt-get -qq update && apt-get -qq install -y zstd", "ollama_install")
    url = f"https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-linux-amd64.tar.zst"
    rc = sh(f"curl -fsSL {url} | zstd -d | tar -x -C /usr/local", "ollama_install", check=False)
    if rc != 0:
        say("pinned Ollama release not found; installing the latest from ollama.com")
        sh("curl -fsSL https://ollama.com/download/ollama-linux-amd64.tar.zst | zstd -d | tar -x -C /usr/local",
           "ollama_install")


def start_ollama(brain_gpu, ctx):
    if C.alive(C.OLLAMA_URL + "/api/tags"):
        return
    say(f"starting Ollama on GPU {brain_gpu} (context {ctx})")
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=brain_gpu, OLLAMA_HOST="127.0.0.1:11434",
               OLLAMA_MODELS=str(C.BIG / "ollama"), OLLAMA_CONTEXT_LENGTH=str(ctx), OLLAMA_KEEP_ALIVE="-1",
               OLLAMA_NUM_PARALLEL="1", OLLAMA_MAX_LOADED_MODELS="1")
    spawn(["ollama", "serve"], "ollama", env)
    for _ in range(60):
        if C.alive(C.OLLAMA_URL + "/api/tags"):
            return
        time.sleep(1)
    raise SystemExit(f"Ollama did not start; see {C.LOGS / 'ollama.log'}")


def pull_brain(model):
    tags = C.http_json(C.OLLAMA_URL + "/api/tags") or {}
    if any(m.get("name") == model for m in tags.get("models", [])):
        return
    say(f"downloading the brain model {model} (~7 GB)")
    sh(f"ollama pull {model}", "ollama_pull", timeout=3600)


def warm_brain(model):
    say("loading the brain onto the GPU")
    t = time.time()
    C.http_json(C.OLLAMA_URL + "/api/chat", {"model": model, "stream": False, "think": False, "keep_alive": -1,
                                            "messages": [{"role": "user", "content": "Reply with: ready"}]},
                timeout=900)
    ps = C.http_json(C.OLLAMA_URL + "/api/ps") or {}
    for m in ps.get("models", []):
        vram, size = m.get("size_vram", 0), m.get("size", 1)
        pct = 100 * vram / max(1, size)
        say(f"brain loaded in {time.time() - t:.0f}s: {C.human_bytes(size)}, {pct:.0f}% on GPU")
        if pct < 90:
            say("WARNING: part of the brain runs on the CPU, so replies will be slow. "
                "Use a smaller model (STUDIO_BRAIN_MODEL='qwen3.5:4b') or a shorter context.")


# ---------------------------------------------------------------------------
def install_comfy():
    if (C.COMFY_DIR / "main.py").exists() and (COMFY_VENV / "bin" / "python").exists() \
            and (C.COMFY_DIR / ".studio-installed").exists():
        return
    say(f"installing ComfyUI {COMFY_TAG} + ComfyUI-GGUF")
    if not (C.COMFY_DIR / "main.py").exists():
        shutil.rmtree(C.COMFY_DIR, ignore_errors=True)
        sh(f"git clone -q --depth 1 --branch {COMFY_TAG} https://github.com/comfyanonymous/ComfyUI.git "
           f"'{C.COMFY_DIR}'", "comfy_install")
    gg = C.COMFY_DIR / "custom_nodes" / "ComfyUI-GGUF"
    if not gg.exists():
        sh(f"git clone -q https://github.com/city96/ComfyUI-GGUF.git '{gg}' && git -C '{gg}' checkout -q {GGUF_REF}",
           "comfy_install")
    # A venv that SEES Kaggle's CUDA torch (system site packages) but installs ComfyUI's extra
    # packages privately, so nothing in the notebook's own Python environment changes.
    if not (COMFY_VENV / "bin" / "python").exists():
        sh(f"{sys.executable} -m venv --system-site-packages '{COMFY_VENV}'", "comfy_install")
    req = C.COMFY_DIR / "requirements.txt"
    kept = [l for l in req.read_text().splitlines()
            if l.strip() and not re.match(r"^(torch|torchvision|torchaudio)\b", l.strip())]
    filtered = C.COMFY_DIR / "requirements.studio.txt"
    filtered.write_text("\n".join(kept) + "\n")
    pip = f"'{COMFY_VENV}/bin/python' -m pip install -q --disable-pip-version-check"
    sh(f"{pip} -r '{filtered}'", "comfy_install")
    sh(f"{pip} -r '{gg}/requirements.txt'", "comfy_install")
    (C.COMFY_DIR / ".studio-installed").write_text(COMFY_TAG)


def start_comfy(render_gpu):
    if C.alive(C.COMFY_URL + "/system_stats"):
        return
    say(f"starting ComfyUI on GPU {render_gpu}")
    (C.COMFY_DIR / "output").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=render_gpu)
    # No --force-fp16: on the T4 ComfyUI then picks fp32 for Z-Image (fp16 gives black images)
    # and fp16 for the models that support it.
    spawn([str(COMFY_VENV / "bin" / "python"), str(C.COMFY_DIR / "main.py"), "--listen", "127.0.0.1",
           "--port", "8188", "--disable-auto-launch", "--preview-method", "none",
           "--output-directory", str(C.COMFY_DIR / "output")], "comfyui", env)
    for _ in range(300):
        if C.alive(C.COMFY_URL + "/system_stats"):
            say("ComfyUI is up")
            return
        time.sleep(2)
    tail = (C.LOGS / "comfyui.log").read_text(errors="replace")[-2000:]
    raise SystemExit(f"ComfyUI did not start. Last log lines:\n{tail}")


# ---------------------------------------------------------------------------
def install_hermes():
    if HERMES_BIN.exists():
        return
    say("installing Hermes Agent (~2-4 min)")
    flags = "--non-interactive --skip-browser --skip-computer-use"
    rc = sh(f"curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- {flags}",
            "hermes_install", check=False, timeout=2400)
    if rc != 0 or not HERMES_BIN.exists():
        say("installer script failed; trying the GitHub checkout")
        src = C.BIG / "hermes-src"
        shutil.rmtree(src, ignore_errors=True)
        sh(f"git clone -q --depth 1 https://github.com/NousResearch/hermes-agent.git '{src}' && "
           f"bash '{src}/scripts/install.sh' {flags}", "hermes_install", timeout=2400)
    if not HERMES_BIN.exists():
        raise SystemExit(f"Hermes did not install; see {C.LOGS / 'hermes_install.log'}")


def deep_merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def hermes_config(model, ctx):
    import yaml

    local = {"provider": "custom", "model": model, "base_url": C.OLLAMA_URL + "/v1", "api_key": "ollama"}
    ours = {
        "model": {"provider": "custom", "default": model, "base_url": C.OLLAMA_URL + "/v1", "api_key": "ollama",
                  "context_length": ctx, "ollama_num_ctx": ctx},
        "terminal": {"backend": "local", "cwd": str(C.WORK), "timeout": 600},
        "approvals": {"mode": "off", "deny": ["rm -rf /*", "rm -rf /kaggle*", "rm -rf ~*", "mkfs*", "shutdown*",
                                              "reboot*"]},
        "agent": {"max_turns": 80, "gateway_timeout": 3600},
        "timeouts": {"tools": {"concurrent_batch": 900, "sequential_call": 900}},
        "auxiliary": {
            "vision": dict(local, timeout=300),
            "compression": dict(local, timeout=300),
            "approval": dict(local, timeout=60),
            "title_generation": {"enabled": False},
            "background_review": {"enabled": False},
        },
        "memory": {"nudge_interval": 0},
        "skills": {"auto_load": sorted(p.name for p in SKILLS_SRC.iterdir() if (p / "SKILL.md").exists()),
                   "creation_nudge_interval": 0},
        "platform_toolsets": {
            "cli": ["terminal", "file", "vision", "skills", "todo", "memory"],
            "telegram": ["terminal", "file", "vision", "skills", "todo", "memory", "clarify"],
        },
        "curator": {"enabled": False},
    }
    cfg_path = HERMES_HOME / "config.yaml"
    cfg = {}
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    # the template's model block may be a plain string; replace it wholesale
    if not isinstance(cfg.get("model"), dict):
        cfg["model"] = {}
    deep_merge(cfg, ours)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))

    env_path = HERMES_HOME / ".env"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    want = {
        "HERMES_STREAM_READ_TIMEOUT": "1800",
        "HERMES_API_TIMEOUT": "1800",
    }
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS"):
        if os.environ.get(key):
            want[key] = os.environ[key]
    kept = [l for l in lines if l.split("=", 1)[0].strip() not in want]
    env_path.write_text("\n".join(kept + [f"{k}={v}" for k, v in want.items()]) + "\n")
    os.chmod(env_path, 0o600)


def social_cmd() -> str:
    exe = shutil.which("social")
    return exe or f"{sys.executable} -m socialstudio.cli"


def install_skill():
    names = []
    for src in sorted(p for p in SKILLS_SRC.iterdir() if (p / "SKILL.md").exists()):
        dst = SKILLS_DST / src.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        for md in dst.rglob("*.md"):
            md.write_text(md.read_text().replace("{{STUDIO}}", str(C.CODE / "studio.py"))
                          .replace("{{PYTHON}}", sys.executable).replace("{{OUT}}", str(C.OUT))
                          .replace("{{SOCIAL}}", social_cmd()).replace("{{REPO}}", str(C.CODE.parent))
                          .replace("{{SERVE}}", str(C.CODE / "serve.py")))
        names.append(src.name)
    say(f"skills installed: {', '.join(names)}")
    return names


def restore_state():
    """If an earlier notebook version's output is attached as input, bring back Hermes' memory."""
    marker = HERMES_HOME / ".studio_restored"
    if marker.exists() or not Path("/kaggle/input").exists():
        return
    for cand in sorted(Path("/kaggle/input").glob("**/studio_state/hermes_state.tgz")):
        say(f"restoring Hermes memory from {cand}")
        HERMES_HOME.mkdir(parents=True, exist_ok=True)
        sh(f"tar -xzf '{cand}' -C '{HERMES_HOME}'", "setup", check=False)
        break
    marker.write_text("1")


def hermes_smoke(model):
    say("checking that Hermes can talk to the brain")
    p = subprocess.run([str(HERMES_BIN), "-z", "Reply with exactly: HERMES OK"], capture_output=True, text=True,
                       timeout=900, cwd=str(C.WORK))
    out = (p.stdout or "").strip()
    say(f"Hermes says: {out[-200:] or '(nothing)'}")
    if p.returncode != 0 or not out:
        say(f"WARNING: Hermes did not answer cleanly (exit {p.returncode}). stderr tail:\n{(p.stderr or '')[-1200:]}")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brain", default=os.environ.get("STUDIO_BRAIN_MODEL", C.BRAIN_MODEL))
    ap.add_argument("--ctx", type=int, default=int(os.environ.get("STUDIO_CTX", "65536")))
    ap.add_argument("--voice-now", action="store_true", help="install Chatterbox now instead of on first film")
    ap.add_argument("--skip-hermes", action="store_true")
    a = ap.parse_args()

    n_gpus = preflight()
    C.pin(brain=a.brain)
    brain_gpu, render_gpu = gpu_ids(n_gpus)
    install_ollama()
    start_ollama(brain_gpu, a.ctx)
    # ComfyUI installs while the brain downloads
    pull = subprocess.Popen(["ollama", "pull", a.brain], stdout=open(C.LOGS / "ollama_pull.log", "a"),
                            stderr=subprocess.STDOUT)
    install_comfy()
    start_comfy(render_gpu)
    if not a.skip_hermes:
        install_hermes()
        restore_state()
        hermes_config(a.brain, a.ctx)
        install_skill()
    if pull.wait() != 0:
        pull_brain(a.brain)  # retry in the foreground with a visible error
    warm_brain(a.brain)
    if a.voice_now:
        from studiolib import voice
        voice.install(say)
    if not a.skip_hermes:
        hermes_smoke(a.brain)
    C.write_json(C.STATE / "session.json", {"started": C.read_json(C.STATE / "session.json", {}).get("started")
                                            if (C.STATE / "session.json").exists() else T0,
                                            "mode": os.environ.get("KAGGLE_KERNEL_RUN_TYPE", "unknown"),
                                            "brain": a.brain})
    say(f"Studio is ready. Outputs: {C.OUT}  Logs: {C.LOGS}")


if __name__ == "__main__":
    main()
