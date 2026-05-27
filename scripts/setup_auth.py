"""One-time auth setup for ytmusicapi.

Two paths:
  1. OAuth (recommended) — log in via Google's device-flow OAuth.
     Requires a Google OAuth Client ID/Secret (TV/limited-input type).
     See: https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html

  2. Browser auth — paste request headers from a logged-in YouTube Music browser tab.
     No Google Cloud setup needed. The cookies typically last for months.
     See: https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html

Run from the `backend/` directory:
    python scripts/setup_auth.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from ytmusicapi import YTMusic
from ytmusicapi.setup import setup, setup_oauth

BACKEND_DIR = Path(__file__).resolve().parent.parent


def do_oauth() -> None:
    print()
    print("=== OAuth setup ===")
    print("You need a Google OAuth Client ID + Secret of type 'TV and Limited Input'.")
    print("Guide: https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html")
    print()
    client_id = input("Client ID: ").strip()
    client_secret = input("Client Secret: ").strip()
    out = BACKEND_DIR / "oauth.json"
    setup_oauth(filepath=str(out), client_id=client_id, client_secret=client_secret, open_browser=True)
    print(f"\nSaved {out}.  Set AUTH_TYPE=oauth in your .env (default).")


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
    print(f"\nSaved {out}.  Set AUTH_FILE=browser.json and AUTH_TYPE=browser in .env.")


def verify(path: Path) -> None:
    print(f"\nVerifying {path.name}...")
    yt = YTMusic(str(path))
    try:
        playlists = yt.get_library_playlists(limit=1)
        print(f"OK — found {len(playlists)} library playlist(s).  You're authed!")
    except Exception as e:  # noqa: BLE001
        print(f"WARN — could not fetch library playlists: {e}")
        print("Auth file was written but the account may not be configured for YT Music.")


def main() -> None:
    print("Which auth method?")
    print("  1) OAuth  (needs Google Cloud OAuth client; cleaner long-term)")
    print("  2) Browser cookies  (easier; just paste request headers)")
    choice = input("Choice [1/2]: ").strip()
    if choice == "1":
        do_oauth()
        verify(BACKEND_DIR / "oauth.json")
    elif choice == "2":
        do_browser()
        verify(BACKEND_DIR / "browser.json")
    else:
        print("Unknown choice.")
        sys.exit(1)


if __name__ == "__main__":
    main()
