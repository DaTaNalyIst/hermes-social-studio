"""Outbox: how a Kaggle run hands posts to the publisher without holding any GitHub credential.

On Kaggle (SOCIAL_OUTBOX set, or "outbox" in ~/.social-studio.json), `social add` validates the
post exactly as usual, then copies the spec and its media into the outbox folder instead of
queuing. The folder is part of the notebook's output. When the run ends, the publisher on GitHub
Actions downloads the output and queues every staged post (`social kaggle-collect`).
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from pathlib import Path

from . import ops
from .config import POINTER, Config


def outbox_dir() -> Path | None:
    if os.environ.get("SOCIAL_OUTBOX"):
        return Path(os.environ["SOCIAL_OUTBOX"])
    if POINTER.exists():
        try:
            p = json.loads(POINTER.read_text()).get("outbox")
            return Path(p) if p else None
        except (OSError, ValueError):
            return None
    return None


def current_tag() -> dict:
    """Extra fields the Kaggle autopilot runner wants on posts staged right now."""
    if POINTER.exists():
        try:
            return json.loads(POINTER.read_text()).get("tag") or {}
        except (OSError, ValueError):
            return {}
    return {}


def stage(cfg: Config, spec: dict, base: Path, box: Path) -> dict:
    preview = ops.add(cfg, spec, base, preview=True)  # raises AddError with every problem
    oid = time.strftime("%H%M%S") + "-" + secrets.token_hex(3)
    d = Path(box) / oid
    d.mkdir(parents=True, exist_ok=True)
    staged = dict(spec)
    staged.update(current_tag())
    names = []
    for i, m in enumerate(ops._resolve(spec, base)):
        name = f"m{i}{m.suffix.lower()}"
        shutil.copy2(m, d / name)
        names.append(name)
    staged["media"] = names
    staged.pop("id", None)
    staged["staged_at"] = int(time.time())
    (d / "spec.json").write_text(json.dumps(staged, indent=1, ensure_ascii=False))
    preview["staged"] = oid
    return preview


def items(folder: Path) -> list[tuple[str, Path, dict]]:
    out = []
    for spec in sorted(Path(folder).glob("*/spec.json")):
        try:
            out.append((spec.parent.name, spec.parent, json.loads(spec.read_text())))
        except (OSError, ValueError):
            continue
    return out
