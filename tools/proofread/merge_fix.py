"""Merge hand-corrected lines into a part's lines_pNN.py, measuring each one first.

    python tools/proofread/merge_fix.py 1            # check the corrections only
    python tools/proofread/merge_fix.py 1 --write    # merge them into lines_p01.py

The corrections live in fix_pNN.py next to where the command runs, as ``U = {beat: "..."}``.
Only the beats in U are touched; everything else in lines_pNN.py is left exactly as it is.

This is for the half of proofreading the local model cannot do -- a line whose meaning is
inverted, whose speaker is wrong, or that dropped a figure. expand.py would happily rewrite
such a line to the right length and leave it just as wrong, so those beats are corrected by
hand here and then measured against the same budget the rest of the part was held to.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from common import ROOT, args, beats_of, job_dir, utf8_stdout

utf8_stdout()
sys.path.insert(0, str(ROOT))
from src.burmese import clean_narration, count_syllables, spell_numbers  # noqa: E402
from src.story_rewriter import _problem  # noqa: E402


def load(path: Path, var: str) -> dict:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(getattr(mod, var))


n = int(args()[0])
job = job_dir()
lines_file = Path.cwd() / f"lines_p{n:02d}.py"
fix_file = Path.cwd() / f"fix_p{n:02d}.py"
for f in (lines_file, fix_file):
    if not f.exists():
        sys.exit(f"no {f.name}")

L = load(lines_file, "L")
U = load(fix_file, "U")
beats = {b["index"]: b for b in beats_of(job, n)}

stray = sorted(set(U) - set(beats))
if stray:
    sys.exit(f"corrections for beats not in part {n}: {stray}")

ok, bad = [], []
for i, text in sorted(U.items()):
    b = beats[i]
    final = clean_narration(spell_numbers(text))
    syl = count_syllables(final)
    was = count_syllables(clean_narration(spell_numbers(L.get(i, ""))))
    note = ""
    if p := _problem(final):
        note = f"RULE: {p}"
    elif syl > b["max_syllables"]:
        note = f"OVER by {syl - b['max_syllables']}"
    elif syl < 0.75 * b["target_syllables"]:
        note = "short"
    (bad if note else ok).append(
        f"  #{i:4d}  {was:3d} -> {syl:3d}  (target {b['target_syllables']}, "
        f"max {b['max_syllables']})  {note}")

print(f"part {n}: {len(U)} correction(s)")
for row in ok:
    print(row)
for row in bad:
    print(row)

if "--write" in sys.argv:
    if bad:
        sys.exit(f"\nnot written: {len(bad)} correction(s) do not fit -- fix them first")
    L.update({i: clean_narration(spell_numbers(t)) for i, t in U.items()})
    with open(lines_file, "w", encoding="utf-8") as fh:
        fh.write(f"# Part {n} of {job.name}. Expanded by the local model, then hand-corrected\n"
                 f"# where the meaning, the speaker or a figure was wrong.\n"
                 "L = {\n")
        for i in sorted(L):
            tgt = beats[i]["target_syllables"] if i in beats else 0
            fh.write(f"    {i}: {json.dumps(L[i], ensure_ascii=False)},  # {tgt} target\n")
        fh.write("}\n")
    print(f"\nmerged {len(U)} correction(s) into {lines_file.name}")
