"""Parse SRT/VTT subtitles and group lines into narration beats."""
from __future__ import annotations

import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

Segment = dict[str, Any]

_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_CUE = re.compile(rf"({_TIME.pattern})\s*-->\s*({_TIME.pattern})")
_TAGS = re.compile(r"<[^>]*>|\{[^}]*\}")
_NOISE = re.compile(r"^[\s\-–—♪♫~*.。…]+$")
# YouTube's auto-captions scroll: a line appears as the new bottom line of one cue and again as
# the carried top line of the next. Two is the height of that window, so looking that far back
# drops the repeat while leaving a line a speaker really says three times in a row alone.
ROLL_LOOKBACK = 2
ROLL_MAX_GAP = 0.5  # seconds; a line coming back later than this is said again, not scrolled


def _seconds(stamp: str) -> float:
    h, m, s, ms = _TIME.match(stamp).groups()
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def _clean(text: str) -> str:
    text = _TAGS.sub("", text).replace("\\N", " ").replace("\\n", " ")
    text = re.sub(r"^\s*-\s*", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    return "" if _NOISE.match(text or " ") else text


def _unroll(cues: list[dict[str, Any]]) -> list[Segment]:
    """Emit every caption line once, timed by the cue that first showed it.

    Auto-captions repeat each line in the following cue (see ROLL_LOOKBACK), so joining a cue's
    lines into one text would say everything twice. Where a cue does introduce several new lines
    at once, its span is shared out between them in proportion to their length.

    A cue that carries nothing new is the same line re-cued, as ordinary subtitles do to hold a
    line on screen, so it extends that line instead of being dropped.
    """
    out: list[Segment] = []
    recent: list[Segment] = []
    for cue in cues:
        window = [s for s in recent[-ROLL_LOOKBACK:] if cue["start"] - s["end"] <= ROLL_MAX_GAP]
        texts = [t for t in (_clean(line) for line in cue["lines"]) if t]
        fresh = [t for t in texts if not any(t == s["text"] for s in window)]
        if not fresh:
            for s in window:
                if s["text"] in texts:
                    s["end"] = max(s["end"], cue["end"])
            continue
        widths = [len(t) for t in fresh]
        span, total, at = max(cue["end"] - cue["start"], 0.0), sum(widths), cue["start"]
        for text, w in zip(fresh, widths):
            seg = {"start": at, "end": at + span * w / total, "text": text}
            out.append(seg)
            recent.append(seg)
            at = seg["end"]
    return out


def parse_subtitles(path: str | Path) -> list[Segment]:
    """Read an .srt or .vtt file into [{index, start, end, text}], sorted and de-duplicated."""
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    lines = raw.split("\n")
    cues: list[Segment] = []
    current: dict[str, Any] | None = None
    # Line by line rather than by blank-line blocks: SRTs converted from YouTube auto-captions
    # put an empty line between the timing and the text.
    for i, line in enumerate(lines):
        m = _CUE.search(line)
        if m:
            current = {"start": _seconds(m.group(1)), "end": _seconds(m.group(6)), "lines": []}
            cues.append(current)
        elif current is not None and line.strip():
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if line.strip().isdigit() and _CUE.search(nxt):
                continue  # the next cue's number
            if line.startswith(("NOTE", "STYLE")):
                current = None
                continue
            current["lines"].append(line)
    cues.sort(key=lambda c: c["start"])
    cues = _unroll(cues)

    # Auto-generated captions repeat a line across consecutive cues; fold those together.
    merged: list[Segment] = []
    for cue in cues:
        prev = merged[-1] if merged else None
        if prev and cue["text"] == prev["text"] and cue["start"] <= prev["end"] + 0.5:
            prev["end"] = max(prev["end"], cue["end"])
        elif prev and cue["text"].startswith(prev["text"]) and cue["start"] <= prev["end"] + 0.1:
            prev["text"], prev["end"] = cue["text"], max(prev["end"], cue["end"])
        else:
            merged.append(dict(cue))
    for i, seg in enumerate(merged):
        seg["index"] = i
        if seg["end"] <= seg["start"]:
            seg["end"] = seg["start"] + 1.0
    return [{"index": s["index"], "start": round(s["start"], 3), "end": round(s["end"], 3), "text": s["text"]}
            for s in merged]


def split_parts(beats: list[Segment], total: float, fps: Fraction, part_seconds: float,
                search: float = 120.0) -> list[Segment]:
    """Cut a long timeline into parts of about ``part_seconds`` (output-video seconds).

    Each cut lands in the widest pause between beats within ``search`` seconds of the target, so no
    narration straddles two parts, and on a frame boundary, so parts rendered separately add up to
    exactly the right length when joined.
    """
    cuts: list[int] = []
    target = part_seconds
    search = min(search, part_seconds * 0.25)
    while total - target > part_seconds * 0.4:
        best: tuple[float, float] | None = None
        for a, b in zip(beats, beats[1:]):
            gap_start, gap_end = a["start"] + a["slot"], b["start"]
            mid = (gap_start + gap_end) / 2
            if abs(mid - target) <= search and (best is None or gap_end - gap_start > best[0]):
                best = (gap_end - gap_start, mid)
        cut = best[1] if best else target
        frame = round(cut * fps)
        if cuts and frame <= cuts[-1]:
            break
        cuts.append(frame)
        target = frame / fps + part_seconds

    frames = [0, *cuts]
    parts = []
    for i, first in enumerate(frames):
        last = frames[i + 1] if i + 1 < len(frames) else None
        parts.append({
            "index": i + 1,
            "start_frame": first,
            "frames": None if last is None else last - first,
            "start": round(float(first / fps), 6),
            "end": round(float(last / fps), 6) if last is not None else round(total, 6),
        })
    return parts


def build_beats(segments: list[Segment], duration: float, cfg: dict[str, Any], speed: float = 1.0) -> list[Segment]:
    """Group subtitle lines into beats and give each beat a narration slot.

    Times in the result are output-video times (source time / speed). ``slot`` is how long
    the narration may last: from the beat's start until the next beat starts (minus a small
    pad), but never more than ``max_overhang`` past the beat's last line.

    Stretches with no dialogue longer than ``visual_gap`` become "visual" beats of at most
    ``visual_beat`` seconds (no subtitles), so the narrator can describe the action there too.
    """
    groups: list[list[Segment]] = []
    for seg in segments:
        cur = groups[-1] if groups else None
        if (cur and seg["start"] - cur[-1]["end"] <= cfg["max_gap"]
                and seg["end"] - cur[0]["start"] <= cfg["max_beat"]):
            cur.append(seg)
        else:
            groups.append([seg])

    end_of_video = duration / speed
    spans = [{"start": g[0]["start"] / speed, "end": g[-1]["end"] / speed,
              "source": " ".join(s["text"] for s in g), "kind": "dialogue"} for g in groups]

    visual_gap = cfg.get("visual_gap")
    if visual_gap:
        # Leave room after each line for its narration to run on before a visual beat begins.
        reserve, size = cfg.get("visual_reserve", 2.5), cfg.get("visual_beat", 8.0)
        edges = [0.0] + [s["end"] + reserve for s in spans]
        nexts = [s["start"] - cfg["tail_pad"] for s in spans] + [end_of_video]
        for a, b in zip(edges, nexts):
            if b - a >= visual_gap:
                n = math.ceil((b - a) / size)
                step = (b - a) / n
                spans += [{"start": a + k * step, "end": a + (k + 1) * step, "source": "", "kind": "visual"}
                          for k in range(n)]
        spans.sort(key=lambda s: s["start"])

    beats: list[Segment] = []
    for i, span in enumerate(spans):
        nxt = spans[i + 1]["start"] if i + 1 < len(spans) else end_of_video
        if span["kind"] == "visual":
            slot_end = min(nxt - cfg["tail_pad"], span["end"])
        else:
            slot_end = min(nxt - cfg["tail_pad"], span["end"] + cfg["max_overhang"])
        beats.append({
            "index": i,
            "kind": span["kind"],
            "start": round(span["start"], 3),
            "end": round(span["end"], 3),
            "slot": round(max(slot_end - span["start"], 0.5), 3),
            "source": span["source"],
        })
    return beats
