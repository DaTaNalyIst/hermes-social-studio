"""Build kaggle/hermes_social_studio.ipynb. The notebook clones YOUR copy of this repo on Kaggle,
so edits you push to GitHub reach the next Kaggle session automatically.

usage: python3 kaggle/build_notebook.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(True)}


def code(text):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.strip("\n").splitlines(True)}


CELLS = [
    md("""
# Hermes Social Studio on Kaggle (100% free)

Hermes Agent with an open brain makes images, clips and narrated films on Kaggle's free **GPU T4 x2**,
then queues them for **Facebook, Instagram, Threads, YouTube, Bluesky and Telegram**. A free GitHub
Actions publisher in your repo posts each one at the best time for your audience.

**Before running (right-hand panel):**
1. *Session options*: **Accelerator GPU T4 x2**, **Internet on** (phone-verified account).
2. *Add-ons → Secrets*, ticked for this notebook:
   * `GITHUB_TOKEN`: fine-grained token for your repo only, **Contents: Read and write** (docs/setup-github.md)
   * `SOCIAL_VAULT_PASSPHRASE`: your vault passphrase (only needed to connect accounts)
   * optional `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USERS`: to chat with Hermes from your phone

**Run all** to work interactively, or *Save Version → Save & Run All* to let it run unattended until idle.
"""),
    md("## 1. Settings"),
    code("""
REPO             = "YOUR-GITHUB-NAME/hermes-social-studio"   # your copy of the repo
BRAIN_MODEL      = "qwen3.5:9b"     # alternatives: "gemma4:12b"; "qwen3.5:4b" if replies are slow
CONTEXT          = 65536
IDLE_MINUTES     = 30
MAX_HOURS        = 11.5
PREINSTALL_VOICE = False

# Unattended runs without Telegram: Hermes works through these, then the session ends.
REQUESTS = [
    # "Make a 30-second vertical short about Nairobi street food with English narration, then queue it
    #  for Instagram, Facebook, YouTube and Telegram at the best times. Do not approve it.",
]
"""),
    md("## 2. Secrets, your repo and the `social` command"),
    code("""
import json, os, subprocess, sys
from pathlib import Path

WORK = Path("/kaggle/working")
try:
    from kaggle_secrets import UserSecretsClient
    _s = UserSecretsClient()
    for key in ("GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USERS"):
        try:
            os.environ[key] = _s.get_secret(key)
        except Exception:
            pass
    try:   # kept out of os.environ so Hermes' tools do not inherit it; only social() below uses it
        _VAULT_PW = _s.get_secret("SOCIAL_VAULT_PASSPHRASE")
    except Exception:
        _VAULT_PW = None
except ImportError:
    _VAULT_PW = os.environ.pop("SOCIAL_VAULT_PASSPHRASE", None)

REPO_DIR = WORK / REPO.split("/")[-1]
tok = os.environ.get("GITHUB_TOKEN", "")
if tok:
    cred = Path.home() / ".git-credentials"
    cred.write_text(f"https://x-access-token:{tok}@github.com\\n")
    cred.chmod(0o600)
    subprocess.run(["git", "config", "--global", "credential.helper", "store"], check=True)
else:
    print("WARNING: no GITHUB_TOKEN secret - the repo is read-only here, so posts cannot be queued.")
subprocess.run(["git", "config", "--global", "user.name", "Hermes on Kaggle"], check=True)
subprocess.run(["git", "config", "--global", "user.email", "hermes-kaggle@users.noreply.github.com"], check=True)
if (REPO_DIR / ".git").exists():
    subprocess.run(["git", "-C", str(REPO_DIR), "pull", "-q", "--ff-only"], check=False)
else:
    subprocess.run(["git", "clone", "-q", f"https://github.com/{REPO}.git", str(REPO_DIR)], check=True)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", str(REPO_DIR)], check=True)
(Path.home() / ".social-studio.json").write_text(json.dumps({"repo": str(REPO_DIR)}))
os.environ["SOCIAL_REPO_DIR"] = str(REPO_DIR)
(WORK / "outputs" / "posts").mkdir(parents=True, exist_ok=True)
STUDIO = REPO_DIR / "studio"
os.environ["STUDIO_BRAIN_MODEL"] = BRAIN_MODEL
TELEGRAM = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_ALLOWED_USERS"))
RUN_TYPE = os.environ.get("KAGGLE_KERNEL_RUN_TYPE", "Interactive")

# this kernel started before the install, so add the package path by hand (new processes see it anyway)
sys.path.insert(0, str(REPO_DIR / "src"))
from socialstudio.cli import main as _social_main
def social(*args):
    \"\"\"Run a `social` command in this notebook (prompts for hidden input work here).\"\"\"
    if _VAULT_PW:
        os.environ["SOCIAL_VAULT_PASSPHRASE"] = _VAULT_PW
    try:
        _social_main([str(a) for a in args])
    except SystemExit as e:
        if e.code not in (0, None):
            print(f"(exit code {e.code})")
    finally:
        os.environ.pop("SOCIAL_VAULT_PASSPHRASE", None)

print(f"repo: {REPO_DIR} | Telegram chat: {'on' if TELEGRAM else 'off'} | run type: {RUN_TYPE}")
"""),
    md("## 3. Install and start the studio (~8-12 min the first time in a session)"),
    code("""
cmd = [sys.executable, str(STUDIO / "setup_studio.py"), "--brain", BRAIN_MODEL, "--ctx", str(CONTEXT)]
if PREINSTALL_VOICE:
    cmd.append("--voice-now")
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
for line in p.stdout:
    print(line, end="")
if p.wait() != 0:
    raise SystemExit("Setup failed - read the message above.")
"""),
    md("## 4. Smoke test: one image straight through the studio"),
    code("""
from IPython.display import Image, Video, display

def studio(*args):
    out = subprocess.run([sys.executable, str(STUDIO / "studio.py"), *map(str, args)], capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        print(out.stdout[-3000:], out.stderr[-3000:])
        raise

r = studio("image", "a red fox sitting in fresh snow, golden hour light, 85mm photo", "--n", "1", "--seed", "7",
           "--wait", "1200")
print(json.dumps({k: r.get(k) for k in ("status", "message", "elapsed_s")}, indent=1))
for o in r.get("outputs", []):
    display(Image(filename=o["path"], width=384))
"""),
    md("""
## 5. Connect your accounts (once; you type, Hermes never sees the secrets)

Run these yourself in an **interactive** session. Each asks for its secrets in hidden fields, saves them in
the encrypted vault in your repo and switches the platform on. Platform guides are in `docs/`.
"""),
    code("""
if not (REPO_DIR / "state" / "vault.enc").exists():
    print("No vault yet: creating studio.toml and the vault (uses SOCIAL_VAULT_PASSPHRASE).")
    social("init")   # set your AUDIENCE's timezone in studio.toml (or: social("init", "--timezone", "Europe/London"))

# Uncomment one line at a time and run the cell:
# social("auth", "facebook")    # Facebook Page + the Instagram account linked to it
# social("auth", "threads")
# social("auth", "youtube")
# social("auth", "bluesky")
# social("auth", "telegram")    # a channel your bot posts to
# social("auth", "hosting")     # optional S3 bucket (Threads videos)
social("doctor")
"""),
    md("""
## 6. Chat with Hermes

`ask("...")` talks to Hermes and shows the commands it runs and the files it delivers. Hermes queues posts
but never approves them unless you say so; you can also approve on GitHub (an issue opens for each post).
"""),
    code("""
import re
HERMES = str(Path.home() / ".local/bin/hermes")
_session = {"id": None}

def _show_media(text):
    for path in re.findall(r"MEDIA:(\\S+)", text):
        p = Path(path)
        if not p.exists():
            continue
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            display(Image(filename=str(p), width=512))
        elif p.suffix.lower() in (".mp4", ".webm", ".mov") and p.stat().st_size < 40 * 1024 * 1024:
            display(Video(str(p), embed=True, width=512))
        else:
            print("file:", p)

def ask(message, new=False, show_tools=True):
    cmd = [HERMES, "chat", "--query-file", "-", "--format", "stream-json"]
    if _session["id"] and not new:
        cmd += ["--resume", _session["id"]]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, cwd=str(WORK))
    p.stdin.write(message); p.stdin.close()
    final = ""
    for line in p.stdout:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("session_id"):
            _session["id"] = ev["session_id"]
        if ev.get("type") == "tool_use" and show_tools:
            print(f"  -> {ev.get('name')} {json.dumps(ev.get('input', ''))[:160]}")
        elif ev.get("type") == "result":
            final = ev.get("text") or ev.get("error") or ""
    p.wait()
    print(re.sub(r"MEDIA:\\S+", "", final).strip())
    _show_media(final)

# Examples:
# ask("Make 3 square images of a Nairobi rooftop cafe at dusk, pick the best and queue it for Instagram and Facebook.")
# ask("What's in the social queue this week? Move Thursday's reel to 19:00.")
"""),
    md("""
## 7. Telegram chat (interactive) or unattended run (*Save & Run All*)
"""),
    code("""
serve = [sys.executable, str(STUDIO / "serve.py"), "--idle-minutes", str(IDLE_MINUTES), "--max-hours", str(MAX_HOURS)]
if RUN_TYPE == "Interactive":
    if TELEGRAM:
        subprocess.run(serve + ["--background"])
    else:
        print("No Telegram secrets: chat with ask(...) above.")
else:
    for req in REQUESTS:
        print("=" * 80, "\\nREQUEST:", req)
        ask(req, new=True, show_tools=False)
    if TELEGRAM:
        subprocess.run(serve)
"""),
    md("## 8. Finish: save Hermes' memory and list files"),
    code("""
def finish():
    subprocess.run([sys.executable, str(STUDIO / "serve.py"), "--stop"])
    social("queue")
    files = sorted(p for p in (WORK / "outputs").rglob("*") if p.is_file() and "_jobs" not in p.parts)
    print(f"{len(files)} files in /kaggle/working/outputs")

if RUN_TYPE != "Interactive":
    finish()
else:
    print("When you are done, run: finish()")
"""),
]


def build():
    nb = {"cells": CELLS,
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                       "language_info": {"name": "python"},
                       "kaggle": {"accelerator": "nvidiaTeslaT4", "isInternetEnabled": True, "isGpuEnabled": True,
                                  "dataSources": []}},
          "nbformat": 4, "nbformat_minor": 5}
    out = ROOT / "kaggle" / "hermes_social_studio.ipynb"
    out.write_text(json.dumps(nb, indent=1) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    build()
