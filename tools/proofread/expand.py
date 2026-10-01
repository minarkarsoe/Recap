"""Lengthen a part's short narration lines with the local model, and write them as lines_pNN.py.

    python tools/proofread/expand.py 2                 # expand part 2, write lines_p02.py here
    python tools/proofread/expand.py 2 --tries 4       # more attempts per beat
    python tools/proofread/expand.py 2 --low 0.85      # what counts as short (fraction of target)
    python tools/proofread/expand.py 1-8               # a range of parts, one after another

Why this exists: the expansion passes are the bulk of proofreading and the least interesting
part of it. A hand-written Burmese line lands at 60-65% of its slot, so every part needs several
rounds of "say the same thing, but fuller", each measured against an exact syllable budget. That
is a loop a local model can run on its own -- it is scored, it is retried, and a candidate that
fails the rules or overshoots the maximum is simply thrown away.

What it does NOT do is judgement: who is speaking, whether the draft inverted a line's meaning,
whether a number is right. Those are what the hand pass is for, and check.py flags them.

The output is a lines_pNN.py in the current directory, exactly the file apply_proof.py already
reads, so the result can be reviewed, patched by hand and written the usual way.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

from common import ROOT, args, beats_of, job_dir, part_dir, utf8_stdout

utf8_stdout()
sys.path.insert(0, str(ROOT))
from src.burmese import clean_narration, count_syllables, spell_numbers  # noqa: E402
from src.config import load_config  # noqa: E402
from src.story_rewriter import (NARRATOR_RULES, SHORTEN_SCHEMA, LLMError, OllamaClient,  # noqa: E402
                                _finish, _glossary_text, _problem)

LONGER = (
    "The line is too short for its slot, so it will be followed by silence. Make it longer by "
    "saying more about what is already there -- name the speaker instead of saying 'he', add the "
    "gesture, the expression, the room, what the listener does -- and keep every fact of the "
    "original. Do not invent events that are not in the subtitles, do not repeat a clause, and do "
    "not add a comment about the story."
)
SHORTER = (
    "The line is too long for its slot, so it will be rushed or cut off. Make it "
    "shorter by dropping description, not facts -- every name, number and event in "
    "it must survive. Cut repeated clauses and scene-setting first."
)


def expand_one(client: OllamaClient, names, beat: dict, current: str, tries: int) -> tuple[str, int]:
    """Best candidate for one beat, as (line, syllables). Falls back to what came in."""
    target, top = beat["target_syllables"], beat["max_syllables"]
    best, best_syl = current, count_syllables(current)
    for attempt in range(tries):
        # An over-long line is the worse fault of the two -- it is rushed or clipped,
        # where a short one only leaves silence -- so it gets its own instruction.
        guide = SHORTER if best_syl > top else LONGER
        user = (
            f"Glossary:\n{_glossary_text(names)}\n\n"
            f"Original subtitles for this moment: {beat['source'] or '(no dialogue on screen)'}\n"
            f"Current narration ({best_syl} syllables): {best}\n\n"
            f"{guide}\n"
            f"Write about {round(target / 2.5)} words ({target} syllables), and never more than {round(top / 2.5)} words ({top} syllables). "
            f'Return JSON {{"narration": "..."}}.'
        )
        try:
            text = _finish(str(client.chat_json(client.rules(NARRATOR_RULES), user, SHORTEN_SCHEMA,
                                                temperature=0.6 + 0.1 * attempt).get("narration", "")))
        except LLMError as exc:
            print(f"    beat {beat['index']}: {exc}", flush=True)
            continue
        if not text:
            continue
        final = clean_narration(spell_numbers(text))
        syl = count_syllables(final)
        if _problem(final):
            continue
        if syl > top:
            # Nothing fits some very tight slots -- an 11-syllable maximum is four
            # words. Keep the least-over candidate so the beat still improves.
            if best_syl > top and syl < best_syl:
                best, best_syl = final, syl
            continue
        # Closer to target wins. best may itself be over max on the way in, so any
        # candidate that fits is an improvement even if it sits further from target.
        if best_syl > top or abs(syl - target) < abs(best_syl - target):
            best, best_syl = final, syl
        if 0.88 * target <= best_syl <= top:
            break
    return best, best_syl


def seed_lines(job: Path, n: int) -> dict[int, str]:
    """Where a pass starts from: the previous pass's lines_pNN.py if one is here,

    otherwise gemma's draft. Without this a second pass would throw away the first
    one's work and start over from the draft, and the stubborn beats -- the ones that
    need the extra tries -- are exactly the ones that would be reset.
    """
    f = Path.cwd() / f"lines_p{n:02d}.py"
    if f.exists():
        spec = importlib.util.spec_from_file_location(f"lines_p{n:02d}", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return dict(mod.L)
    script = part_dir(job, n) / "script.json"
    if not script.exists():
        return {}
    return {b["index"]: b.get("narration", "")
            for b in json.loads(script.read_text(encoding="utf-8"))["beats"]}


def run_part(client: OllamaClient, names, job: Path, n: int, low: float, tries: int) -> None:
    beats = beats_of(job, n)
    drafted = seed_lines(job, n)

    lines, touched, t0 = {}, 0, time.time()
    before = after = tgt = 0
    for b in beats:
        current = clean_narration(spell_numbers(drafted.get(b["index"], "")))
        syl = count_syllables(current)
        before += syl
        tgt += b["target_syllables"]
        if current and syl >= low * b["target_syllables"] and not _problem(current) \
                and syl <= b["max_syllables"]:
            lines[b["index"]] = current
            after += syl
            continue
        line, got = expand_one(client, names, b, current, tries)
        lines[b["index"]] = line
        after += got
        touched += 1
        if touched % 20 == 0:
            print(f"    {touched} rewritten, {time.time() - t0:.0f}s", flush=True)

    out = Path.cwd() / f"lines_p{n:02d}.py"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# Part {n} of {job.name}, expanded by the local model against each beat's\n"
                 f"# syllable budget. Review and correct by hand, then: apply_proof.py {n} --write\n"
                 "L = {\n")
        for b in beats:
            fh.write(f'    {b["index"]}: {json.dumps(lines[b["index"]], ensure_ascii=False)},'
                     f'  # {b["target_syllables"]} target\n')
        fh.write("}\n")
    print(f"  part {n}: {before}->{after} syllables vs target {tgt} "
          f"({before / tgt:.0%} -> {after / tgt:.0%}), {touched} rewritten, "
          f"{time.time() - t0:.0f}s -> {out.name}", flush=True)


def main() -> None:
    a = args()
    spec = a[0]
    parts = ([int(spec)] if "-" not in spec
             else list(range(int(spec.split("-")[0]), int(spec.split("-")[1]) + 1)))
    low = float(sys.argv[sys.argv.index("--low") + 1]) if "--low" in sys.argv else 0.85
    tries = int(sys.argv[sys.argv.index("--tries") + 1]) if "--tries" in sys.argv else 3

    job = job_dir()
    cfg = load_config()
    client = OllamaClient(cfg["llm"])
    client.check()
    names = json.loads((job / "context.json").read_text(encoding="utf-8")).get("names", [])
    print(f"{job.name}: parts {parts}, short below {low:.0%} of target, {tries} tries per beat")
    for n in parts:
        run_part(client, names, job, n, low, tries)
    client.unload()


main()
