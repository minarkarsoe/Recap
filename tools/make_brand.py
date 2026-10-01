"""Build the channel's brand assets from the logo.

Writes to assets/brand/: a square profile picture, a logo-and-name lockup, a YouTube banner, a
Facebook cover and a short intro clip. The text is drawn by libass (FFmpeg's subtitles filter) so
Burmese is shaped correctly; the logo itself comes from tools/make_logo.py.

    python tools/make_brand.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

NAME_MM = "လခြမ်းဓား"
NAME_EN = "CRESCENT BLADE"
FONT_MM, FONT_EN = "Myanmar Text", "Segoe UI"

# The series the thumbnail advertises; change these three lines for the next series.
SERIES_MM = "ကုရှီယီ"
SERIES_SUB_MM = "အတုနဲ့ အစစ်"
SERIES_TAG_MM = "ဗမာပြန် · အပိုင်း ၁၂ ပိုင်းတွဲ"

GOLD = "&H004BB5E8"   # ASS colours are &HAABBGGRR
CREAM = "&H009ADBF6"
WHITE = "&H00F2F6FA"
BG = "0x0E1320"       # page background, a hair darker than the logo's disc

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "logos" / "logo.png"
OUT = ROOT / "assets" / "brand"


def _run(args: list[str], cwd: Path | None = None) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True, cwd=cwd)


def _ass(path: Path, w: int, h: int, events: list[str]) -> Path:
    """Write a subtitle file whose canvas matches the image, one Dialogue line per event."""
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: mm,{FONT_MM},100,{GOLD},&H00000000,&H00000000,0,1,0,0,5,0,0,0,1",
        f"Style: en,{FONT_EN},100,{WHITE},&H00000000,&H00000000,0,1,0,0,5,0,0,0,1", "",
        "[Events]", "Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Text",
    ]
    path.write_text("\n".join(head + events) + "\n", encoding="utf-8")
    return path


def _sub_filter(ass: Path) -> str:
    """Just the file name: FFmpeg runs in the subtitle's folder, so no drive colon has to be escaped.

    Fontconfig finds the installed system fonts on its own, so no fontsdir is needed either.
    """
    return f"subtitles={ass.name}"


def profile(dst: Path, size: int = 1024) -> None:
    """The logo on the page background, sized for a round avatar crop."""
    lw = int(size * 0.80)
    _run(["-f", "lavfi", "-i", f"color=c={BG}:s={size}x{size}", "-i", str(LOGO),
          "-filter_complex", f"[1:v]scale={lw}:-1[l];[0:v][l]overlay=(W-w)/2:(H-h)/2",
          "-frames:v", "1", str(dst)])


def _text_layer(tmp: Path, name: str, w: int, h: int, events: list[str]) -> Path:
    """Draw the text on black and turn its own brightness into alpha.

    libass leaves the alpha plane alone, so text drawn straight onto a transparent canvas stays
    invisible; keying the black away gives a layer that can sit on anything.
    """
    ass = _ass(tmp / f"{name}.ass", w, h, events)
    dst = tmp / f"{name}.png"
    _run(["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}", "-filter_complex",
          f"[0:v]{_sub_filter(ass)},split=2[c][m];[m]format=gray[a];[c][a]alphamerge[out]",
          "-map", "[out]", "-frames:v", "1", str(dst)], cwd=tmp)
    return dst


def lockup(dst: Path, tmp: Path) -> None:
    """Logo beside the name, on transparency, for overlays and posts."""
    w, h = 1800, 560
    text = _text_layer(tmp, "lockup", w, h, [
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,mm,0,0,0,{{\pos(1090,235)\fs200}}{NAME_MM}",
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,en,0,0,0,{{\pos(1090,395)\fs66\fsp10\c{CREAM}}}{NAME_EN}",
    ])
    _run(["-f", "lavfi", "-i", f"color=c=black@0.0:s={w}x{h},format=rgba", "-i", str(LOGO), "-i", str(text),
          "-filter_complex", f"[1:v]scale=470:-1[l];[0:v][l]overlay=90:(H-h)/2[a];[a][2:v]overlay=0:0",
          "-frames:v", "1", str(dst)], cwd=tmp)


def banner(dst: Path, w: int, h: int, tmp: Path, logo_w: int, title: int, sub: int) -> None:
    """Logo above the name, centred well inside the crop that every platform keeps."""
    cy = h // 2
    ass = _ass(tmp / f"banner{w}.ass", w, h, [
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,mm,0,0,0,{{\pos({w // 2},{cy + int(h * 0.115)})\fs{title}}}{NAME_MM}",
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,en,0,0,0,"
        rf"{{\pos({w // 2},{cy + int(h * 0.20)})\fs{sub}\fsp8\c{CREAM}}}{NAME_EN}",
    ])
    # A soft gold glow behind the mark keeps the flat background from looking empty.
    glow = (f"color=c={BG}:s={w}x{h}[bg];"
            f"color=c=0x2A2410:s={w // 8}x{h // 8},format=rgba,"
            f"geq=r='r(X\\,Y)':a='255*max(0\\,1-hypot(X-W/2\\,Y-H/2)/(W/3))'[g];"
            f"[g]scale={w}:{h}[glow];[bg][glow]overlay=0:0[canvas]")
    _run(["-f", "lavfi", "-i", f"color=c={BG}:s=16x16", "-i", str(LOGO),
          "-filter_complex",
          f"{glow};[1:v]scale={logo_w}:-1[l];[canvas][l]overlay=(W-w)/2:{cy - int(h * 0.085)}-h/2,"
          f"{_sub_filter(ass)}",
          "-frames:v", "1", str(dst)], cwd=tmp)


def thumbnail(dst: Path, tmp: Path, w: int = 1280, h: int = 720,
              series: str = SERIES_MM, sub: str = SERIES_SUB_MM, tag: str = SERIES_TAG_MM,
              title_size: int = 150) -> None:
    """One cover for every episode of a series: the mark on the left, the title on the right.

    Everything here is our own artwork, so the same file can sit on all of that series' uploads.
    Each series gets its own file, named after the series exactly as its output folder is
    ("05 တယောသံနဲ့ လက်စားချေမှု.png"), so an older cover is never overwritten and a cover, its plan
    and its videos are obvious at a glance. Never write a series cover to the brand-wide
    thumbnail.png below -- a plain run of this script rewrites that one.
    A long title needs a smaller title_size, since the text column is only about 750 px wide.
    """
    cx = 905  # centre of the text column, to the right of the logo
    ass = _ass(tmp / "thumb.ass", w, h, [
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,mm,0,0,0,{{\pos({cx},250)\fs{title_size}}}{series}",
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,mm,0,0,0,"
        rf"{{\pos({cx},375)\fs68\c{CREAM}}}{sub}",
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,mm,0,0,0,"
        rf"{{\pos({cx},505)\fs44\c{WHITE}}}{tag}",
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,mm,0,0,0,"
        rf"{{\pos({cx},625)\fs34\c{GOLD}}}{NAME_MM} · {NAME_EN}",
    ])
    rule = f"drawbox=x={cx - 210}:y=440:w=420:h=3:color=0xE8B54B@0.9:t=fill"
    glow = (f"color=c={BG}:s={w}x{h}[bg];"
            f"color=c=0x2A2410:s={w // 8}x{h // 8},format=rgba,"
            f"geq=r='r(X\\,Y)':a='255*max(0\\,1-hypot(X-W/3\\,Y-H/2)/(W/2.4))'[g];"
            f"[g]scale={w}:{h}[glow];[bg][glow]overlay=0:0[canvas]")
    _run(["-f", "lavfi", "-i", f"color=c={BG}:s=16x16", "-i", str(LOGO),
          "-filter_complex",
          f"{glow};[1:v]scale=400:-1[l];[canvas][l]overlay=115:(H-h)/2[a];"
          f"[a]{_sub_filter(ass)},{rule}",
          "-frames:v", "1", str(dst)], cwd=tmp)


def intro(dst: Path, tmp: Path, seconds: float = 3.2) -> None:
    """A short opener: the mark fades up, the name follows, a gold rule draws itself under it."""
    w, h, fps = 1920, 1080, _part_fps()
    bgm = ROOT / "assets" / "bgm" / "bgm.mp3"
    ass = _ass(tmp / "intro.ass", w, h, [
        rf"Dialogue: 0,0:00:00.70,0:00:{seconds:05.2f},mm,0,0,0,"
        rf"{{\pos({w // 2},712)\fs118\fad(500,400)}}{NAME_MM}",
        rf"Dialogue: 0,0:00:01.00,0:00:{seconds:05.2f},en,0,0,0,"
        rf"{{\pos({w // 2},808)\fs44\fsp12\c{CREAM}\fad(500,400)}}{NAME_EN}",
    ])
    rule = ("drawbox=x=(iw-w)/2:y=862:w='min(max(t-1.2\\,0)\\,0.7)/0.7*520':h=3:"
            "color=0xE8B54B@0.9:t=fill:enable='gt(t,1.2)'")
    graph = (f"[1:v]scale=400:-1,format=rgba,fade=t=in:st=0.1:d=0.7:alpha=1,"
             f"fade=t=out:st={seconds - 0.45:.2f}:d=0.45:alpha=1[l];"
             f"[0:v][l]overlay=(W-w)/2:215,{_sub_filter(ass)},{rule},"
             f"fade=t=out:st={seconds - 0.4:.2f}:d=0.4[v]")
    # -loop keeps the still logo alive for the whole clip; a single frame would freeze it mid-fade.
    inputs = ["-f", "lavfi", "-i", f"color=c={BG}:s={w}x{h}:r={fps}:d={seconds}",
              "-loop", "1", "-framerate", fps, "-i", str(LOGO)]
    maps, audio = ["-map", "[v]"], []
    if bgm.exists():
        inputs += ["-i", str(bgm)]
        maps += ["-map", "2:a"]
        audio = ["-af", f"volume=0.5,afade=t=out:st={seconds - 0.8:.2f}:d=0.8", "-c:a", "aac", "-b:a", "192k"]
    _run([*inputs, "-filter_complex", graph, *maps, *audio, "-t", f"{seconds}",
          "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21", "-pix_fmt", "yuv420p", str(dst)], cwd=tmp)


def _part_fps() -> str:
    """Match the rendered parts so the intro can be joined without re-encoding."""
    part = next((ROOT / "output").rglob("part01*.mp4"), None)
    if not part:
        return "24000/1001"
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=r_frame_rate", "-of", "json", str(part)], capture_output=True, text=True)
    return json.loads(out.stdout)["streams"][0]["r_frame_rate"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the channel's brand assets from the logo")
    parser.add_argument("--thumbnail", type=Path, metavar="FILE",
                        help="build only a series cover, at this path (the shared assets are left alone)")
    parser.add_argument("--series", default=SERIES_MM, help="series name for the cover")
    parser.add_argument("--sub", default=SERIES_SUB_MM, help="line under the series name")
    parser.add_argument("--tag", default=SERIES_TAG_MM, help="line under the gold rule")
    parser.add_argument("--title-size", type=int, default=150, help="font size of the series name")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        if args.thumbnail:
            # FFmpeg runs in the temp folder, so a relative path would land there instead.
            dst = args.thumbnail.resolve()
            thumbnail(dst, tmp, series=args.series, sub=args.sub, tag=args.tag,
                      title_size=args.title_size)
            print(f"wrote {dst}")
            return
        profile(OUT / "profile.png")
        lockup(OUT / "lockup.png", tmp)
        banner(OUT / "youtube_banner.png", 2560, 1440, tmp, logo_w=360, title=150, sub=52)
        banner(OUT / "facebook_cover.png", 1640, 856, tmp, logo_w=250, title=104, sub=36)
        thumbnail(OUT / "thumbnail.png", tmp)   # the brand-wide sample, not any series' cover
        intro(OUT / "intro.mp4", tmp)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
