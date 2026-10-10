"""Stand-in for voice_worker.py: writes tone WAVs whose length follows the text."""

import json
import os
import subprocess
import sys
from pathlib import Path

spec = json.load(open(sys.argv[1]))
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
with open(Path(os.environ["STUDIO_WORK"]).parent / "voice-calls.log", "a") as f:
    f.write(",".join(l["id"] for l in spec["lines"]) + "\n")
res = []
for i, line in enumerate(spec["lines"]):
    dur = max(1.0, len(line["text"]) / 25)
    p = out / f"{line['id']}.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={300 + 50 * i}:duration={dur}", "-ar", "24000", "-ac", "1", str(p)], check=True)
    res.append({"id": line["id"], "path": str(p), "seconds": round(dur, 3)})
json.dump({"engine": spec.get("engine"), "sr": 24000, "lines": res}, open(out / "voice_manifest.json", "w"))
