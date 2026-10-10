"""Score finished renders with the local vision model (Ollama), as strict JSON.

Hermes could look at each image itself, but a 9B brain does this more
reliably with a fixed rubric and a JSON schema, one image per call.
"""

from __future__ import annotations

import base64
import io
import time

from . import common as C

SCHEMA = {
    "type": "object",
    "properties": {
        "matches_prompt": {"type": "integer", "minimum": 1, "maximum": 10},
        "anatomy": {"type": "integer", "minimum": 1, "maximum": 10},
        "text_correct": {"type": "integer", "minimum": 1, "maximum": 10},
        "composition": {"type": "integer", "minimum": 1, "maximum": 10},
        "motion": {"type": "integer", "minimum": 1, "maximum": 10},
        "overall": {"type": "integer", "minimum": 1, "maximum": 10},
        "problems": {"type": "array", "items": {"type": "string"}},
        "prompt_fix": {"type": "string"},
    },
    "required": ["matches_prompt", "anatomy", "composition", "overall", "problems", "prompt_fix"],
}

RUBRIC = """You are a strict art director checking an AI render before it goes to a client.
The render was made from this prompt:
---
{prompt}
---
{kind_note}
Score 1-10 (10 = flawless): matches_prompt (subject, setting, style, every requested detail),
anatomy (hands, faces, bodies, animals; 10 if none visible), text_correct (spelling of any
requested text; 10 if no text requested), composition (framing, focus, lighting),
{motion_note}overall. List concrete problems you can SEE (max 4, short). In prompt_fix write
one improved prompt sentence that would fix the biggest problem (or "" if overall >= 8).
Answer with JSON only."""


def _b64_jpeg(path, max_side=1024) -> str:
    from PIL import Image

    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def score(image_path, prompt: str, is_video_sheet: bool = False, model: str | None = None) -> dict:
    kind_note = ("This image is a CONTACT SHEET of frames from a video, left-to-right, top-to-bottom in time "
                 "order. Judge consistency between frames and whether the motion described happens."
                 if is_video_sheet else "This is a single still image.")
    motion_note = "motion (does the described action happen smoothly across frames), " if is_video_sheet else ""
    payload = {
        "model": model or C.REVIEW_MODEL,
        "messages": [{"role": "user",
                      "content": RUBRIC.format(prompt=prompt, kind_note=kind_note, motion_note=motion_note),
                      "images": [_b64_jpeg(image_path)]}],
        "format": SCHEMA,
        "stream": False,
        "think": False,
        "keep_alive": -1,
        # num_ctx is left to the server default (OLLAMA_CONTEXT_LENGTH) on purpose: a different
        # value would make Ollama reload the brain model between Hermes turns and reviews.
        "options": {"temperature": 0},
    }
    t0 = time.time()
    res = C.http_json(C.OLLAMA_URL + "/api/chat", payload, timeout=600)
    import json

    content = res.get("message", {}).get("content", "{}")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {"overall": 0, "problems": ["reviewer returned non-JSON"], "prompt_fix": "", "raw": content[:400]}
    data["review_s"] = round(time.time() - t0, 1)
    return data


def review_job(state: dict, threshold: int = 7) -> dict:
    results = []
    for o in state.get("outputs", []):
        if o["type"] == "image":
            r = score(o["path"], o["prompt"])
        else:
            r = score(o["sheet"], o["prompt"], is_video_sheet=True)
        r.update({"item": o["item"], "seed": o["seed"], "path": o["path"]})
        r["verdict"] = "keep" if r.get("overall", 0) >= threshold else "redo"
        results.append(r)
    results.sort(key=lambda r: -r.get("overall", 0))
    return {"job_id": state.get("job_id"), "threshold": threshold, "best": results[0]["item"] if results else None,
            "results": results}
