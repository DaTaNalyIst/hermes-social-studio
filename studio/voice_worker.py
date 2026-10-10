"""Runs INSIDE the Chatterbox venv (torch 2.6) on GPU 0. Not imported by studio.py.

usage: python voice_worker.py lines.json out_dir
lines.json: {"engine": "turbo" | "multilingual", "voice_ref": "/path.wav" | null,
             "exaggeration": 0.5, "lines": [{"id": "s01", "text": "...", "lang": "en"}, ...]}
Writes out_dir/<id>.wav (24 kHz mono) and out_dir/voice_manifest.json.
"""

import json
import re
import sys
import time
from pathlib import Path

import torch
import torchaudio as ta

MAX_CHARS = 260


def chunks(text: str):
    """Split long narration at sentence ends so each generate() call stays short."""
    parts = re.split(r"(?<=[.!?;:])\s+", text.strip())
    out, cur = [], ""
    for p in parts:
        if len(cur) + len(p) + 1 <= MAX_CHARS:
            cur = (cur + " " + p).strip()
        else:
            if cur:
                out.append(cur)
            while len(p) > MAX_CHARS:  # no punctuation: cut at a space
                cut = p.rfind(" ", 0, MAX_CHARS)
                cut = cut if cut > 40 else MAX_CHARS
                out.append(p[:cut])
                p = p[cut:].strip()
            cur = p
    if cur:
        out.append(cur)
    return out


def main(spec_path, out_dir):
    spec = json.load(open(spec_path, encoding="utf-8"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    engine = spec.get("engine", "turbo")
    langs = {(l.get("lang") or "en").lower() for l in spec["lines"]}
    if engine == "turbo" and langs != {"en"}:
        engine = "multilingual"  # Turbo is English-only
    device = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.time()
    if engine == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        model = ChatterboxTurboTTS.from_pretrained(device=device)
    else:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model="v3")
    print(f"loaded {engine} on {device} in {time.time() - t0:.0f}s", flush=True)
    ref = spec.get("voice_ref") or None
    exag = float(spec.get("exaggeration", 0.5))
    gap = torch.zeros(1, int(model.sr * 0.15))
    results = []
    for line in spec["lines"]:
        pieces = []
        for c in chunks(line["text"]):
            if engine == "turbo":
                wav = model.generate(c, audio_prompt_path=ref)
            else:
                wav = model.generate(c, language_id=(line.get("lang") or "en").lower(),
                                     audio_prompt_path=ref, exaggeration=exag)
            ref = None  # the model caches the voice conditionals after the first call
            pieces += [wav.cpu(), gap]
        audio = torch.cat(pieces[:-1], dim=-1) if pieces else torch.zeros(1, model.sr)
        path = out / f"{line['id']}.wav"
        ta.save(str(path), audio, model.sr)
        dur = audio.shape[-1] / model.sr
        results.append({"id": line["id"], "path": str(path), "seconds": round(dur, 3)})
        print(f"{line['id']}: {dur:.1f}s", flush=True)
    json.dump({"engine": engine, "sr": model.sr, "lines": results},
              open(out / "voice_manifest.json", "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
