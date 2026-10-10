"""Download model weights on demand and keep the scratch disk from filling up.

Files land in HF_DIR/<repo>/<path> and are symlinked into ComfyUI's models
folders. When space runs short, weights of the least recently used presets
are deleted (they re-download in a minute or two when needed again).
"""

from __future__ import annotations

import os
import shutil
import time
import urllib.request
from pathlib import Path

from . import common as C
from . import presets as P

USAGE_FILE = C.HF_DIR / "_last_used.json"
SAFETY_MARGIN = 6 * 1024 ** 3  # keep 6 GB free for outputs, venvs, temp files


def _target(spec) -> Path:
    return C.HF_DIR / spec[0] / spec[1]


def _link(spec) -> Path:
    return C.COMFY_DIR / "models" / spec[2] / P.local_name(spec)


def is_present(spec) -> bool:
    t = _target(spec)
    return t.exists() and t.stat().st_size == spec[3]


def _touch_usage(keys):
    usage = C.read_json(USAGE_FILE, {}) or {}
    for k in keys:
        usage[k] = time.time()
    C.write_json(USAGE_FILE, usage)


def _download(spec, log=print):
    target = _target(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download  # preinstalled on Kaggle

        hf_hub_download(repo_id=spec[0], filename=spec[1], local_dir=str(C.HF_DIR / spec[0]))
    except ImportError:
        tmp = target.with_suffix(target.suffix + ".part")
        start = tmp.stat().st_size if tmp.exists() else 0
        req = urllib.request.Request(P.url_for(spec), headers={"Range": f"bytes={start}-"} if start else {})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "ab" if start else "wb") as f:
            shutil.copyfileobj(r, f, 16 * 1024 * 1024)
        os.replace(tmp, target)
    if target.stat().st_size != spec[3]:
        raise RuntimeError(f"size mismatch for {spec[1]}: {target.stat().st_size} != {spec[3]}")


def _ensure_link(spec):
    link = _link(spec)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        if link.resolve() == _target(spec).resolve():
            return
        link.unlink()
    link.symlink_to(_target(spec))


def free_bytes() -> int:
    C.HF_DIR.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(C.HF_DIR).free


def evict(needed: int, keep: set, log=print) -> int:
    """Delete least-recently-used weight files not in `keep` until `needed` bytes are free."""
    usage = C.read_json(USAGE_FILE, {}) or {}
    all_specs = dict(P.FILES)
    for q in P.WAN14_QUANT_SIZES:
        all_specs.update(P.wan14_files(q))
    candidates = [(usage.get(k, 0), k, s) for k, s in all_specs.items() if k not in keep and is_present(s)]
    candidates.sort()
    freed = 0
    for _, k, s in candidates:
        if free_bytes() - SAFETY_MARGIN >= needed:
            break
        _target(s).unlink(missing_ok=True)
        link = _link(s)
        if link.is_symlink():
            link.unlink()
        freed += s[3]
        log(f"evicted {P.local_name(s)} ({C.human_bytes(s[3])}) to make room")
    return freed


def ensure_preset(preset: str, quant: str | None = None, log=print) -> dict:
    files = P.preset_files(preset, quant)
    missing = {k: s for k, s in files.items() if not is_present(s)}
    need = sum(s[3] for s in missing.values())
    if need and free_bytes() - SAFETY_MARGIN < need:
        evict(need, keep=set(files), log=log)
        if free_bytes() - SAFETY_MARGIN < need:
            raise RuntimeError(
                f"not enough disk for {preset}: need {C.human_bytes(need)}, free {C.human_bytes(free_bytes())}")
    for k, s in missing.items():
        t0 = time.time()
        log(f"downloading {P.local_name(s)} ({C.human_bytes(s[3])}) ...")
        _download(s, log)
        log(f"  done in {time.time() - t0:.0f}s")
    for s in files.values():
        _ensure_link(s)
    _touch_usage(files)
    return {"preset": preset, "downloaded": len(missing), "bytes": need}


def disk_report() -> dict:
    present = {}
    all_specs = dict(P.FILES)
    for q in P.WAN14_QUANT_SIZES:
        all_specs.update(P.wan14_files(q))
    for k, s in all_specs.items():
        if is_present(s):
            present[P.local_name(s)] = C.human_bytes(s[3])
    return {"weights_on_disk": present, "free": C.human_bytes(free_bytes()), "dir": str(C.HF_DIR)}
