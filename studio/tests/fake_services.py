"""Fake ComfyUI (port 8188) and Ollama (port 11434) for the local end-to-end test.

ComfyUI: accepts /prompt, then after ~1 s writes PNGs for the SaveImage node
(one image, or `length` frames for video graphs) and reports success in
/history. A prompt whose text contains FAILME reports an execution error.
Ollama: /api/tags and /api/chat with a fixed JSON review.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw

COMFY_DIR = Path(os.environ["STUDIO_COMFY_DIR"])
HISTORY = {}
QUEUE = []
SEEN = []


def render(pid, prompt):
    time.sleep(1.0)
    save = next(n for n in prompt.values() if n["class_type"] == "SaveImage")
    prefix = save["inputs"]["filename_prefix"]
    text = " ".join(str(n["inputs"].get("text", "")) for n in prompt.values())
    if "FAILME" in text:
        HISTORY[pid] = {"prompt": [0, pid, prompt], "outputs": {},
                        "status": {"status_str": "error", "completed": False,
                                   "messages": [["execution_error", {"node_type": "KSampler",
                                                                     "exception_message": "CUDA out of memory",
                                                                     "exception_type": "OutOfMemoryError"}]]}}
        QUEUE.remove(pid)
        return
    latent = next((n for n in prompt.values() if "length" in n["inputs"]), None)
    frames = latent["inputs"]["length"] if latent else 1
    size = next((n["inputs"] for n in prompt.values() if "width" in n["inputs"]), {"width": 64, "height": 64})
    w, h = max(64, size["width"] // 8), max(64, size["height"] // 8)
    sub = os.path.dirname(prefix)
    base = os.path.basename(prefix)
    d = COMFY_DIR / "output" / sub
    d.mkdir(parents=True, exist_ok=True)
    imgs = []
    for i in range(frames):
        im = Image.new("RGB", (w, h), (20 + i * 2 % 200, 80, 140))
        ImageDraw.Draw(im).ellipse([i % w, h // 4, i % w + h // 2, 3 * h // 4], fill="orange")
        name = f"{base}_{i + 1:05d}_.png"
        im.save(d / name)
        imgs.append({"filename": name, "subfolder": sub, "type": "output"})
    save_id = next(k for k, n in prompt.items() if n["class_type"] == "SaveImage")
    HISTORY[pid] = {"prompt": [0, pid, prompt], "outputs": {save_id: {"images": imgs}},
                    "status": {"status_str": "success", "completed": True, "messages": []}}
    QUEUE.remove(pid)


class Comfy(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/system_stats":
            return self._json({"system": {}, "devices": []})
        if self.path.startswith("/history/"):
            pid = self.path.split("/")[-1]
            return self._json({pid: HISTORY[pid]} if pid in HISTORY else {})
        if self.path == "/queue":
            return self._json({"queue_running": [[0, p] for p in QUEUE[:1]],
                               "queue_pending": [[0, p] for p in QUEUE[1:]]})
        self._json({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        if self.path == "/prompt":
            pid = body["prompt_id"]
            SEEN.append(body["prompt"])
            QUEUE.append(pid)
            threading.Thread(target=render, args=(pid, body["prompt"]), daemon=True).start()
            return self._json({"prompt_id": pid, "number": len(SEEN), "node_errors": {}})
        if self.path == "/free":
            return self._json({})
        self._json({}, 404)


class Ollama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._json({"models": [{"name": "qwen3.5:9b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["messages"][0]["images"], "review must send an image"
        assert body["format"]["type"] == "object"
        score = 8 if "sheet" not in body["messages"][0]["content"].lower() else 6
        self._json({"message": {"role": "assistant", "content": json.dumps({
            "matches_prompt": score, "anatomy": 9, "text_correct": 10, "composition": 8, "motion": 7,
            "overall": score, "problems": ["test problem"], "prompt_fix": "sharper focus on the fox"})}})


if __name__ == "__main__":
    c = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Comfy)
    o = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), Ollama)
    threading.Thread(target=o.serve_forever, daemon=True).start()
    print("fake services up", flush=True)
    c.serve_forever()
