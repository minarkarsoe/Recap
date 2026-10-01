"""Thin wrappers around the ffmpeg and ffprobe command-line tools."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def run(cmd: list[str], desc: str = "") -> subprocess.CompletedProcess:
    """Run a command, raising FFmpegError with the tail of stderr on failure."""
    log.debug("run: %s", " ".join(map(str, cmd)))
    proc = subprocess.run(
        [str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"{desc or cmd[0]} failed (exit {proc.returncode}):\n{tail}")
    return proc


def run_with_progress(cmd: list[str], total_seconds: float, desc: str) -> None:
    """Run a long ffmpeg job, logging progress every 10%."""
    cmd = [str(c) for c in cmd]
    cmd[1:1] = ["-progress", "pipe:1", "-nostats"]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8", errors="replace")
        shown = 0
        for line in proc.stdout:
            if line.startswith("out_time_us=") and total_seconds > 0:
                try:
                    pct = int(line.split("=", 1)[1]) / 1e6 / total_seconds * 100
                except ValueError:
                    continue
                if pct >= shown + 10:
                    shown = int(pct // 10 * 10)
                    log.info("%s: %d%%", desc, min(shown, 100))
        proc.wait()
        if proc.returncode != 0:
            err.seek(0)
            tail = "\n".join(err.read().strip().splitlines()[-15:])
            raise FFmpegError(f"{desc} failed (exit {proc.returncode}):\n{tail}")


def probe_duration(path: str | Path) -> float:
    proc = run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        f"ffprobe {Path(path).name}",
    )
    return float(json.loads(proc.stdout)["format"]["duration"])


def probe_fps(path: str | Path) -> str:
    """Nominal video frame rate, e.g. '30/1' or '30000/1001'.

    Prefers r_frame_rate; averages over a whole file come out as odd fractions such as
    231902500/7730083, so the result is snapped to a denominator of at most 1001.
    """
    proc = run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate,avg_frame_rate",
         "-of", "default=nw=1", path],
        f"ffprobe {Path(path).name}",
    )
    rates = dict(line.split("=", 1) for line in proc.stdout.strip().splitlines() if "=" in line)
    for key in ("r_frame_rate", "avg_frame_rate"):
        try:
            fps = Fraction(rates.get(key, "0/1"))
        except (ValueError, ZeroDivisionError):
            continue
        if 1 <= fps <= 240:
            return str(fps.limit_denominator(1001))
    return "30"


def has_audio(path: str | Path) -> bool:
    proc = run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
        f"ffprobe {Path(path).name}",
    )
    return bool(proc.stdout.strip())


def stretch(src: Path, dst: Path, tempo: float, sample_rate: int, filter_name: str = "rubberband") -> None:
    """Change speech tempo without changing pitch (tempo > 1 means faster)."""
    af = f"rubberband=tempo={tempo:.4f}" if filter_name == "rubberband" else f"atempo={tempo:.4f}"
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", af, "-ar", sample_rate, "-ac", 1, "-c:a", "pcm_f32le", dst],
        f"stretch {src.name}")


def extract_audio(src: Path, dst: Path, sample_rate: int, speed: float = 1.0,
                  start: float | None = None, duration: float | None = None) -> None:
    """Decode audio to a stereo WAV, sped up to match the output video when speed != 1.

    ``start`` and ``duration`` select a stretch of the source, in source seconds.
    """
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.6f}"]
    if duration:
        cmd += ["-t", f"{duration:.6f}"]
    cmd += ["-i", src, "-vn"]
    if abs(speed - 1.0) > 1e-3:
        cmd += ["-af", f"atempo={speed:.4f}"]
    cmd += ["-ar", sample_rate, "-ac", 2, "-c:a", "pcm_s16le", dst]
    run(cmd, f"extract audio from {src.name}")


def extract_frames(src: Path, out_dir: Path, start: float, duration: float, width: int = 448) -> None:
    """Save one small JPEG per second of source time [start, start + duration] as 00001.jpg, ..."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src,
         "-vf", f"fps=1,scale={width}:-2", "-q:v", "5", out_dir / "%05d.jpg"],
        f"extract frames from {src.name}")


def input_args(paths: list[Path], list_file: Path) -> list[str]:
    """ffmpeg input arguments for one file, or for several joined with the concat demuxer."""
    if len(paths) == 1:
        return ["-i", str(paths[0])]
    lines = ["file '{}'".format(str(p.resolve()).replace("\\", "/").replace("'", r"'\''")) for p in paths]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ["-f", "concat", "-safe", "0", "-i", str(list_file)]


def measure_loudness(inputs: list[str], target: float) -> dict:
    """First loudnorm pass: measure the audio so the second pass can normalize linearly.

    ``inputs`` are ffmpeg input arguments (see input_args).
    """
    proc = run(
        ["ffmpeg", "-hide_banner", "-nostats", *inputs, "-vn",
         "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        "measure loudness",
    )
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not match:
        raise FFmpegError("loudnorm did not report measurements")
    return json.loads(match.group(0))


def loudnorm_filter(measured: dict, target: float) -> str:
    return (
        f"loudnorm=I={target}:TP=-1.5:LRA=11"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )
