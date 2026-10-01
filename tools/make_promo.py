"""Build a page-promotion poster from one still of a finished recap.

A portrait card for Facebook: the still, graded and cropped, a dark panel across the bottom, and
the channel lockup with a Like / Share / Follow call to action. The text is drawn by libass
(FFmpeg's subtitles filter) so Burmese is shaped correctly.

    python tools/make_promo.py --still scratch/promo/hero.png --out assets/brand/promo_follow.png

--crop-x moves the 4:5 window across the 16:9 still; the default keeps it centred. Pull the still
out of a finished video with a plain `ffmpeg -ss <seconds> -i <video> -frames:v 1 hero.png`, and
pick a moment with no burned-in subtitle line across it.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

NAME_MM = "လခြမ်းဓား"
NAME_EN = "CRESCENT BLADE"
FONT_MM, FONT_EN = "Myanmar Text", "Segoe UI"

HEADLINE = NAME_MM
SUBLINE = "တရုတ်ဇာတ်လမ်းတွဲများ · ဗမာစကားပြန်"
CTA = "LIKE  ·  SHARE  ·  FOLLOW"
FOOTER = "အပိုင်းအသစ် — ဗုဒ္ဓဟူး နဲ့ စနေ၊ ည ၈ နာရီ"

GOLD = "&H004BB5E8"   # ASS colours are &HAABBGGRR
CREAM = "&H009ADBF6"
WHITE = "&H00F2F6FA"

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "logos" / "logo.png"

W, H = 1080, 1350          # 4:5, the tallest card Facebook shows without cropping
PANEL_TOP, PANEL_SOLID = 690, 975   # where the dark panel starts to fade in, and where it is full


def _run(args: list[str], cwd: Path | None = None) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True, cwd=cwd)


def _ass(path: Path, events: list[str]) -> Path:
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: mm,{FONT_MM},100,{GOLD},&H00000000,&H00000000,0,1,0,0,5,0,0,0,1",
        f"Style: en,{FONT_EN},100,{WHITE},&H00000000,&H00000000,0,1,0,0,5,0,0,0,1", "",
        "[Events]", "Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Text",
    ]
    path.write_text("\n".join(head + events) + "\n", encoding="utf-8")
    return path


def poster(still: Path, dst: Path, tmp: Path, crop_x: int | None = None) -> None:
    """The still under a dark panel, with the lockup and the call to action on top."""
    cw = int(H / 5 * 4 * (1080 / H))  # a 4:5 window out of the 1920x1080 still
    cw = 864
    x = (1920 - cw) // 2 if crop_x is None else crop_x
    ass = _ass(tmp / "promo.ass", [
        # the lockup, left aligned beside the mark
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,mm,0,0,0,{{\an4\pos(232,1028)\fs96}}{HEADLINE}",
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,mm,0,0,0,"
        rf"{{\an4\pos(232,1104)\fs38\c{CREAM}}}{SUBLINE}",
        # the ask, centred under the gold rule
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,en,0,0,0,"
        rf"{{\an5\pos(540,1216)\fs54\c{WHITE}}}{CTA}",
        rf"Dialogue: 0,0:00:00.00,0:00:10.00,mm,0,0,0,"
        rf"{{\an5\pos(540,1292)\fs34\c{GOLD}}}{FOOTER}",
    ])
    panel = (f"color=c=black:s={W}x{H},format=rgba,"
             f"geq=r=0:g=0:b=0:a='255*clip((Y-{PANEL_TOP})/{PANEL_SOLID - PANEL_TOP}\\,0\\,1)'[panel]")
    rule = "drawbox=x=70:y=1156:w=940:h=3:color=0xE8B54B@0.9:t=fill"
    # a hairline frame, inset, so the card reads as a printed poster rather than a screenshot
    frame = "drawbox=x=22:y=22:w=%d:h=%d:color=0xE8B54B@0.55:t=2" % (W - 44, H - 44)
    _run(["-i", str(still), "-i", str(LOGO),
          "-filter_complex",
          f"[0:v]crop={cw}:1080:{x}:0,scale={W}:{H},eq=contrast=1.06:saturation=1.10,"
          f"vignette=PI/5[pic];"
          f"{panel};[pic][panel]overlay=0:0[a];"
          f"[1:v]scale=128:-1[l];[a][l]overlay=70:966[b];"
          f"[b]subtitles={ass.name},{rule},{frame}",
          "-frames:v", "1", str(dst)], cwd=tmp)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a Like/Share/Follow poster from a still")
    parser.add_argument("--still", type=Path, required=True, help="a 1920x1080 frame to build on")
    parser.add_argument("--out", type=Path, required=True, help="where to write the poster")
    parser.add_argument("--crop-x", type=int, help="left edge of the 864-wide window (default: centred)")
    args = parser.parse_args()

    still, dst = args.still.resolve(), args.out.resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        poster(still, dst, Path(td), args.crop_x)
    print(f"wrote {dst}")


if __name__ == "__main__":
    main()
