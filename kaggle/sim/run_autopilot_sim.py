"""Simulate one full autopilot day without GPUs or accounts.

GitHub side (real code): `social autopilot-launch` builds and "pushes" the notebook with a fake
Kaggle CLI. Kaggle side (real code): the pushed notebook runs kaggle_run.py -> setup_studio.py ->
autopilot.py, with fakes for nvidia-smi, Ollama, ComfyUI and Hermes. GitHub side again:
`social kaggle-collect` downloads the outputs through the fake CLI and queues the posts.

Needs root (creates /kaggle).  usage: python3 kaggle/sim/run_autopilot_sim.py
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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


FAKE_KAGGLE = r'''#!PY
import json, os, re, shutil, sys
from pathlib import Path
sim = Path(os.environ["SIM_DIR"])
a = sys.argv[1:]
with open(sim / "kaggle_calls.txt", "a") as f:
    f.write(json.dumps(a) + "\n")
if a[:2] == ["kernels", "push"]:
    d = Path(a[a.index("-p") + 1])
    meta = json.loads((d / "kernel-metadata.json").read_text())
    shutil.copy(d / meta["code_file"], sim / "pushed.ipynb")
    print("Kernel version 1 successfully pushed.")
elif a[:2] == ["kernels", "status"]:
    print(a[2] + ' has status "KernelWorkerStatus.COMPLETE"')
elif a[:2] == ["kernels", "output"]:
    dest = Path(a[a.index("-p") + 1])
    pat = re.compile(a[a.index("--file-pattern") + 1])
    work = Path("/kaggle/working")
    for f in work.rglob("*"):
        rel = str(f.relative_to(work))
        if f.is_file() and pat.search(rel):
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest / rel)
'''


def main():
    if Path("/kaggle").exists():
        sys.exit("/kaggle already exists here; refusing to touch it")
    sim = Path(tempfile.mkdtemp(prefix="autopilot-sim-"))
    home, big, binp = sim / "home", sim / "big", sim / "bin"
    home.mkdir()
    gh = sim / "github-repo"
    shutil.copytree(ROOT, gh, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.egg-info", "sim"))
    os.environ.update({
        "SIM_DIR": str(sim), "HOME": str(home), "STUDIO_BIG": str(big), "STUDIO_COMFY_DIR": str(big / "ComfyUI"),
        "PATH": f"{binp}:{home / '.local' / 'bin'}:{os.environ['PATH']}", "KAGGLE_KERNEL_RUN_TYPE": "Batch",
        "PIP_NO_BUILD_ISOLATION": "0", "PIP_NO_INDEX": "1", "PIP_BREAK_SYSTEM_PACKAGES": "1", "PIP_NO_DEPS": "1",
        "KAGGLE_API_TOKEN": "KGAT_sim", "SOCIAL_VAULT_PASSPHRASE": "simulated passphrase 123",
    })
    gh_env = dict(os.environ, SOCIAL_REPO_DIR=str(gh), SOCIAL_NO_GIT="1", SOCIAL_LOCAL_STORE=str(sim / "store"),
                  PYTHONPATH=str(gh / "src"))
    Path("/kaggle/working").mkdir(parents=True)
    Path("/kaggle/input").mkdir(parents=True)
    procs_before = set(os.listdir("/proc"))
    try:
        # --- fakes -----------------------------------------------------------------
        launcher(binp / "nvidia-smi", "nvidia-smi")
        launcher(binp / "ollama", "ollama")
        launcher(home / ".local" / "bin" / "hermes", "hermes")
        (binp / "kaggle").write_text(FAKE_KAGGLE.replace("#!PY", f"#!{sys.executable}", 1))
        (binp / "kaggle").chmod(0o755)
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

        # --- GitHub side: configure and launch ---------------------------------------
        def social(*args, ok=True):
            p = subprocess.run([sys.executable, "-m", "socialstudio.cli", "--json", *args], capture_output=True,
                               text=True, env=gh_env)
            if ok and p.returncode != 0:
                raise SystemExit(f"social {args} failed:\n{p.stdout}\n{p.stderr}")
            return json.loads(p.stdout) if p.stdout.strip() else {}

        social("init", "--timezone", "Africa/Nairobi")
        social("enable", "telegram")
        toml = gh / "studio.toml"
        toml.write_text(toml.read_text().replace('username = ""', 'username = "kevo"'))
        today = datetime.now(ZoneInfo("Africa/Nairobi")).date().isoformat()
        ap = (gh / "autopilot.toml").read_text().replace("enabled = false", "enabled = true")
        ap = ap.replace('days = ["tue", "thu", "sat", "sun"]', 'days = ["daily"]')
        ap = ap.replace('targets = ["instagram:image", "facebook:photo", "telegram:photo"]', 'targets = ["telegram:photo"]')
        (gh / "autopilot.toml").write_text(ap)
        run = social("autopilot-launch", "--date", today)
        print("launched", run["run_id"])

        # --- Kaggle side: execute the pushed notebook ---------------------------------
        ks = types.ModuleType("kaggle_secrets")

        class USC:
            def get_secret(self, k):
                raise RuntimeError("no secrets attached")
        ks.UserSecretsClient = USC
        sys.modules["kaggle_secrets"] = ks
        nb = json.loads((sim / "pushed.ipynb").read_text())
        g = {"__name__": "__main__"}
        kenv = {k: v for k, v in os.environ.items() if not k.startswith("SOCIAL_") and k != "KAGGLE_API_TOKEN"}
        saved_env = dict(os.environ)
        os.environ.clear()
        os.environ.update(kenv)  # the Kaggle run has no vault passphrase and no Kaggle/GitHub tokens
        try:
            for i, cell in enumerate(c for c in nb["cells"] if c["cell_type"] == "code"):
                src = "".join(cell["source"])
                print(f"--- kaggle cell {i}: {src.splitlines()[0][:60]}", flush=True)
                exec(compile(src, f"cell{i}", "exec"), g)
        finally:
            os.environ.clear()
            os.environ.update(saved_env)

        report = json.loads(Path("/kaggle/working/run_report/report.json").read_text())
        check(report["ok"] and report["run_id"] == run["run_id"], f"Kaggle run reported ok ({report.get('error')})")
        check(len(report["outbox_items"]) == 1, "one post staged in the outbox")
        check((Path("/kaggle/working/studio_memory/hermes_memory.tgz")).exists(), "Hermes' memory packed")
        check("SOCIAL_VAULT_PASSPHRASE" not in Path("/kaggle/working/repo/run.json").read_text()
              and not (Path("/kaggle/working/repo/state/vault.enc")).exists(), "no vault or passphrase on Kaggle")

        # --- GitHub side: collect --------------------------------------------------
        col = social("kaggle-collect")
        check(col and col[0]["status"] == "complete" and len(col[0]["posts"]) == 1 and not col[0]["errors"],
              f"collector queued the post ({col})")
        posts = [json.loads(p.read_text()) for p in (gh / "queue" / "posts").glob("p_*.json")]
        check(len(posts) == 1, "post is in the queue")
        if posts:
            p = posts[0]
            check(p["source"] == "kaggle:autopilot" and p["approval"] == "pending" and p.get("auto_approve_at"),
                  "autopilot post waits for approval with a rejection window")
            check(p["autopilot"] == {"date": today, "slot": "image"}, "post knows its date and slot")
            check(p["targets"][0]["assets"][0]["asset_id"], "media uploaded to the store")
        hist = json.loads((gh / "state" / "autopilot_history.json").read_text())
        check(hist[-1]["topic"] == "rooftop cafes", "topic remembered so it is not repeated")
        check((gh / "state" / "hermes_memory.tgz").exists(), "memory carried to the next run")
        again = social("kaggle-collect")
        check(again == [] or again == {}, "second collect does nothing")
        calls = [json.loads(l) for l in (sim / "kaggle_calls.txt").read_text().splitlines()]
        check(any(c[:2] == ["kernels", "push"] and "--accelerator" in c for c in calls), "pushed with GPU")
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
