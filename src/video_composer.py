"""Mix narration with the source audio and BGM, and render video with FFmpeg.

Long videos are rendered in parts: each part's video (no audio) and its mixed soundtrack are made
separately, then the parts are joined without re-encoding and the soundtrack is normalized once.
"""
from __future__ import annotations

import logging
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from . import media
from .config import resolve

log = logging.getLogger(__name__)

BLOCK_SECONDS = 10
MIX_HEADROOM = 0.5  # mixes are stored as 16-bit; loudnorm restores the level later


def fit_clips(entries: list[dict[str, Any]], fit_dir: Path, sample_rate: int, filter_name: str) -> None:
    """Speed up the clips the timeline asked for; sets entry['audio'] to the file to use."""
    fit_dir.mkdir(parents=True, exist_ok=True)
    for e in entries:
        src = Path(e["clip"])
        if e["tempo"] > 1.0:
            dst = fit_dir / f"{src.stem}_x{e['tempo']:.3f}.wav"
            if not dst.exists():
                media.stretch(src, dst, e["tempo"], sample_rate, filter_name)
            e["audio"] = str(dst)
        else:
            e["audio"] = str(src)


def _duck_curve(t: np.ndarray, windows: list[tuple[float, float]], fade: float) -> np.ndarray:
    """0 where nobody narrates, 1 under narration, with linear ramps of ``fade`` seconds."""
    duck = np.zeros_like(t)
    for s, e in windows:
        if e + fade < t[0] or s - fade > t[-1]:
            continue
        w = np.clip(np.minimum((t - (s - fade)) / fade, ((e + fade) - t) / fade), 0.0, 1.0)
        np.maximum(duck, w, out=duck)
    return duck


def build_mix(entries: list[dict[str, Any]], dst: Path, t0: float, t1: float,
              source_wav: Path | None, bgm_wav: Path | None, acfg: dict[str, Any]) -> None:
    """Write the soundtrack for output time [t0, t1]: ducked source audio + looped BGM + narration.

    ``source_wav`` must already cover [t0, t1] (sped up to match the video). Sample positions are
    absolute, so consecutive parts join sample-exactly.
    """
    sr = acfg["sample_rate"]
    first, last = int(round(t0 * sr)), int(round(t1 * sr))
    fade = max(acfg["duck_fade"], 0.01)
    ov, dv, bv = acfg["original_volume"], acfg["duck_volume"], acfg["bgm_volume"]
    windows = [(e["start"], e["end"]) for e in entries]

    clips = []
    for e in entries:
        data, clip_sr = sf.read(e["audio"], dtype="float32")
        if clip_sr != sr:
            raise ValueError(f"{e['audio']} is {clip_sr} Hz, expected {sr} Hz")
        if data.ndim > 1:
            data = data.mean(axis=1)
        clips.append((int(round(e["start"] * sr)), data * acfg["narration_volume"]))

    bgm = sf.read(bgm_wav, dtype="float32", always_2d=True)[0] if bgm_wav else None
    src = sf.SoundFile(source_wav) if source_wav else None
    block = sr * BLOCK_SECONDS
    try:
        with sf.SoundFile(dst, "w", sr, 2, "PCM_16") as out:
            for b0 in range(first, last, block):
                n = min(block, last - b0)
                pos = b0 + np.arange(n)
                duck = _duck_curve(pos / sr, windows, fade)
                y = np.zeros((n, 2), dtype=np.float32)
                if src is not None:
                    x = src.read(n, dtype="float32", always_2d=True)
                    if len(x) < n:
                        x = np.pad(x, ((0, n - len(x)), (0, 0)))
                    y += x[:, :2] * (ov + (dv - ov) * duck)[:, None]
                if bgm is not None:
                    y += bgm[pos % len(bgm)] * (bv * (1 - 0.5 * duck))[:, None]
                for start, data in clips:
                    a, b = max(start, b0), min(start + len(data), b0 + n)
                    if a < b:
                        y[a - b0:b - b0] += data[a - start:b - start, None]
                out.write(np.clip(y * MIX_HEADROOM, -1.0, 1.0))
    finally:
        if src is not None:
            src.close()


def write_srt(entries: list[dict[str, Any]], path: Path) -> None:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    blocks = [f"{i}\n{ts(e['start'])} --> {ts(e['end'])}\n{e['text']}\n" for i, e in enumerate(entries, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")


def _encoder_args(vcfg: dict[str, Any]) -> list[str]:
    q = str(vcfg["quality"])
    if vcfg["encoder"] == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", q, "-b:v", "0", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", q, "-pix_fmt", "yuv420p"]


def _logo(vcfg: dict[str, Any]) -> Path | None:
    logo = resolve((vcfg.get("logo") or {}).get("path"))
    return logo if logo and logo.exists() else None


def _video_graph(ratio: str, vcfg: dict[str, Any], fps: Fraction, logo_input: int | None) -> tuple[str, str]:
    """Filter graph for speed, frame rate, reframing and logo. Returns (graph, output label)."""
    w, h = vcfg["landscape"] if ratio == "16:9" else vcfg["portrait"]
    base = f"[0:v]setpts=PTS/{vcfg['speed']:.4f},fps={fps.numerator}/{fps.denominator}"
    if vcfg.get("mirror"):
        # A mirrored copy no longer matches the original frame for frame, which is what
        # re-upload matching compares. It goes here, on the source, so the logo overlaid
        # further down still reads the right way round. Anything burned into the picture
        # (hard subtitles, on-screen signs) reads backwards afterwards, so this is opt-in.
        base += ",hflip"
    if ratio == "16:9":
        graph = f"{base},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1[base]"
    elif vcfg.get("portrait_mode") == "crop":
        graph = f"{base},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[base]"
    else:
        # Blur a small copy and scale it back up: same look as blurring at full size, much faster.
        sw, sh = w // 4, h // 4
        graph = (
            f"{base},split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},gblur=sigma=6,"
            f"scale={w}:{h},eq=brightness=-0.06[bg];"
            f"[fgsrc]scale={w}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
        )
    if logo_input is None:
        return graph, "[base]"
    lcfg = vcfg["logo"]
    m = lcfg.get("margin", 30)
    lw = int(w * lcfg.get("width", 0.12)) // 2 * 2
    x, y = {
        "top-left": (f"{m}", f"{m}"),
        "top-right": (f"W-w-{m}", f"{m}"),
        "bottom-left": (f"{m}", f"H-h-{m}"),
        "bottom-right": (f"W-w-{m}", f"H-h-{m}"),
    }[lcfg.get("position", "top-right")]
    bcfg = lcfg.get("bounce") or {}
    if not bcfg.get("enabled"):
        graph += (f";[{logo_input}:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa={lcfg.get('opacity', 0.85)}[logo]"
                  f";[base][logo]overlay={x}:{y}[v]")
        return graph, "[v]"
    # A second, smaller copy drifts across the frame and turns at the edges, like a DVD player's
    # idle screen. The commas inside the expressions are escaped for FFmpeg's filtergraph parser.
    bw = int(w * bcfg.get("width", 0.06)) // 2 * 2
    vx, vy = bcfg.get("speed", [120, 80])
    cycle = bcfg.get("color_cycle", 0)
    tint = f",format=yuva444p,hue=h=mod(t*{cycle}\\,360)" if cycle else ""
    graph += (
        f";[{logo_input}:v]format=rgba,split=2[logosrc][bouncesrc]"
        f";[logosrc]scale={lw}:-1,colorchannelmixer=aa={lcfg.get('opacity', 0.85)}[logo]"
        f";[bouncesrc]scale={bw}:-1,colorchannelmixer=aa={bcfg.get('opacity', 0.8)}{tint}[bounce]"
        f";[base][logo]overlay={x}:{y}[corner]"
        f";[corner][bounce]overlay=x=abs(mod(t*{vx}\\,2*(W-w))-(W-w))"
        f":y=abs(mod(t*{vy}\\,2*(H-h))-(H-h))[v]"
    )
    return graph, "[v]"


def render_part(video: Path, dst: Path, ratio: str, vcfg: dict[str, Any], fps: Fraction,
                start: float, frames: int | None, duration: float, desc: str) -> None:
    """Render output time [start, start + frames/fps) of the video, without audio.

    ``frames`` pins the exact frame count so parts join without drifting from the soundtrack;
    None renders to the end of the source.
    """
    logo = _logo(vcfg)
    inputs = ["-ss", f"{start * vcfg['speed']:.6f}", "-i", video] + (["-i", logo] if logo else [])
    graph, vout = _video_graph(ratio, vcfg, fps, 1 if logo else None)
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", vout, "-an",
           *_encoder_args(vcfg)]
    if frames:
        cmd += ["-frames:v", frames]
    dst.parent.mkdir(parents=True, exist_ok=True)
    media.run_with_progress([*cmd, dst], duration, desc)


def mux(videos: list[Path], audios: list[Path], dst: Path, target_lufs: float, duration: float, desc: str) -> None:
    """Join video parts (stream copy) and their soundtracks, normalizing loudness over the whole."""
    lists = dst.parent / f".{dst.stem}"
    v_in = media.input_args(videos, lists.with_suffix(".videos.txt"))
    a_in = media.input_args(audios, lists.with_suffix(".audios.txt"))
    measured = media.measure_loudness(a_in, target_lufs)
    af = f"{media.loudnorm_filter(measured, target_lufs)},aresample=48000"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        media.run_with_progress(
            ["ffmpeg", "-y", "-v", "error", *v_in, *a_in, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
             "-af", af, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", dst],
            duration, desc,
        )
    finally:
        for f in (lists.with_suffix(".videos.txt"), lists.with_suffix(".audios.txt")):
            f.unlink(missing_ok=True)
