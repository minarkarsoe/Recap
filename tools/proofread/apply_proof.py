"""Check hand-written narration lines for one part and write them to that part's script.txt.

    python tools/proofread/apply_proof.py 2            # check only: syllable fit + pipeline rules
    python tools/proofread/apply_proof.py 2 --write    # also write work/<job>/parts/p02/script.txt

The lines live in lines_pNN.py next to where the command runs, as a dict ``L = {beat_index: "..."}``
(later ``L.update({...})`` passes are fine). The first write keeps gemma's draft as script.gemma.txt.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

from common import ROOT, args, beats_of, job_dir, part_dir, utf8_stdout

utf8_stdout()
sys.path.insert(0, str(ROOT))
from src.burmese import clean_narration, count_syllables, spell_numbers  # noqa: E402
from src.story_rewriter import _problem  # noqa: E402

n = int(args()[0])
job = job_dir()
lines_file = Path.cwd() / f"lines_p{n:02d}.py"
if not lines_file.exists():
    sys.exit(f"no {lines_file}")
spec = importlib.util.spec_from_file_location("lines", lines_file)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
L = mod.L

pdir = part_dir(job, n)
beats = beats_of(job, n)
missing = [b["index"] for b in beats if b["index"] not in L]
extra = sorted(set(L) - {b["index"] for b in beats})
if extra:
    sys.exit(f"lines for beats not in this part: {extra}")
if missing and "--write" in sys.argv:
    sys.exit(f"missing lines: {missing}")
if missing:
    # A whole-video part runs to hundreds of beats, so check the written ones as the writing goes.
    print(f"checking {len(L)}/{len(beats)} beats; {len(missing)} still unwritten, "
          f"first {missing[0]}, last {missing[-1]}")
    beats = [b for b in beats if b["index"] in L]

over, short, bad, changed = [], [], [], []
tot = tgt = 0
for b in beats:
    text = L[b["index"]]
    final = clean_narration(spell_numbers(text))
    syl = count_syllables(final)
    tot += syl
    tgt += b["target_syllables"]
    if syl > b["max_syllables"]:
        over.append((b["index"], syl, b["max_syllables"]))
    elif syl < 0.75 * b["target_syllables"]:
        short.append((b["index"], syl, b["target_syllables"]))
    if _problem(final):
        bad.append((b["index"], _problem(final)))
    if final != text:
        changed.append(b["index"])

print(f"part {n}: {tot} syllables vs target {tgt} ({tot / tgt * 100:.0f}%)")
print("OVER (index, syl, max):", over)
print("short (index, syl, target):", short)
print("rule problems:", bad)
print("cleaner changes:", changed)

if "--write" in sys.argv:
    if bad:
        sys.exit("not written: fix the rule problems first")
    out = [f"# Part {n} narration. Edit the Burmese lines, save, then run:",
           f"#   python main.py --job {job.name}",
           "# Keep the #number header lines as they are. An empty line under a header = no narration.",
           ""]
    for b in beats:
        stamp = time.strftime("%H:%M:%S", time.gmtime(b["start"]))
        out += [f"#{b['index']} [{stamp}] {b['source'] or '(no dialogue)'}", L[b["index"]], ""]
    draft = pdir / "script.gemma.txt"
    if not draft.exists() and (pdir / "script.txt").exists():
        draft.write_bytes((pdir / "script.txt").read_bytes())
    (pdir / "script.txt").write_text("\n".join(out), encoding="utf-8")
    print(f"written {pdir / 'script.txt'}")
