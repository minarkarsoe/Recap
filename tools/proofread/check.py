"""Audit a part's narration without calling any model, and print only what needs a human.

    python tools/proofread/check.py 2          # audit part 2 of the current job
    python tools/proofread/check.py 1-8        # every part
    python tools/proofread/check.py 2 --lines  # read the flagged lines with their source

It reads lines_pNN.py if one sits in the current directory, otherwise the part's script.json,
so it works on the local model's expansion as well as on gemma's raw draft.

The checks are the ones that are mechanical and were each learned from a job that went wrong:

  fit            syllables against the beat's target and maximum, and the part's fill
  rules          the pipeline's own _problem(): CJK, Zawgyi, foreign script, literary register
  glossary       a beat whose source names someone the glossary covers, whose line does not
                 use the Burmese name -- this is how invented characters get in
  numbers        a beat whose source carries a figure, whose line carries none -- amounts were
                 dropped or mangled on three earlier jobs
  empty          a beat with a slot and no narration at all

What it cannot check is meaning: whether the speaker is right, whether the draft inverted a
line. Read the flagged beats, and use sheet.py for the ones that need the picture.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

from common import ROOT, args, beats_of, job_dir, part_dir, utf8_stdout

utf8_stdout()
sys.path.insert(0, str(ROOT))
from src.burmese import clean_narration, count_syllables, spell_numbers  # noqa: E402
from src.story_rewriter import _problem  # noqa: E402

CJK = re.compile(r"[一-鿿]")
DIGITS = re.compile(r"\d")
CN_NUM = re.compile(r"[一二三四五六七八九十百千万亿两]")
MM_NUM = re.compile(r"[၀-၉]|တစ်|နှစ်|သုံး|လေး|ငါး|ခြောက်|ခုနစ်|ရှစ်|ကိုး|ဆယ်|ရာ|ထောင်|သောင်း|သိန်း|သန်း")


# Forms of address are in the glossary so the model spells them the same way when it uses them,
# but third-person narration is free to name the person instead -- "Lin Feng" where the dialogue
# says "young master". Flagging those would bury the real misses, so they are skipped here.
ADDRESS = ("少爷", "小老板", "老板", "宿主", "系统")
ADDRESS_TAIL = ("爷", "板", "主", "哥", "姐", "叔", "长", "会长")


def short_form_used(my: str, text: str) -> bool:
    """True when the line uses a shortened form of a compound glossary name.

    A compound name is written out in full the first time and shortened afterwards, which
    is how Burmese actually reads -- the egg comes off the fried rice, the city name comes
    off the district. Any suffix of at least half the name counts as the name being used,
    which keeps the glossary check pointed at real misses instead of at ordinary prose.
    """
    return any(my[cut:] in text for cut in range(1, len(my) // 2 + 1))


def is_address(key: str) -> bool:
    return key in ADDRESS or key.endswith(ADDRESS_TAIL)


def load_lines(job: Path, n: int) -> dict[int, str]:
    f = Path.cwd() / f"lines_p{n:02d}.py"
    if f.exists():
        spec = importlib.util.spec_from_file_location("lines", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return dict(mod.L)
    script = part_dir(job, n) / "script.json"
    if not script.exists():
        return {}
    return {b["index"]: b.get("narration", "")
            for b in json.loads(script.read_text(encoding="utf-8"))["beats"]}


def audit(job: Path, n: int, glossary: dict[str, str], show: bool) -> None:
    beats = beats_of(job, n)
    lines = load_lines(job, n)
    if not lines:
        print(f"part {n}: nothing written yet")
        return

    over, short, bad, empty, gloss, nums = [], [], [], [], [], []
    tot = tgt = 0
    for b in beats:
        raw = lines.get(b["index"], "")
        text = clean_narration(spell_numbers(raw))
        syl = count_syllables(text)
        tot += syl
        tgt += b["target_syllables"]
        src = b["source"] or ""
        if not text.strip():
            empty.append(b["index"])
            continue
        if syl > b["max_syllables"]:
            over.append((b["index"], syl, b["max_syllables"]))
        elif syl < 0.75 * b["target_syllables"]:
            short.append((b["index"], syl, b["target_syllables"]))
        if p := _problem(text):
            bad.append((b["index"], p))
        elif CJK.search(text):
            bad.append((b["index"], "CJK left in the line"))
        for key, my in glossary.items():
            if key in src and my not in text and not is_address(key) \
                    and not short_form_used(my, text):
                gloss.append((b["index"], key, my))
                break
        if (DIGITS.search(src) or CN_NUM.search(src)) and not MM_NUM.search(text):
            nums.append((b["index"], src[:40]))

    fill = tot / tgt * 100 if tgt else 0
    print(f"\n=== part {n}: {len(beats)} beats, {tot}/{tgt} syllables ({fill:.0f}%) ===")
    print(f"  OVER {len(over)}   short {len(short)}   rule problems {len(bad)}   empty {len(empty)}")
    print(f"  glossary name missing {len(gloss)}   figure dropped {len(nums)}")
    for label, rows in (("OVER", over), ("short", short), ("rules", bad), ("empty", empty),
                        ("glossary", gloss), ("numbers", nums)):
        if rows:
            print(f"  {label}: {rows[:25]}{' ...' if len(rows) > 25 else ''}")
    if show:
        flagged = {r[0] if isinstance(r, tuple) else r
                   for rows in (over, short, bad, empty, gloss, nums) for r in rows}
        for b in beats:
            if b["index"] in flagged:
                print(f"\n#{b['index']}  [{b['source'] or '(silent)'}]")
                print(f"   {lines.get(b['index'], '')}")


def main() -> None:
    spec = args()[0]
    parts = ([int(spec)] if "-" not in spec
             else list(range(int(spec.split("-")[0]), int(spec.split("-")[1]) + 1)))
    job = job_dir()
    gfile = job / "glossary.yaml"
    glossary = yaml.safe_load(gfile.read_text(encoding="utf-8")) if gfile.exists() else {}
    glossary = {str(k): str(v) for k, v in (glossary or {}).items()}
    for n in parts:
        audit(job, n, glossary, "--lines" in sys.argv)


main()
