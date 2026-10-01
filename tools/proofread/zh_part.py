"""Chinese subtitles of one part, straight from beats.json (works before the draft exists).

    python tools/proofread/zh_part.py 4            # newest job under work/
    python tools/proofread/zh_part.py 4 --job xUkV1eR8-Ok

The syllable budgets are recomputed here from each beat's slot, the same way the script stage does,
so the lines can be written while the drafts are still running.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from common import ROOT, args, job_dir, utf8_stdout

utf8_stdout()
sys.path.insert(0, str(ROOT))
from src.config import load_config  # noqa: E402

n = int(args()[0])
job = job_dir()
meta = json.loads((job / "beats.json").read_text(encoding="utf-8"))
part = next(p for p in meta["parts"] if p["index"] == n)
tts = load_config(Path(ROOT / "config" / "settings.yaml"))["tts"]
rate, base, top = tts["syllables_per_second"], tts.get("base_tempo", 1.0), tts["max_tempo"]
for b in meta["beats"]:
    if part["start"] <= b["start"] < part["end"]:
        tgt = max(3, math.floor(b["slot"] * rate * base * 0.95))
        mx = max(tgt, math.floor(b["slot"] * rate * top * 0.9))
        print(f"#{b['index']} {b['start']:.0f}s tgt={tgt} max={mx} "
              f"{b['kind'][0]} {b['source'] or '-'}")
