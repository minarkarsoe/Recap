"""Fetch a YouTube video and its subtitles with yt-dlp, or register a local file."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import media
from .config import resolve

log = logging.getLogger(__name__)

# YouTube holds a 429 on the subtitle endpoint for several minutes, so back off in long steps.
# The last entry is None: that attempt raises instead of waiting again.
SUB_RETRY_WAITS = (120, 300, 600, 900, None)


class DownloadError(RuntimeError):
    pass


def _ytdlp(args: list[str]) -> subprocess.CompletedProcess:
    # yt-dlp needs a JavaScript runtime for YouTube. deno is pip-installed into this
    # venv, so put the venv's Scripts folder on PATH for the child process.
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "yt_dlp", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-10:])
        raise DownloadError(f"yt-dlp failed:\n{tail}")
    return proc


def choose_track(info: dict[str, Any], langs: list[str]) -> tuple[str | None, bool]:
    """Pick one subtitle track: (language code, is_auto_generated).

    Uploaded subtitles win over auto-generated ones. Auto-translated tracks ("Chinese from Arabic")
    are skipped: they are machine translations of another track, and asking for dozens of them is
    what gets YouTube to answer 429 Too Many Requests.
    """
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = {k: v for k, v in (info.get("automatic_captions") or {}).items()
            if not any(" from " in (f.get("name") or "") for f in v)}
    for tracks, is_auto in ((manual, False), (auto, True)):
        for pattern in langs:
            for lang in sorted(tracks):
                if re.fullmatch(pattern, lang):
                    return lang, is_auto
    return None, False


def download(url: str, work_root: Path, cfg: dict[str, Any]) -> Path:
    """Download video + one subtitle track into work/<video_id>/ and write meta.json. Returns the job dir."""
    dl = cfg["download"]
    common = ["--no-playlist"]
    # YouTube hands out subtitles and some formats only against a PO Token, which yt-dlp can mint
    # itself once it may fetch its JavaScript challenge solver. Without this, subtitle tracks come
    # back as "require a PO Token ... discarded since they are not downloadable as-is".
    if dl.get("remote_components"):
        common += ["--remote-components", dl["remote_components"]]
    # A cookies.txt exported from the browser is the only option that still works on Windows with
    # Chromium 127+, whose App-Bound Encryption yt-dlp cannot decrypt (yt-dlp issue 10927).
    if dl.get("cookies_file"):
        cookies = resolve(dl["cookies_file"])
        if not cookies or not cookies.exists():
            raise DownloadError(f"download.cookies_file is set but {dl['cookies_file']} is missing")
        common += ["--cookies", str(cookies)]
    elif dl.get("cookies_from_browser"):
        common += ["--cookies-from-browser", dl["cookies_from_browser"]]

    info = json.loads(_ytdlp([*common, "-J", url]).stdout)
    video_id = info["id"]
    job_dir = work_root / video_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "source.info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    log.info("%s (%s)", info.get("title", video_id), info.get("duration_string", "?"))

    # The video comes first: it is served from a different endpoint than the subtitles, and the
    # minutes it takes give YouTube's subtitle endpoint time to lift a 429 it is holding against us.
    video = job_dir / "source.mp4"
    if video.exists():
        log.info("Video already downloaded: %s", video)
    else:
        h = dl.get("max_height", 1080)
        # max_height is the SHORT side: the output is 1920x1080 or 1080x1920 depending on the
        # source's shape. Filtering on height alone throws away every good portrait format --
        # a 1080x1920 upload is 1920 tall, so "height<=1080" drops it and leaves 480p. Capping
        # both sides at the long side keeps 1080p either way and still excludes 1440p and 4K.
        long_side = round(h * 16 / 9)
        fmt = (f"bv*[height<={long_side}][width<={long_side}]+ba/"
               f"b[height<={long_side}][width<={long_side}]/b")
        log.info("Downloading video (up to %sp on the short side)...", h)
        _ytdlp([*common, "-f", fmt, "--merge-output-format", "mp4",
                "-o", str(job_dir / "source.%(ext)s"), url])
        if not video.exists():
            raise DownloadError(f"yt-dlp finished but {video} is missing")

    lang, is_auto = choose_track(info, dl["sub_langs"])
    subs = job_dir / f"source.{lang}.srt" if lang else None
    if subs and not subs.exists():
        log.info("Downloading %s subtitles (%s)...", "auto-generated" if is_auto else "uploaded", lang)
        for wait in SUB_RETRY_WAITS:
            try:
                _ytdlp([*common, "--skip-download", "--ignore-no-formats-error",
                        "--write-auto-subs" if is_auto else "--write-subs",
                        "--sub-langs", re.escape(lang), "--convert-subs", "srt",
                        "-o", str(job_dir / "source.%(ext)s"), url])
                break
            except DownloadError as exc:
                if wait is None or "429" not in str(exc):
                    raise
                log.warning("YouTube is rate-limiting subtitles (429); waiting %ds before retrying", wait)
                time.sleep(wait)
    if subs and not subs.exists():
        subs = next(iter(sorted(job_dir.glob(f"source.{lang}*.srt"))), None)
    if not lang:
        log.warning("No usable subtitles on YouTube for %s. Pass --subs with an .srt file.", video_id)

    return write_meta(job_dir, video_id, info.get("title", video_id), video, subs, lang)


def register_local(video: Path, subs: Path | None, work_root: Path) -> Path:
    """Create a job for a video file already on disk."""
    video = video.resolve()
    if not video.exists():
        raise DownloadError(f"Video not found: {video}")
    job_id = re.sub(r"[^\w\-]+", "_", video.stem).strip("_")[:60] or "local"
    job_dir = work_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    if subs:
        subs = subs.resolve()
        if not subs.exists():
            raise DownloadError(f"Subtitle file not found: {subs}")
    return write_meta(job_dir, job_id, video.stem, video, subs, None)


def attach_subs(job_dir: Path, subs: Path) -> None:
    """Use a subtitle file supplied by the user instead of the downloaded one."""
    dst = job_dir / f"source.user{subs.suffix.lower()}"
    if not (dst.exists() and dst.read_bytes() == subs.read_bytes()):
        shutil.copyfile(subs, dst)  # a new copy's mtime makes the pipeline re-parse
    meta = load_meta(job_dir)
    meta["subs"], meta["subs_lang"] = str(dst), "user"
    save_meta(job_dir, meta)


def write_meta(job_dir: Path, job_id: str, title: str, video: Path, subs: Path | None, lang: str | None) -> Path:
    meta = {
        "id": job_id,
        "title": title,
        "video": str(video),
        "subs": str(subs) if subs else None,
        "subs_lang": lang,
        "duration": media.probe_duration(video),
        "has_audio": media.has_audio(video),
    }
    if (job_dir / "meta.json").exists():
        # a Burmese output name set by hand outlives a re-download
        meta["my_title"] = load_meta(job_dir).get("my_title") or None
    save_meta(job_dir, meta)
    return job_dir


def load_meta(job_dir: Path) -> dict[str, Any]:
    return json.loads((job_dir / "meta.json").read_text(encoding="utf-8"))


def save_meta(job_dir: Path, meta: dict[str, Any]) -> None:
    (job_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
