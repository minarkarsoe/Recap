"""End-to-end orchestration. Every step saves its result under work/<id>/ so a run can resume.

Long videos are handled in parts of about ``pipeline.part_minutes``: each part gets its own
script, narration audio and rendered video (with a preview in output/<title>/), and the finished
parts are joined into one video at the end.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from . import downloader, media, timeline
from .burmese import clean_narration, count_syllables, spell_numbers
from .config import load_glossary, resolve
from .srt_parser import build_beats, parse_subtitles, split_parts
from .story_rewriter import (OllamaClient, build_context, enforce_budget, gloss_beats,
                            language_of, merge_names, polish,
                             shorten, summary_for, write_narration)
from .tts_voxcpm import VoxCPM2TTS
from .video_composer import build_mix, fit_clips, mux, render_part, write_srt

log = logging.getLogger(__name__)

STAGES = ["parse", "context", "script", "tts", "compose"]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _safe_name(title: str, fallback: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().rstrip(".")
    return name[:80] or fallback


def _tag(ratio: str) -> str:
    return ratio.replace(":", "x")


class Pipeline:
    def __init__(self, cfg: dict[str, Any], job_dir: Path, from_stage: str | None = None,
                 stop_after: str | None = None, ratios: list[str] | None = None,
                 only_parts: set[int] | None = None):
        self.cfg = cfg
        self.job = job_dir
        self.meta = downloader.load_meta(job_dir)
        self.force_from = STAGES.index(from_stage) if from_stage else len(STAGES)
        self.stop_at = STAGES.index(stop_after) if stop_after else len(STAGES) - 1
        self.ratios = ratios or ["16:9"]
        self.only_parts = only_parts
        self.speed = cfg["video"]["speed"]
        self.until = cfg["video"].get("until") or None
        # meta["title"] stays the source's own title (the prompts rely on its spellings); the
        # output is named after meta["my_title"] when one is set, so the folders read in Burmese.
        self.name = _safe_name(self.meta.get("my_title") or self.meta["title"], self.meta["id"])
        self.out_dir = resolve(cfg["paths"]["output_dir"])
        self._llm: OllamaClient | None = None

    def path(self, name: str) -> Path:
        return self.job / name

    @property
    def out_folder(self) -> Path:
        """One folder per video, holding the joined file, its subtitles and the part previews."""
        return self.out_dir / self.name

    @property
    def source_length(self) -> float:
        """How much of the source video this job covers, in source seconds.

        A single upload sometimes carries two unrelated shows one after the other. ``until`` cuts
        the job off where the first one ends, so the second never reaches the beats, the script or
        the render.
        """
        return min(self.meta["duration"], self.until) if self.until else self.meta["duration"]

    def _stale(self, stage: str, outputs: list[Path], inputs: list[Path]) -> bool:
        """Redo a step if forced, if an output is missing, or if an input changed since (e.g. an edited script)."""
        if STAGES.index(stage) >= self.force_from or not all(p.exists() for p in outputs):
            return True
        made = min(p.stat().st_mtime for p in outputs)
        return any(p.exists() and p.stat().st_mtime > made for p in inputs)

    def _stop(self, stage: str) -> bool:
        return STAGES.index(stage) >= self.stop_at

    @property
    def llm(self) -> OllamaClient:
        if self._llm is None:
            # Prompts are worded around the subtitles the model actually reads, whatever language
            # this show ships; llm.source_language only covers a track attached by hand.
            seg = self.path("segments.json")
            lang = language_of(_load(seg) if seg.exists() else [], self.meta.get("subs_lang"),
                               self.cfg["llm"].get("source_language", "Chinese"))
            self._llm = OllamaClient({**self.cfg["llm"], "source_language": lang})
            self._llm.check()
            log.info("Reading %s subtitles", lang)
        return self._llm

    def _free_llm(self) -> None:
        if self._llm is not None:
            self._llm.unload()  # VoxCPM2 needs the VRAM

    # -- driver ---------------------------------------------------------------------------

    def run(self) -> list[Path]:
        m = self.meta
        log.info("Job %s: %s (%s)", m["id"], m["title"], time.strftime("%H:%M:%S", time.gmtime(m["duration"])))
        subs = [Path(m["subs"])] if m.get("subs") else []
        if self._stale("parse", [self.path("beats.json")], subs):
            log.info("== parse ==")
            self.parse()
        if not self._stop("parse") and self._stale("context", [self.path("context.json")], subs):
            log.info("== context ==")
            self.context()
        if self._stop("context"):
            return self._stopped(STAGES[self.stop_at])

        data = _load(self.path("beats.json"))
        if abs(data["speed"] - self.speed) > 1e-6:
            # The beats were cut for the speed this job was parsed at, so that is the job's speed
            # from here on. video.speed only picks the default for the next parse.
            log.info("Using this job's parse-time speed %.2f (video.speed is %.2f)",
                     data["speed"], self.speed)
            self.speed = data["speed"]
            # render_part and _video_graph read the speed from cfg["video"], not from self.speed,
            # so it has to be corrected here too. Without this the picture is rendered at the
            # config's speed while the soundtrack is built at the job's: the video falls further
            # behind the narration every second, and the tail of the film never gets rendered.
            self.cfg["video"]["speed"] = self.speed
        if data.get("until") != self.until:
            # Same reasoning as the speed above: the beats already end where the parse cut them.
            self.until = data.get("until")
        parts = data["parts"]
        todo = [p for p in parts if not self.only_parts or p["index"] in self.only_parts]
        for part in todo:
            self.run_part(part, data)
        self._free_llm()
        if self._stop("tts"):
            return self._stopped(STAGES[self.stop_at])

        rendered = all(self._part_done(p) for p in parts)
        if not rendered:
            done = sum(1 for p in parts if self._part_done(p))
            log.info("%d/%d parts rendered. Run again without --parts to finish the rest and join them.",
                     done, len(parts))
            return []
        return self.finish(parts)

    def _stopped(self, stage: str) -> list[Path]:
        log.info("Stopped after %s. Files are in %s", stage, self.job)
        if stage == "script":
            log.info("Review or edit work\\%s\\parts\\pNN\\script.txt, then continue with: "
                     "python main.py --job %s", self.meta["id"], self.meta["id"])
        return []

    # -- script.txt: the human-editable copy of a part's narration -------------------------

    def _export_txt(self, part: dict, script: dict) -> None:
        """Write script.txt and remember its hash, so only real edits are imported back."""
        lines = [f"# Part {part['index']} narration. Edit the Burmese lines, save, then run:",
                 f"#   python main.py --job {self.meta['id']}",
                 "# Keep the #number header lines as they are. An empty line under a header = no narration.",
                 ""]
        for b in script["beats"]:
            stamp = time.strftime("%H:%M:%S", time.gmtime(b["start"]))
            lines += [f"#{b['index']} [{stamp}] {b['source'] or '(no dialogue)'}", b["narration"], ""]
        text = "\n".join(lines)
        (self.part_dir(part) / "script.txt").write_text(text, encoding="utf-8")
        script["txt_sha1"] = hashlib.sha1(text.encode("utf-8")).hexdigest()
        _save(self.part_dir(part) / "script.json", script)

    def _import_txt(self, part: dict) -> bool:
        """Apply edits made in script.txt to script.json. Returns True if anything changed."""
        d = self.part_dir(part)
        txt, js = d / "script.txt", d / "script.json"
        if not txt.exists() or not js.exists():
            return False
        text = txt.read_text(encoding="utf-8-sig")
        script = _load(js)
        if hashlib.sha1(text.encode("utf-8")).hexdigest() == script.get("txt_sha1"):
            return False
        edited: dict[int, list[str]] = {}
        current: int | None = None
        for line in text.splitlines():
            if m := re.match(r"^#(\d+) \[", line):
                current = int(m.group(1))
                edited[current] = []
            elif current is not None and not line.startswith("#"):
                edited[current].append(line.strip())
        changed = 0
        for b in script["beats"]:
            if b["index"] in edited:
                new = clean_narration(spell_numbers(" ".join(x for x in edited[b["index"]] if x)))
                if new != b["narration"]:
                    b["narration"] = new
                    b["edited"] = True
                    changed += 1
        script["txt_sha1"] = hashlib.sha1(text.encode("utf-8")).hexdigest()
        _save(js, script)
        log.info("Part %d: took %d edited line(s) from script.txt", part["index"], changed)
        return changed > 0

    def part_dir(self, part: dict) -> Path:
        return self.path(f"parts/p{part['index']:02d}")

    def _part_done(self, part: dict) -> bool:
        d = self.part_dir(part)
        return (d / "mix.wav").exists() and all((d / f"video_{_tag(r)}.mp4").exists() for r in self.ratios)

    def run_part(self, part: dict, data: dict) -> None:
        d = self.part_dir(part)
        n = len(data["parts"])
        label = f"part {part['index']}/{n}" if n > 1 else "video"
        beats = [b for b in data["beats"] if part["start"] <= b["start"] < part["end"]]
        span = f"{time.strftime('%H:%M:%S', time.gmtime(part['start']))}-" \
               f"{time.strftime('%H:%M:%S', time.gmtime(part['end']))}"
        t0 = time.time()

        if self._stale("script", [d / "script.json"], [self.path("beats.json"), self.path("context.json")]):
            log.info("== script: %s (%s, %d beats) ==", label, span, len(beats))
            self.script(part, beats)
        if self._stop("script"):
            return
        self._import_txt(part)
        if self._stale("tts", [d / "timeline.json"], [d / "script.json"]):
            log.info("== tts: %s ==", label)
            self._free_llm()
            self.tts(part)
        if self._stop("tts"):
            return
        videos = [d / f"video_{_tag(r)}.mp4" for r in self.ratios]
        if self._stale("compose", [d / "mix.wav", *videos], [d / "timeline.json"]):
            log.info("== compose: %s ==", label)
            self.compose(part, n)
        log.info("%s finished in %.1f min", label.capitalize(), (time.time() - t0) / 60)

    # -- stages ---------------------------------------------------------------------------

    def parse(self) -> None:
        if not self.meta.get("subs"):
            raise RuntimeError(
                "This video has no subtitles. Supply one with --subs file.srt "
                f"(e.g. python main.py --job {self.meta['id']} --subs subs.srt)."
            )
        segments = parse_subtitles(self.meta["subs"])
        if not segments:
            raise RuntimeError(f"No subtitle lines found in {self.meta['subs']}")
        length = self.source_length
        if self.until:
            segments = [s for s in segments if s["start"] < length]
            log.info("Using the first %s of the video (%d subtitle lines)",
                     time.strftime("%H:%M:%S", time.gmtime(length)), len(segments))
            if not segments:
                raise RuntimeError(f"No subtitle lines before --until {length:.0f}s")
        total = length / self.speed
        beats = build_beats(segments, length, self.cfg["chunking"], self.speed)
        fps = Fraction(media.probe_fps(self.meta["video"]))
        parts = split_parts(beats, total, fps, self.cfg["pipeline"]["part_minutes"] * 60)
        _save(self.path("segments.json"), segments)
        _save(self.path("beats.json"), {"speed": self.speed, "until": self.until, "fps": str(fps),
                                        "parts": parts, "beats": beats})
        log.info("%d subtitle lines -> %d beats in %d part(s)", len(segments), len(beats), len(parts))

    def _glossary(self) -> dict[str, str]:
        """The shared spellings, then this job's own on top.

        Names belong to one show, so each job keeps its own glossary.yaml and only genuinely
        general terms live in the shared file. Otherwise a name fixed for one series would be
        forced onto the next one that happens to use the same characters.
        """
        return {**load_glossary(self.cfg["llm"].get("glossary")),
                **load_glossary(self.path("glossary.yaml"))}

    def context(self) -> None:
        ctx = build_context(self.llm, _load(self.path("segments.json")), self._glossary())
        _save(self.path("context.json"), ctx)
        log.info("Names (%d): %s", len(ctx["names"]),
                 ", ".join(f"{n['source']}={n['burmese']}" for n in ctx["names"][:40]) or "(none)")

    def _part_context(self, part: dict) -> dict:
        ctx = _load(self.path("context.json"))
        # Merge the glossary again so edits to it apply without re-reading the whole story.
        names = merge_names(ctx["names"], self._glossary())
        return {"summary": summary_for(ctx, part["start"] * self.speed, part["end"] * self.speed),
                "names": names}

    def _frame_picker(self, part: dict) -> Callable[[dict], list[Path]] | None:
        """Frames the LLM looks at for each beat (one per second of video is extracted per part)."""
        llm = self.cfg["llm"]
        if not llm.get("vision"):
            return None
        d = self.part_dir(part) / "frames"
        src_start = part["start"] * self.speed
        src_length = (part["end"] - part["start"]) * self.speed
        # Frames are numbered from the part's own start, so a re-parse that moves the part
        # boundaries leaves every frame pointing at the wrong moment. Keep the range they were
        # cut for and re-cut when it no longer matches.
        stamp = d / "range.json"
        want = {"start": round(src_start, 3), "length": round(src_length, 3),
                "width": llm.get("frame_width", 448)}
        if not (d / "00001.jpg").exists() or not stamp.exists() or _load(stamp) != want:
            log.info("Extracting frames for the model to look at...")
            for old in d.glob("*.jpg"):
                old.unlink()
            media.extract_frames(Path(self.meta["video"]), d, src_start, src_length, want["width"])
            _save(stamp, want)
        count = len(list(d.glob("*.jpg")))

        def pick(beat: dict) -> list[Path]:
            length = beat["end"] - beat["start"]
            shots: list[Path] = []
            for f in ([0.5] if length < 2.5 else [0.3, 0.75]):
                k = min(max(round((beat["start"] + f * length) * self.speed - src_start), 0), count - 1)
                if (p := d / f"{k + 1:05d}.jpg") not in shots:
                    shots.append(p)
            return shots
        return pick

    def script(self, part: dict, beats: list[dict]) -> None:
        llm, tcfg = self.cfg["llm"], self.cfg["tts"]
        context = self._part_context(part)
        frames = self._frame_picker(part)
        batch_size = llm.get("batch_size", 8)
        gloss_beats(self.llm, beats, context, frames=frames, batch_size=batch_size)
        beats = write_narration(
            self.llm, beats, context, rate=tcfg["syllables_per_second"],
            base_tempo=tcfg.get("base_tempo", 1.0), max_tempo=tcfg["max_tempo"],
            batch_size=batch_size, max_retries=llm.get("max_retries", 3), frames=frames,
        )
        changed = polish(self.llm, beats, context)
        log.info("Editor pass changed %d line(s)", changed)
        enforce_budget(self.llm, beats, context, tolerance=1.1)
        self._export_txt(part, {"part": part, "beats": beats})
        spoken = sum(1 for b in beats if b["narration"])
        log.info("Narration written for %d/%d beats -> %s", spoken, len(beats), self.part_dir(part) / "script.txt")

    def tts(self, part: dict) -> None:
        tcfg = self.cfg["tts"]
        script_file = self.part_dir(part) / "script.json"
        script = _load(script_file)
        beats = script["beats"]
        tts = VoxCPM2TTS(tcfg)
        tts_dir = self.path("tts")
        tts.ensure_voice(tts_dir)
        max_tempo = tcfg["max_tempo"]

        for round_no in range(tcfg["shorten_retries"] + 1):
            items = []
            for b in beats:
                b.pop("clip", None)
                b.pop("duration", None)
                if b["narration"]:
                    b["clip"] = str(tts.clip_path(tts_dir, b["index"], b["narration"]))
                    items.append({"id": b["index"], "text": b["narration"], "out": b["clip"]})
            self._free_llm()
            durations = tts.synthesize(items, tts_dir)
            for b in beats:
                if b["index"] in durations:
                    b["duration"] = round(durations[b["index"]], 3)

            # A little overrun is fine: the timeline absorbs it in the next pause. Lines edited by
            # hand in script.txt are never rewritten by the LLM; they just play fast or run late.
            too_long = [b for b in beats if b.get("duration", 0) > b["slot"] * max_tempo * 1.05
                        and not b.get("edited")]
            if not too_long or round_no == tcfg["shorten_retries"]:
                break
            log.info("%d line(s) too long for their slot; asking the LLM to shorten them", len(too_long))
            context = self._part_context(part)
            shortened = 0
            for b in too_long:
                room = b["slot"] * max_tempo * 0.95
                before = count_syllables(b["narration"])
                new = shorten(self.llm, b, context, max(3, math.floor(before * room / b["duration"])), b["slot"])
                if new and count_syllables(new) < before * 0.95:  # the model often "shortens" by nothing
                    log.info("Beat %d: %s -> %s", b["index"], b["narration"], new)
                    b["narration"] = new
                    shortened += 1
            if not shortened:
                break
            _save(script_file, script)

        self._export_txt(part, script)  # keeps script.txt in step with any shortened lines
        entries = timeline.plan(beats, max_tempo, tcfg.get("base_tempo", 1.0), tcfg.get("min_tempo"))
        _save(self.part_dir(part) / "timeline.json", {"entries": entries})
        sped = sum(1 for e in entries if e["tempo"] > 1.0)
        late = [e for e in entries if e["late"] > 0.3]
        log.info("Timeline: %d lines, %d sped up, %d running late", len(entries), sped, len(late))
        for e in late:
            log.info("  beat %d runs %.1fs past its slot", e["index"], e["late"])

    def _bgm_wav(self) -> Path | None:
        bgm = resolve(self.cfg["audio"].get("bgm_path"))
        if not bgm or not bgm.exists():
            return None
        wav = self.path("bgm.wav")
        if not wav.exists() or wav.stat().st_mtime < bgm.stat().st_mtime:
            media.extract_audio(bgm, wav, self.cfg["audio"]["sample_rate"])
        return wav

    def compose(self, part: dict, n_parts: int) -> None:
        acfg, vcfg = self.cfg["audio"], self.cfg["video"]
        sr = acfg["sample_rate"]
        d = self.part_dir(part)
        entries = _load(d / "timeline.json")["entries"]
        fit_clips(entries, self.path("tts_fit"), sr, self.cfg["tts"]["stretch_filter"])
        length = part["end"] - part["start"]

        source_wav = None
        if acfg.get("use_original", True) and self.meta.get("has_audio", True):
            source_wav = d / "source.wav"
            media.extract_audio(Path(self.meta["video"]), source_wav, sr, self.speed,
                                start=part["start"] * self.speed, duration=length * self.speed)
        bgm_wav = self._bgm_wav()
        parts = ["narration"] + (["source audio"] if source_wav else []) + (["BGM"] if bgm_wav else [])
        log.info("Mixing %s...", ", ".join(parts))
        build_mix(entries, d / "mix.wav", part["start"], part["end"], source_wav, bgm_wav, acfg)
        if source_wav:
            source_wav.unlink(missing_ok=True)

        fps = Fraction(_load(self.path("beats.json"))["fps"])
        for ratio in self.ratios:
            video = d / f"video_{_tag(ratio)}.mp4"
            render_part(Path(self.meta["video"]), video, ratio, vcfg, fps, part["start"], part["frames"],
                        length, f"Render {ratio}")
            if n_parts > 1:
                preview = self.out_folder / f"part{part['index']:02d} [{_tag(ratio)}].mp4"
                mux([video], [d / "mix.wav"], preview, acfg["loudness"], length, "Preview")
                log.info("Preview: %s", preview)

    def finish(self, parts: list[dict]) -> list[Path]:
        log.info("== join %d part(s) ==", len(parts))
        total = self.source_length / self.speed
        mixes = [self.part_dir(p) / "mix.wav" for p in parts]
        self.out_folder.mkdir(parents=True, exist_ok=True)
        outputs = []
        for ratio in self.ratios:
            dst = self.out_folder / f"{self.name} [{_tag(ratio)}].mp4"
            videos = [self.part_dir(p) / f"video_{_tag(ratio)}.mp4" for p in parts]
            mux(videos, mixes, dst, self.cfg["audio"]["loudness"], total, f"Join {ratio}")
            outputs.append(dst)
            log.info("Done: %s", dst)
        entries = [e for p in parts for e in _load(self.part_dir(p) / "timeline.json")["entries"]]
        srt = self.out_folder / f"{self.name}.my.srt"
        write_srt(entries, srt)
        log.info("Burmese narration subtitles: %s", srt)
        return outputs
