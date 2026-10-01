"""Place narration clips on the output timeline."""
from __future__ import annotations

from typing import Any

MIN_GAP = 0.1  # seconds between two narration lines


def plan(beats: list[dict[str, Any]], max_tempo: float, base_tempo: float = 1.0,
         min_tempo: float | None = None) -> list[dict[str, Any]]:
    """Decide where each clip starts and how fast it plays.

    Each beat has ``start``, ``slot`` (both output-video seconds), ``clip`` and ``duration``. A clip
    starts at its beat, or right after the previous clip if that one ran late. It plays at the
    tempo that makes it fill its slot, kept between ``min_tempo`` (short lines slow down a little
    instead of leaving silence; defaults to ``base_tempo``) and ``max_tempo``; anything still too
    long runs over and pushes the next line back a little.
    """
    floor = base_tempo if min_tempo is None else min_tempo
    entries = []
    cursor = 0.0
    for b in sorted(beats, key=lambda b: b["start"]):
        if not b.get("clip") or not b.get("duration"):
            continue
        place = max(b["start"], cursor + MIN_GAP if entries else 0.0)
        available = b["start"] + b["slot"] - place
        tempo = max_tempo if available <= 0 else min(max(b["duration"] / available, floor), max_tempo)
        tempo = 1.0 if tempo < 1.02 else round(tempo, 3)
        end = place + b["duration"] / tempo
        entries.append({
            "index": b["index"],
            "clip": b["clip"],
            "start": round(place, 3),
            "end": round(end, 3),
            "tempo": tempo,
            "late": round(max(0.0, end - (b["start"] + b["slot"])), 3),
            "text": b["narration"],
        })
        cursor = end
    return entries
