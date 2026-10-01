"""Write a lines_pNN.py seeded from the draft, ready to be corrected by hand.

    python tools/proofread/seed.py 1 > lines_p01.py

This is the middle road the user picked on 2026-09-26: keep gemma's draft for its length, put a
person on every line for its accuracy. The draft fills the slots well (it measured 104% of budget
on one job against 87% for a hand-written pass) but gets names, person and numbers wrong, follows
the captions into their homophone errors, and leaves a third of its lines over the maximum.

Each beat is written out with everything needed to judge it in place: the source line, the budget,
the draft's own syllable count, and a marker when the draft is already OVER its ceiling or short of
its slot. Correct the Burmese in this file, then run apply_proof.py on it.
"""
from __future__ import annotations

import sys
from pathlib import Path

from common import ROOT, args, beats_of, job_dir, utf8_stdout

utf8_stdout()
sys.path.insert(0, str(ROOT))
from src.burmese import clean_narration, count_syllables, spell_numbers  # noqa: E402

n = int(args()[0])
beats = beats_of(job_dir(), n)
print("# -*- coding: utf-8 -*-")
print(f'"""Part {n} narration, seeded from the draft and corrected by hand.')
print()
print("Markers: OVER = the draft is past max and must be cut; short = under 75% of target.")
print('"""')
print()
print("L = {")
for b in beats:
    text = (b.get("narration") or "").replace('"', "'").strip()
    tgt, mx = b["target_syllables"], b["max_syllables"]
    syl = count_syllables(clean_narration(spell_numbers(text)))
    flag = " OVER" if syl > mx else (" short" if syl < 0.75 * tgt else "")
    source = (b.get("source") or "-").replace("\n", " ")
    print(f'    # [{b["start"]:.0f}s] {source}')
    print(f'    # tgt={tgt} max={mx} draft={syl}{flag}')
    print(f'    {b["index"]}: "{text}",')
print("}")
