"""Compact view for proofreading: every beat's syllable budget plus a trimmed gloss.

    python tools/proofread/compact_part.py 4 [--job <folder>]
"""
from __future__ import annotations

from common import args, beats_of, job_dir, utf8_stdout

utf8_stdout()
n = int(args()[0])
for b in beats_of(job_dir(), n):
    head = f"#{b['index']} {b['start']:.0f}s tgt={b['target_syllables']} max={b['max_syllables']}"
    if b.get("kind") == "visual" or not b["source"]:
        print(f"{head} VISUAL: {b.get('gloss', '-')[:260]}")
    else:
        print(f"{head} | {b.get('gloss', '-')[:200]}")
