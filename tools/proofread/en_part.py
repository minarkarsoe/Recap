"""Everything needed to proofread one part by hand, in one listing.

    python tools/proofread/en_part.py 4 [--job <folder>]

Each beat prints its syllable budget and the subtitle lines it covers. Shows with an English
subtitle track need no gloss: the source already says what happens, so it is printed in full.
"""
from __future__ import annotations

from common import args, beats_of, job_dir, utf8_stdout

utf8_stdout()
n = int(args()[0])
for b in beats_of(job_dir(), n):
    head = f"#{b['index']} {b['start']:.0f}s tgt={b['target_syllables']} max={b['max_syllables']}"
    if b.get("kind") == "visual" or not b["source"]:
        print(f"{head} VISUAL {b.get('gloss', '-')[:300]}")
    else:
        print(f"{head} {b['source']}")
