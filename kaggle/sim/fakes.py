"""Fake binaries for the Kaggle simulation: nvidia-smi, ollama, hermes, and ComfyUI's main.py.

Each is installed as a small launcher script that calls this file with a role name.
"""

import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SIM = Path(os.environ["SIM_DIR"])
REPO = Path(__file__).resolve().parent.parent.parent


def nvidia_smi(args):
    if any("index" in a for a in args):
        print("0, Tesla T4, 9000, 15360, 3\n1, Tesla T4, 300, 15360, 0")
    else:
        print("Tesla T4, 15360 MiB\nTesla T4, 15360 MiB")


class OllamaH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        pulled = (SIM / "pulled.txt").read_text().split() if (SIM / "pulled.txt").exists() else []
        if self.path == "/api/tags":
            return self._j({"models": [{"name": m} for m in pulled]})
        if self.path == "/api/ps":
            return self._j({"models": [{"name": m, "size": 9_000_000_000, "size_vram": 9_000_000_000}
                                       for m in pulled]})
        self._j({})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/chat":
            with open(SIM / "ollama_env.txt", "w") as f:
                f.write(json.dumps({k: os.environ.get(k) for k in
                                    ("CUDA_VISIBLE_DEVICES", "OLLAMA_CONTEXT_LENGTH", "OLLAMA_MODELS")}))
            if body.get("format"):
                return self._j({"message": {"content": json.dumps({
                    "matches_prompt": 8, "anatomy": 9, "composition": 8, "overall": 8,
                    "problems": [], "prompt_fix": ""})}})
            return self._j({"message": {"content": "ready"}})
        self._j({})


def ollama(args):
    if args[:1] == ["serve"]:
        ThreadingHTTPServer(("127.0.0.1", 11434), OllamaH).serve_forever()
    elif args[:1] == ["pull"]:
        time.sleep(1)
        with open(SIM / "pulled.txt", "a") as f:
            f.write(args[1] + "\n")
        print("success")


def comfy_main(args):
    # main.py stand-in: the fake ComfyUI from the end-to-end test, on port 8188
    with open(SIM / "comfy_env.txt", "w") as f:
        f.write(json.dumps({"CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"), "args": args}))
    os.execv(sys.executable, [sys.executable, str(REPO / "studio" / "tests" / "fake_services.py"), "8188", "21435"])


def _db():
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    con = sqlite3.connect(home / "state.db")
    con.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
                "content TEXT, timestamp REAL NOT NULL)")
    return con


def hermes(args):
    with open(SIM / "hermes_calls.txt", "a") as f:
        f.write(json.dumps(args) + "\n")
    if args[:1] == ["-z"]:
        print("HERMES OK")
        return
    if args[:2] == ["gateway", "run"]:
        con = _db()
        con.execute("INSERT INTO messages(session_id, role, content, timestamp) VALUES('tg','user','hi',?)",
                    (time.time(),))
        con.commit()
        print("gateway: telegram connected", flush=True)
        while True:
            time.sleep(1)
    if args[:1] == ["chat"] and "--query-file" in args:
        msg = sys.stdin.read()
        if msg.startswith("AUTOPILOT TASK"):
            return autopilot_turn(msg)
        sys.stdin = io.StringIO(msg)
    if args[:1] == ["chat"]:
        msg = sys.stdin.read()
        sid = args[args.index("--resume") + 1] if "--resume" in args else "sess-1"
        print(json.dumps({"type": "system", "subtype": "init", "model": "qwen3.5:9b", "session_id": sid}))
        repo = Path(json.loads((Path.home() / ".social-studio.json").read_text())["repo"])
        cmd = [sys.executable, str(repo / "studio" / "studio.py"), "image", msg[:60], "--n", "1", "--wait", "120"]
        print(json.dumps({"type": "tool_use", "name": "terminal", "input": {"command": " ".join(cmd)}}))
        r = json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)
        img = r["outputs"][0]["path"]
        post = Path("/kaggle/working/outputs/posts/sim.json")
        post.write_text(json.dumps({"title": msg[:40], "media": [img], "caption": "Sim post #test",
                                    "targets": [{"platform": "telegram", "format": "photo"},
                                                {"platform": "bluesky", "format": "images"}]}))
        for plat in ("telegram", "bluesky"):  # stands in for the user having connected these accounts
            subprocess.run([sys.executable, "-m", "socialstudio.cli", "enable", plat], capture_output=True)
        add = [sys.executable, "-m", "socialstudio.cli", "--json", "add", str(post)]
        print(json.dumps({"type": "tool_use", "name": "terminal", "input": {"command": " ".join(add)}}))
        p = subprocess.run(add, capture_output=True, text=True)
        with open(SIM / "social_add.json", "w") as f:
            f.write(p.stdout or p.stderr)
        queued = json.loads(p.stdout)
        text = f"Queued {queued['id']} for approval.\n" + "\n".join(r.get("deliver", []))
        print(json.dumps({"type": "result", "session_id": sid, "exit_code": 0, "text": text}))


def autopilot_turn(msg):
    """What a well-behaved Hermes does with an AUTOPILOT TASK: render, write the post file, stage it."""
    import re
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "auto"}))
    post_file = Path(re.search(r"write the post file (\S+)", msg).group(1))
    targets = json.loads(re.search(r"exactly these targets:\s*(\[.*?\])\n", msg, re.S).group(1))
    social = re.search(r"3\. Run: (.+?) --json add", msg).group(1).split()
    media = []
    if "TEXT post" not in msg:
        repo = Path(json.loads((Path.home() / ".social-studio.json").read_text())["repo"])
        r = json.loads(subprocess.run([sys.executable, str(repo / "studio" / "studio.py"), "image",
                                       "a rooftop cafe in Nairobi at dusk", "--n", "1", "--wait", "120"],
                                      capture_output=True, text=True).stdout)
        media = [r["outputs"][0]["path"]]
    post_file.parent.mkdir(parents=True, exist_ok=True)
    post_file.write_text(json.dumps({"title": "Rooftop cafe at dusk", "topic": "rooftop cafes", "language": "en",
                                     "media": media, "caption": "Golden hour above the city #nairobi",
                                     "targets": targets}))
    p = subprocess.run([*social, "--json", "add", str(post_file)], capture_output=True, text=True)
    with open(SIM / "autopilot_add.json", "a") as f:
        f.write((p.stdout or p.stderr) + "\n")
    print(json.dumps({"type": "result", "session_id": "auto", "exit_code": 0,
                      "text": "DONE rooftop cafes" if p.returncode == 0 else "FAILED " + p.stdout[-200:]}))


if __name__ == "__main__":
    role, rest = sys.argv[1], sys.argv[2:]
    {"nvidia-smi": nvidia_smi, "ollama": ollama, "hermes": hermes, "comfy": comfy_main}[role](rest)
