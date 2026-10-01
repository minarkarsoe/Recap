"""Shared bits for the proofreading helpers: which job, which part, and the video speed.

The job is picked, in order, from ``--job <folder>``, the RECAP_JOB environment variable, or the
newest folder under work/ that has a beats.json. That way a fresh video needs no edits here.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"


def job_dir(argv: list[str] | None = None) -> Path:
    argv = sys.argv if argv is None else argv
    if "--job" in argv:
        return WORK / argv[argv.index("--job") + 1]
    if os.environ.get("RECAP_JOB"):
        return WORK / os.environ["RECAP_JOB"]
    jobs = [p for p in WORK.iterdir() if (p / "beats.json").exists()] if WORK.exists() else []
    if not jobs:
        sys.exit(f"no job with a beats.json under {WORK}")
    return max(jobs, key=lambda p: p.stat().st_mtime)


def args(argv: list[str] | None = None) -> list[str]:
    """Positional arguments, with --job and its value removed."""
    argv = (sys.argv if argv is None else argv)[1:]
    if "--job" in argv:
        i = argv.index("--job")
        argv = argv[:i] + argv[i + 2:]
    return [a for a in argv if not a.startswith("--")]


def part_dir(job: Path, n: int) -> Path:
    return job / "parts" / f"p{n:02d}"


def beats_of(job: Path, n: int) -> list[dict]:
    """The beats of one part, with the budgets the script stage wrote.

    Before that stage has run there is no script.json, so the budgets are recomputed from
    beats.json the same way it would: that lets the lines be written and checked while the
    drafts are still being generated.
    """
    script = part_dir(job, n) / "script.json"
    if script.exists():
        return json.loads(script.read_text(encoding="utf-8"))["beats"]
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))["tts"]
    rate, base, top = cfg["syllables_per_second"], cfg.get("base_tempo", 1.0), cfg["max_tempo"]
    data = json.loads((job / "beats.json").read_text(encoding="utf-8"))
    part = next(p for p in data["parts"] if p["index"] == n)
    beats = [b for b in data["beats"] if part["start"] <= b["start"] < part["end"]]
    for b in beats:
        b["target_syllables"] = max(3, int(b["slot"] * rate * base * 0.95))
        b["max_syllables"] = max(b["target_syllables"], int(b["slot"] * rate * top * 0.9))
    return beats


def video_speed() -> float:
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    return float(cfg["video"]["speed"])


def utf8_stdout() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
