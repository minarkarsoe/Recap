"""Keep a list of videos to recap, and say which one to work on next.

This is the one piece the pipeline did not already have. Everything downstream — download, frames,
drafting, proofreading, TTS, render — is `main.py` and the proofread tools; the queue only decides
what goes through them and remembers how far each video got.

    python tools/queue_manager.py list
    python tools/queue_manager.py search --limit 10 --min-minutes 20
    python tools/queue_manager.py add "https://www.youtube.com/watch?v=..."
    python tools/queue_manager.py next
    python tools/queue_manager.py fail <id> "no subtitle track"

Status is read back off the disk on every run rather than trusted from the file, so a job stays
correct even when work is done by hand outside the queue:

    pending   nothing downloaded yet
    parsed    beats.json exists — downloaded and cut into beats
    drafted   gemma has written a narration, nobody has corrected it
    approved  the corrected narration has been written over the draft
    rendered  the joined video is in output/
    failed    set by hand with `fail`, and skipped by `next`

It never deletes anything: no downloads, no work folders, no finished videos.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_config, resolve  # noqa: E402
from src.downloader import _ytdlp  # noqa: E402

QUEUE = ROOT / "queue.json"

# Compilations of a whole short drama, which is what this channel recaps. Narrow searches return
# single episodes and clip accounts; these are the phrasings the full uploads actually use.
DEFAULT_QUERIES = [
    "短剧 全集",
    "Chinese Short Drama Full Episode",
    "新番上线 全集",
]


# -- queue file ---------------------------------------------------------------------------


def load_queue() -> list[dict[str, Any]]:
    if not QUEUE.exists():
        return []
    return json.loads(QUEUE.read_text(encoding="utf-8"))


def save_queue(items: list[dict[str, Any]]) -> None:
    QUEUE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# -- status ------------------------------------------------------------------------------


def _output_exists(cfg: dict[str, Any], job: Path) -> bool:
    """True once a joined video for this job is in output/.

    The output is named after the video's Burmese name if it has one and its own title otherwise --
    never its id -- so the name is read back from the job's own meta.json and matched against the
    folder. Each video has its own folder under output/, so the joined file is one level down; the
    part previews live beside it but are named partNN, so only the joined file matches the name and
    a half-rendered job still reads as unfinished.
    """
    meta = job / "meta.json"
    if not meta.exists():
        return False
    out_dir = resolve(cfg["paths"]["output_dir"])
    if not out_dir or not out_dir.exists():
        return False
    m = json.loads(meta.read_text(encoding="utf-8"))
    title = m.get("my_title") or m.get("title", "")
    # _safe_name trims the title, so compare on a prefix rather than the whole thing.
    stem = "".join(c for c in title[:40] if c not in '\\/:*?"<>|')
    return any(p.suffix == ".mp4" and p.stem.startswith(stem)
               for folder in out_dir.iterdir() if folder.is_dir()
               for p in folder.iterdir())


def disk_status(cfg: dict[str, Any], video_id: str, recorded: str) -> str:
    """What the files on disk say about this job, falling back to what the queue recorded."""
    if recorded == "failed":
        return "failed"
    job = resolve(cfg["paths"]["work_dir"]) / video_id
    if not job.exists():
        return "pending"
    if _output_exists(cfg, job):
        return "rendered"
    parts = job / "parts"
    if parts.exists():
        for part in sorted(parts.iterdir()):
            # apply_proof.py keeps the draft as script.gemma.txt the first time it writes over it,
            # so its presence is the mark that a person has been through the narration.
            if (part / "script.gemma.txt").exists():
                return "approved"
        if any((p / "script.txt").exists() for p in parts.iterdir()):
            return "drafted"
    if (job / "beats.json").exists():
        return "parsed"
    return "pending"


def refresh(cfg: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        item["status"] = disk_status(cfg, item["id"], item.get("status", "pending"))
    return items


# -- youtube ------------------------------------------------------------------------------


def _cookie_args(cfg: dict[str, Any]) -> list[str]:
    dl = cfg.get("download", {})
    if dl.get("cookies_file"):
        cookies = resolve(dl["cookies_file"])
        if cookies and cookies.exists():
            return ["--cookies", str(cookies)]
    if dl.get("cookies_from_browser"):
        return ["--cookies-from-browser", dl["cookies_from_browser"]]
    return []


def search(cfg: dict[str, Any], queries: list[str], limit: int, min_seconds: float) -> list[dict]:
    """Search YouTube for full-drama compilations longer than ``min_seconds``.

    Results come back flat (no per-video request), so this stays cheap even for a wide search.
    """
    found: dict[str, dict[str, Any]] = {}
    for query in queries:
        args = ["--flat-playlist", "--dump-json", "--ignore-errors",
                *_cookie_args(cfg), f"ytsearch{limit * 3}:{query}"]
        try:
            done = _ytdlp(args)
        except Exception as exc:  # a bad query should not lose the other queries' results
            print(f"  ! search failed for {query!r}: {exc}", file=sys.stderr)
            continue
        for line in done.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue
            vid, duration = info.get("id"), info.get("duration")
            if not vid or vid in found:
                continue
            if duration is None or duration < min_seconds:
                continue
            found[vid] = {
                "id": vid,
                "title": (info.get("title") or "").strip(),
                "url": info.get("url") or f"https://www.youtube.com/watch?v={vid}",
                "duration": int(duration),
                "status": "pending",
                "query": query,
            }
    return list(found.values())


def video_id(url: str) -> str | None:
    """The eleven-character id out of any of the URL shapes YouTube hands out."""
    import re
    for pattern in (r"[?&]v=([A-Za-z0-9_-]{11})", r"youtu\.be/([A-Za-z0-9_-]{11})",
                    r"/shorts/([A-Za-z0-9_-]{11})", r"^([A-Za-z0-9_-]{11})$"):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def probe(cfg: dict[str, Any], url: str) -> dict[str, Any] | None:
    """Title and duration for one URL, so a hand-added video looks like a searched one."""
    try:
        done = _ytdlp(["--dump-json", "--skip-download", *_cookie_args(cfg), url])
    except Exception as exc:
        print(f"  ! could not read {url}: {exc}", file=sys.stderr)
        return None
    for line in done.stdout.splitlines():
        if line.strip().startswith("{"):
            info = json.loads(line)
            return {"id": info["id"], "title": (info.get("title") or "").strip(),
                    "url": url, "duration": int(info.get("duration") or 0),
                    "status": "pending", "query": "manual"}
    return None


# -- commands -----------------------------------------------------------------------------


def _hms(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def cmd_list(cfg: dict[str, Any], _: argparse.Namespace) -> int:
    items = refresh(cfg, load_queue())
    save_queue(items)
    if not items:
        print("queue is empty — try: python tools/queue_manager.py search")
        return 0
    width = max(len(i["title"][:60]) for i in items)
    for i, item in enumerate(items, 1):
        print(f"{i:2d}. {item['status']:9} {_hms(item['duration']):>8}  "
              f"{item['title'][:60]:<{width}}  {item['id']}")
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    print("\n" + "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    return 0


def cmd_search(cfg: dict[str, Any], args: argparse.Namespace) -> int:
    queries = [args.query] if args.query else DEFAULT_QUERIES
    print(f"searching: {', '.join(queries)} (longer than {args.min_minutes} min)")
    results = search(cfg, queries, args.limit, args.min_minutes * 60)
    items = load_queue()
    have = {i["id"] for i in items}
    added = [r for r in results if r["id"] not in have][:args.limit]
    items += added
    save_queue(refresh(cfg, items))
    for r in added:
        print(f"  + {_hms(r['duration']):>8}  {r['title'][:70]}")
    print(f"\n{len(added)} added, {len(results) - len(added)} already queued, "
          f"{len(items)} in the queue")
    return 0


def cmd_add(cfg: dict[str, Any], args: argparse.Namespace) -> int:
    items = load_queue()
    have = {i["id"] for i in items}
    for url in args.urls:
        vid = video_id(url)
        if vid and vid in have:
            print(f"  = already queued: {vid}")
            continue
        info = probe(cfg, url)
        if not info:
            continue
        if info["id"] in have:
            print(f"  = already queued: {info['id']}")
            continue
        items.append(info)
        have.add(info["id"])
        print(f"  + {_hms(info['duration']):>8}  {info['title'][:70]}")
    save_queue(refresh(cfg, items))
    return 0


def cmd_next(cfg: dict[str, Any], _: argparse.Namespace) -> int:
    """Print the command for the next unfinished video, newest status first.

    Nothing is run here on purpose: drafting and rendering are long GPU jobs and the proofreading
    between them is a person's, so the queue hands over one command at a time.
    """
    items = refresh(cfg, load_queue())
    save_queue(items)
    for status, hint in (
        ("approved", "python main.py --job {id}"),
        ("drafted", "python tools/proofread/seed.py 1 > lines_p01.py   # in scratch/{id}/"),
        ("parsed", "python main.py --job {id} --stop-after script"),
        ("pending", 'python main.py --url "{url}" --stop-after script'),
    ):
        for item in items:
            if item["status"] == status:
                print(f"{item['title'][:70]}\n  {item['id']}  ({status}, {_hms(item['duration'])})")
                print("\n  " + hint.format(id=item["id"], url=item["url"]))
                return 0
    print("nothing left to do — every queued video is rendered or failed")
    return 0


def cmd_fail(cfg: dict[str, Any], args: argparse.Namespace) -> int:
    items = load_queue()
    for item in items:
        if item["id"] == args.id:
            item["status"] = "failed"
            item["reason"] = args.reason or ""
            save_queue(items)
            print(f"marked failed: {item['title'][:70]}")
            return 0
    print(f"no queued video with id {args.id}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Pick and track the videos to recap")
    parser.add_argument("--config", type=Path, help="settings file (default config/settings.yaml)")
    subs = parser.add_subparsers(dest="cmd", required=True)

    subs.add_parser("list", help="show the queue with its current status")

    p = subs.add_parser("search", help="find full-drama compilations and queue them")
    p.add_argument("--query", help="search text instead of the built-in queries")
    p.add_argument("--limit", type=int, default=10, help="how many to add (default 10)")
    p.add_argument("--min-minutes", type=float, default=20, help="skip anything shorter")

    p = subs.add_parser("add", help="queue one or more URLs by hand")
    p.add_argument("urls", nargs="+")

    subs.add_parser("next", help="print the command for the next unfinished video")

    p = subs.add_parser("fail", help="mark a video failed so next skips it")
    p.add_argument("id")
    p.add_argument("reason", nargs="?")

    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config(args.config)
    return {"list": cmd_list, "search": cmd_search, "add": cmd_add,
            "next": cmd_next, "fail": cmd_fail}[args.cmd](cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
