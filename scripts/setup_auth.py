"""Optional one-time setup of the GLOBAL guest auth file for ytmusicapi.

This is entirely optional — the backend works in plain guest mode without it.
Supplying a browser-auth file can improve the quality of public search/browse
results. It is NOT per-user auth: there is no YouTube account linking anymore.

Browser auth — paste request headers from a logged-in YouTube Music browser tab.
No Google Cloud setup needed. The cookies typically last for months.
See: https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html

Run from the `backend/` directory:
    python scripts/setup_auth.py
"""

from __future__ import annotations

from pathlib import Path

from ytmusicapi import YTMusic
from ytmusicapi.setup import setup

BACKEND_DIR = Path(__file__).resolve().parent.parent


def do_browser() -> None:
    import re

    print()
    print("=== Browser auth setup ===")
    print("1. Open https://music.youtube.com in Chrome or Firefox while logged in.")
    print("2. Open DevTools (F12) -> Network tab -> filter: Fetch/XHR.")
    print("3. Click 'Library' inside YT Music to trigger requests.")
    print("4. Right-click any /youtubei/v1/... request -> Copy -> Copy as cURL.")
    print("5. Paste below. End input with an empty line.")
    print()
    print("Paste cURL now:")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip() and lines:
            break
        lines.append(line)
    raw_text = "\n".join(lines)

    # If the input is a Chrome cURL command, normalize it to header lines.
    # ytmusicapi's setup() expects `key: value` lines including a `cookie:`
    # header, but cURL uses `-H` flags and a separate `-b 'cookies'` flag.
    if "-H " in raw_text or "-b " in raw_text:
        headers = re.findall(r"-H '([^']+)'", raw_text)
        m = re.search(r"-b '([^']+)'", raw_text)
        if m:
            headers.append("cookie: " + m.group(1))
        headers_raw = "\n".join(headers)
        print(f"(normalized {len(headers)} headers from cURL)")
    else:
        headers_raw = raw_text

    out = BACKEND_DIR / "browser.json"
    setup(filepath=str(out), headers_raw=headers_raw)
    print(f"\nSaved {out}.  It will be picked up automatically (AUTH_FILE=browser.json).")


def verify(path: Path) -> None:
    print(f"\nVerifying {path.name}...")
    yt = YTMusic(str(path))
    try:
        results = yt.search("test", limit=1)
        print(f"OK — search returned {len(results)} result(s).  Guest auth works!")
    except Exception as e:  # noqa: BLE001
        print(f"WARN — a test search failed: {e}")
        print("Auth file was written but may be invalid.")


def main() -> None:
    do_browser()
    verify(BACKEND_DIR / "browser.json")


if __name__ == "__main__":
    main()
