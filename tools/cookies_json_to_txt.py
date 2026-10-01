"""Convert a JSON cookie export into the Netscape cookies.txt file yt-dlp expects.

    python tools/cookies_json_to_txt.py "C:\\Users\\me\\Downloads\\www.youtube.com_....json"

Writes config/cookies.txt by default. Browser extensions export either a plain list of cookie
objects (Puppeteer/EditThisCookie style) or {"cookies": [...]}; both are accepted. Nothing is
printed except counts, because these values are login credentials.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def to_netscape(cookies: list[dict]) -> list[str]:
    lines = ["# Netscape HTTP Cookie File", "# Written by tools/cookies_json_to_txt.py", ""]
    for c in cookies:
        domain = c.get("domain") or ""
        name, value = c.get("name"), c.get("value")
        if not domain or not name or value is None:
            continue
        # Session cookies have no expiry; yt-dlp reads 0 as "expires at the end of the session".
        expires = c.get("expires") or c.get("expirationDate") or 0
        expires = 0 if expires in (-1, None) else int(float(expires))
        row = "\t".join([
            domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE",
            str(expires),
            name,
            value,
        ])
        # yt-dlp keeps httpOnly cookies only when they carry this marker.
        lines.append(f"#HttpOnly_{row}" if c.get("httpOnly") else row)
    return lines


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1]).expanduser()
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "config" / "cookies.txt"
    if not src.exists():
        sys.exit(f"no {src}")
    data = json.loads(src.read_text(encoding="utf-8-sig"))
    cookies = data.get("cookies", []) if isinstance(data, dict) else data
    lines = to_netscape(cookies)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    domains = sorted({c.get("domain", "") for c in cookies})
    print(f"{len(lines) - 3} cookies -> {dst}")
    print("domains:", ", ".join(domains))


if __name__ == "__main__":
    main()
