"""Contact sheets of a part's frames for chosen beats: one row per beat, 3 frames across it.

    python tools/proofread/sheet.py 4 554 556 560 ...   -> sheet_p04_<first>.jpg (6 beats a sheet)

The sheets are written next to wherever you run the command, so keep that in a scratch folder.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from common import args, beats_of, job_dir, part_dir, utf8_stdout, video_speed

utf8_stdout()
pos = args()
n, want = int(pos[0]), [int(a) for a in pos[1:]]
job = job_dir()
meta = json.loads((job / "beats.json").read_text(encoding="utf-8"))
part = next(p for p in meta["parts"] if p["index"] == n)
speed = video_speed()
src_start = part["start"] * speed
beats = {b["index"]: b for b in beats_of(job, n)}
frames = part_dir(job, n) / "frames"
count = len(list(frames.glob("*.jpg")))
if not count:
    sys.exit(f"no frames in {frames}; the script stage writes them")

for s in range(0, len(want), 6):
    group = want[s:s + 6]
    files: list[Path] = []
    for i in group:
        b = beats[i]
        for f in (0.15, 0.5, 0.85):  # start, middle and end of the beat
            k = min(max(round((b["start"] + f * (b["end"] - b["start"])) * speed - src_start), 0), count - 1)
            files.append(frames / f"{k + 1:05d}.jpg")
    while len(files) < 18:  # tile needs a full 3x6 grid
        files.append(files[-1])
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        for p in files:
            fh.write(f"file '{p.as_posix()}'\nduration 1\n")
        lst = fh.name
    out = Path.cwd() / f"sheet_p{n:02d}_{group[0]}.jpg"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-vf", "scale=320:180,tile=3x6", "-frames:v", "1", "-q:v", "4", str(out)], check=True)
    print(out.name, group)
