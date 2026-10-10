"""Minimal client for the local ComfyUI server (HTTP API, no websockets)."""

from __future__ import annotations

import time
import urllib.error
import uuid

from . import common as C


class ComfyError(RuntimeError):
    pass


def ready() -> bool:
    return C.alive(C.COMFY_URL + "/system_stats")


def wait_ready(timeout: float = 600) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if ready():
            return
        time.sleep(2)
    raise ComfyError(f"ComfyUI did not answer at {C.COMFY_URL} within {timeout:.0f}s "
                     f"(see {C.LOGS / 'comfyui.log'})")


def submit(prompt: dict) -> str:
    pid = str(uuid.uuid4())
    try:
        res = C.http_json(C.COMFY_URL + "/prompt", {"prompt": prompt, "prompt_id": pid,
                                                    "client_id": "studio"}, timeout=60)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:2000]
        raise ComfyError(f"ComfyUI rejected the workflow: {body}") from None
    if res.get("node_errors"):
        raise ComfyError(f"node errors: {res['node_errors']}")
    return res.get("prompt_id", pid)


def history(pid: str) -> dict | None:
    h = C.http_json(f"{C.COMFY_URL}/history/{pid}", timeout=30) or {}
    return h.get(pid)


def queue_position(pid: str) -> str:
    try:
        q = C.http_json(C.COMFY_URL + "/queue", timeout=10) or {}
    except Exception:
        return "unknown"
    if any(item[1] == pid for item in q.get("queue_running", [])):
        return "running"
    pending = [item[1] for item in q.get("queue_pending", [])]
    return f"queued ({pending.index(pid) + 1} ahead)" if pid in pending else "unknown"


def wait(pid: str, timeout: float, on_tick=None) -> dict:
    """Block until the prompt finishes; return its history entry."""
    t0 = time.time()
    while True:
        h = history(pid)
        st = (h or {}).get("status") or {}
        if h and st.get("completed") is not None:
            if st.get("status_str") == "error" or not st.get("completed"):
                msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                detail = msgs[-1][1] if msgs else st
                if isinstance(detail, dict):
                    detail = {k: detail.get(k) for k in ("node_type", "exception_message", "exception_type")}
                raise ComfyError(f"render failed: {detail}")
            return h
        if h and h.get("outputs") and not h.get("status"):
            return h
        if time.time() - t0 > timeout:
            raise ComfyError(f"render timed out after {timeout:.0f}s")
        if on_tick:
            on_tick(time.time() - t0)
        time.sleep(3)


def output_files(h: dict) -> list:
    """Absolute paths of the images ComfyUI saved for one finished prompt."""
    files = []
    for node_out in (h.get("outputs") or {}).values():
        for im in node_out.get("images", []):
            if im.get("type", "output") != "output":
                continue
            p = C.COMFY_DIR / "output" / im.get("subfolder", "") / im["filename"]
            files.append(p)
    return sorted(files)


def free_vram() -> None:
    try:
        C.http_json(C.COMFY_URL + "/free", {"unload_models": True, "free_memory": True}, timeout=10)
    except Exception:
        pass
