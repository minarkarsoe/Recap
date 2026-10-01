"""Donghua Burmese voice-over pipeline.

    python main.py --url "https://www.youtube.com/watch?v=..." --ratio 16:9
    python main.py --url "..." --stop-after script      # stop to review the Burmese script
    python main.py --job VIDEO_ID                      # continue (picks up script edits)
    python main.py --video ep01.mp4 --subs ep01.srt --ratio both
    python main.py --job VIDEO_ID --parts 1           # long video: just the first ~20 min part
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src import downloader
from src.config import load_config, resolve
from src.pipeline import STAGES, Pipeline


def setup_logging(log_file: Path | None = None, verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    if not root.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        root.addHandler(console)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_parts(text: str) -> set[int]:
    parts: set[int] = set()
    for chunk in text.split(","):
        a, _, b = chunk.strip().partition("-")
        parts.update(range(int(a), int(b or a) + 1))
    return parts


def parse_time(text: str) -> float:
    """Seconds from "90", "1:30" or "1:12:23"."""
    parts = [float(p) for p in text.strip().split(":")]
    if not 1 <= len(parts) <= 3:
        raise argparse.ArgumentTypeError(f"not a time: {text}")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def main() -> int:
    parser = argparse.ArgumentParser(description="Burmese storytelling voice-over for donghua videos")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="YouTube video URL")
    src.add_argument("--video", type=Path, help="local video file")
    src.add_argument("--job", help="continue an existing job (folder name under work/)")
    parser.add_argument("--subs", type=Path, help="subtitle file (.srt/.vtt) to use instead of downloaded ones")
    parser.add_argument("--ratio", choices=["16:9", "9:16", "both"], default="16:9")
    parser.add_argument("--parts", type=parse_parts, help="only these parts of a long video, e.g. 1 or 1-3 or 2,5")
    parser.add_argument("--stop-after", choices=STAGES[:-1], help="stop after this stage")
    parser.add_argument("--from-stage", choices=STAGES, help="redo this stage and everything after it")
    parser.add_argument("--config", type=Path, help="settings file (default config/settings.yaml)")
    parser.add_argument("--model", help="Ollama model to use instead of llm.model (handy for comparing models)")
    parser.add_argument("--speed", type=float,
                        help="playback speed for this parse instead of video.speed; a faster video has "
                             "shorter narration slots, so a talky show needs less narration per beat")
    parser.add_argument("--part-minutes", type=float, metavar="MINUTES",
                        help="part length for this parse instead of pipeline.part_minutes; a value longer "
                             "than the video keeps it whole, for a recap meant to be posted in one piece")
    parser.add_argument("--until", type=parse_time, metavar="TIME",
                        help="stop at this point in the SOURCE video (e.g. 1:12:23), ignoring the rest; "
                             "for an upload that carries a second, unrelated show after the first")
    parser.add_argument("--mirror", action="store_true",
                        help="flip the picture left-to-right, under the logo, so the upload is not a "
                             "frame-for-frame copy of the source; burned-in text reads backwards")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    setup_logging(verbose=args.verbose)
    log = logging.getLogger("main")
    cfg = load_config(args.config)
    if args.model:
        cfg["llm"]["model"] = args.model
    if args.speed:
        cfg["video"]["speed"] = args.speed
    if args.mirror:
        cfg["video"]["mirror"] = True
    if args.until:
        cfg["video"]["until"] = args.until
    if args.part_minutes:
        cfg["pipeline"]["part_minutes"] = args.part_minutes
    work_root = resolve(cfg["paths"]["work_dir"])
    work_root.mkdir(parents=True, exist_ok=True)

    try:
        if args.url:
            job_dir = downloader.download(args.url, work_root, cfg)
        elif args.video:
            job_dir = downloader.register_local(args.video, args.subs, work_root)
        else:
            job_dir = work_root / args.job
            if not (job_dir / "meta.json").exists():
                log.error("No job at %s", job_dir)
                return 1
        if args.subs and not args.video:
            downloader.attach_subs(job_dir, args.subs)

        setup_logging(job_dir / "pipeline.log", args.verbose)
        ratios = ["16:9", "9:16"] if args.ratio == "both" else [args.ratio]
        Pipeline(cfg, job_dir, args.from_stage, args.stop_after, ratios, args.parts).run()
        return 0
    except KeyboardInterrupt:
        log.warning("Interrupted. Run again with --job to continue where it stopped.")
        return 130
    except Exception as exc:
        log.error("%s", exc)
        log.debug("Traceback", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
