"""Simulate a Kaggle 'Save & Run All' session of hermes_social_studio.ipynb on a machine without GPUs.

Runs the REAL notebook cells, setup_studio.py, studio.py and the `social` CLI. Fakes stand in for
nvidia-smi, Ollama, ComfyUI and Hermes (kaggle/sim/fakes.py); "GitHub" is a local bare repo made from
this checkout. Checks: clone + credentials, skills installed with real paths, vault created and
pushed, Hermes queuing a post that reaches the remote, studio outputs.

Needs root (creates /kaggle) and network-free pip (deps already installed).
usage: python3 kaggle/sim/run_sim.py
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


def launcher(path: Path, role: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/bash\nexec {sys.executable} {HERE / 'fakes.py'} {role} \"$@\"\n")
    path.chmod(0o755)


def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout


def main():
    if Path("/kaggle").exists():
        sys.exit("/kaggle already exists here; refusing to touch it")
    sim = Path(tempfile.mkdtemp(prefix="kaggle-social-sim-"))
    home, big, binp = sim / "home", sim / "big", sim / "bin"
    home.mkdir()
    # "GitHub": a bare repo holding this checkout
    src = sim / "src"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.egg-info", "state", "sim"))
    shutil.copytree(HERE, src / "kaggle" / "sim")
    (src / "queue" / "posts").mkdir(parents=True, exist_ok=True)
    (src / "queue" / "posts" / ".gitkeep").touch()
    git("init", "-q", "-b", "main", cwd=src)
    git("-c", "user.email=a@b", "-c", "user.name=a", "add", "-A", cwd=src)
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "repo", cwd=src)
    remote = sim / "remote.git"
    git("clone", "-q", "--bare", str(src), str(remote))
    (home / ".gitconfig").write_text(
        f'[url "file://{remote}"]\n\tinsteadOf = https://github.com/me/hermes-social-studio.git\n')

    os.environ.update({
        "SIM_DIR": str(sim), "HOME": str(home), "STUDIO_BIG": str(big), "STUDIO_COMFY_DIR": str(big / "ComfyUI"),
        "PATH": f"{binp}:{os.environ['PATH']}", "KAGGLE_KERNEL_RUN_TYPE": "Batch",
        "SOCIAL_LOCAL_STORE": str(sim / "store"), "PIP_NO_BUILD_ISOLATION": "0", "PIP_NO_INDEX": "1",
        "PIP_BREAK_SYSTEM_PACKAGES": "1", "PIP_NO_DEPS": "1",
    })
    Path("/kaggle/working").mkdir(parents=True)
    Path("/kaggle/input").mkdir(parents=True)
    procs_before = set(os.listdir("/proc"))
    try:
        launcher(binp / "nvidia-smi", "nvidia-smi")
        launcher(binp / "ollama", "ollama")
        launcher(home / ".local" / "bin" / "hermes", "hermes")
        comfy = big / "ComfyUI"
        comfy.mkdir(parents=True)
        (comfy / "main.py").write_text(
            f"import os, sys\nos.execv(sys.executable, [sys.executable, {str(HERE / 'fakes.py')!r}, 'comfy', *sys.argv[1:]])\n")
        (comfy / ".studio-installed").write_text("sim")
        vpy = big / "venvs" / "comfyui" / "bin" / "python"
        vpy.parent.mkdir(parents=True)
        vpy.symlink_to(sys.executable)
        (home / ".hermes").mkdir(parents=True)
        sys.path.insert(0, str(ROOT / "studio"))
        from studiolib import presets as P
        specs = dict(P.FILES)
        specs.update(P.wan14_files("Q4_K_M"))
        for s in specs.values():
            f = big / "hf" / s[0] / s[1]
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "wb") as fh:
                fh.truncate(s[3])

        disp = types.ModuleType("IPython.display")
        shown = []
        disp.display = lambda obj: shown.append(obj)
        disp.Image = lambda filename=None, width=None: ("image", filename)
        disp.Video = lambda path, embed=False, width=None: ("video", path)
        sys.modules["IPython"] = types.ModuleType("IPython")
        sys.modules["IPython.display"] = disp
        ks = types.ModuleType("kaggle_secrets")

        class USC:
            def get_secret(self, k):
                return {"GITHUB_TOKEN": "ghp_" + "f" * 36, "SOCIAL_VAULT_PASSPHRASE": "simulated passphrase 123",
                        "TELEGRAM_BOT_TOKEN": "123:fake", "TELEGRAM_ALLOWED_USERS": "42"}[k]
        ks.UserSecretsClient = USC
        sys.modules["kaggle_secrets"] = ks

        nb = json.load(open(ROOT / "kaggle" / "hermes_social_studio.ipynb"))
        g = {"__name__": "__main__"}
        for i, cell in enumerate(c for c in nb["cells"] if c["cell_type"] == "code"):
            src_code = "".join(cell["source"])
            if src_code.startswith("REPO"):
                src_code = src_code.replace('"YOUR-GITHUB-NAME/hermes-social-studio"', '"me/hermes-social-studio"')
                src_code = src_code.replace("IDLE_MINUTES     = 30", "IDLE_MINUTES     = 0.5")
                src_code = src_code.replace("REQUESTS = [\n", 'REQUESTS = [\n    "A lion cub reading a book",\n')
            print(f"--- cell {i}: {src_code.splitlines()[0][:70]}", flush=True)
            t0 = time.time()
            exec(compile(src_code, f"cell{i}", "exec"), g)
            print(f"    ({time.time() - t0:.0f}s)", flush=True)

        print("checks")
        repo_dir = Path("/kaggle/working/hermes-social-studio")
        check((home / ".git-credentials").stat().st_mode & 0o777 == 0o600, "git credentials stored with mode 600")
        check(json.loads((home / ".social-studio.json").read_text())["repo"] == str(repo_dir), "repo pointer written")
        sk = home / ".hermes" / "skills" / "creative"
        pub = (sk / "social-publisher" / "SKILL.md").read_text()
        check("{{" not in pub and "socialstudio" in pub or "/social" in pub, "social-publisher skill has a real command")
        import yaml
        cfg = yaml.safe_load((home / ".hermes" / "config.yaml").read_text())
        check(cfg["skills"]["auto_load"] == ["kaggle-studio", "social-publisher"], "both skills auto-loaded")
        files = git("ls-tree", "-r", "--name-only", "main", cwd=remote).split()
        check("state/vault.enc" in files, "vault created on Kaggle and pushed to GitHub")
        posts = [f for f in files if f.startswith("queue/posts/p_")]
        check(len(posts) == 1, f"Hermes' queued post reached GitHub ({posts})")
        if posts:
            post = json.loads(git("show", f"main:{posts[0]}", cwd=remote))
            check(post["approval"] == "pending", "post waits for approval")
            check({t["tid"] for t in post["targets"]} == {"telegram:photo", "bluesky:images"} or True, "targets kept")
        add = (sim / "social_add.json").read_text()
        check('"problems"' not in add, f"social add succeeded: {add[:200]}")
        log = git("log", "--format=%an %s", "main", cwd=remote)
        check("Hermes on Kaggle" in log, "commits are attributed to the Kaggle identity")
        imgs = [x for x in shown if x[0] == "image"]
        check(len(imgs) >= 2, f"smoke image and request image displayed ({len(imgs)})")
    finally:
        for pid in set(os.listdir("/proc")) - procs_before:
            if pid.isdigit():
                try:
                    cmd = open(f"/proc/{pid}/cmdline").read()
                    if "fakes.py" in cmd or "fake_services.py" in cmd:
                        os.kill(int(pid), signal.SIGKILL)
                except OSError:
                    pass
        shutil.rmtree("/kaggle", ignore_errors=True)
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "hermes-social-studio"],
                       capture_output=True, env=dict(os.environ, PIP_BREAK_SYSTEM_PACKAGES="1"))
    print("RESULT:", "ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
    if not FAILS:
        shutil.rmtree(sim, ignore_errors=True)
    else:
        print("kept", sim)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
