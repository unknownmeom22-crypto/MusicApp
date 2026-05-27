"""Thin wrapper around ytmusicapi.

Two client tiers:
  - Global guest client (for public endpoints — search, home, charts, etc).
  - Per-user clients backed by tokens stored in the `yt_auth` Postgres table.

YT auth payloads live as JSONB rows in Postgres (not on disk) so they survive
Render's ephemeral filesystem.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from psycopg.types.json import Json
from ytmusicapi import YTMusic
from ytmusicapi.setup import setup as ytm_setup

from . import db
from .config import auth_path


@lru_cache(maxsize=1)
def client() -> YTMusic:
    """Process-wide guest/global YTMusic client."""
    path = auth_path()
    if path.exists():
        return YTMusic(str(path))
    return YTMusic()


def is_authed() -> bool:
    return auth_path().exists()


# ---------- Guest client wrappers ----------

def search(q: str, filter: str | None = None, limit: int = 20) -> list[dict]:
    return client().search(q, filter=filter, limit=limit)


def get_song(video_id: str) -> dict:
    return client().get_song(video_id)


def get_album(browse_id: str) -> dict:
    return client().get_album(browse_id)


def get_artist(channel_id: str) -> dict:
    return client().get_artist(channel_id)


def get_playlist(playlist_id: str, limit: int = 100) -> dict:
    return client().get_playlist(playlist_id, limit=limit)


def get_home(limit: int = 5) -> list[dict]:
    return client().get_home(limit=limit)


def get_charts(country: str = "ZZ") -> dict:
    return client().get_charts(country=country)


def get_lyrics(video_id: str) -> dict | None:
    yt = client()
    watch = yt.get_watch_playlist(video_id)
    browse_id = watch.get("lyrics")
    if not browse_id:
        return None
    return yt.get_lyrics(browse_id)


def suggest(q: str) -> list[str]:
    """YouTube's autocomplete suggestions for the search input."""
    return client().get_search_suggestions(q)


# ---------- Per-user storage ----------

_user_clients: dict[int, YTMusic] = {}


def get_user_yt_payload(user_id: int) -> dict | None:
    """Return the user's saved YT auth JSON, or None if not linked."""
    with db.conn() as c:
        row = c.execute(
            "SELECT payload FROM yt_auth WHERE user_id = %s", (user_id,)
        ).fetchone()
    return row["payload"] if row else None


def save_user_yt_payload(user_id: int, payload: dict) -> None:
    """Upsert the user's YT auth blob and invalidate any cached client."""
    with db.conn() as c:
        c.execute(
            """
            INSERT INTO yt_auth (user_id, payload, updated_at)
                 VALUES (%s, %s, NOW())
            ON CONFLICT (user_id)
              DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
            """,
            (user_id, Json(payload)),
        )
    _user_clients.pop(user_id, None)


def delete_user_yt_auth(user_id: int) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM yt_auth WHERE user_id = %s", (user_id,))
    _user_clients.pop(user_id, None)


def user_has_youtube(user_id: int) -> bool:
    return get_user_yt_payload(user_id) is not None


def client_for_user(user_id: int) -> YTMusic:
    """Return the per-user YTMusic client. 412 if not linked."""
    if user_id in _user_clients:
        return _user_clients[user_id]
    payload = get_user_yt_payload(user_id)
    if payload is None:
        raise HTTPException(
            status_code=412,
            detail=(
                "YouTube Music not linked for this account. "
                "POST /me/yt-oauth/start to begin the Sign-in-with-Google flow."
            ),
        )
    # OAuth token vs browser headers: detect by shape
    if "access_token" in payload and "refresh_token" in payload:
        from . import oauth as ytoauth
        yt = YTMusic(payload, oauth_credentials=ytoauth.get_credentials())
    else:
        # Browser-headers format — passing a dict to YTMusic also works.
        yt = YTMusic(payload)
    _user_clients[user_id] = yt
    return yt


# ---------- Browser-headers (cURL) auth — converts to JSON blob ----------

def save_user_youtube_browser_auth(user_id: int, raw: str) -> None:
    """Parse a cURL/headers string and save the resulting ytmusicapi headers
    dict into Postgres. (Legacy fallback — OAuth is preferred.)"""
    if "-H " in raw or "-b " in raw:
        headers = re.findall(r"-H '([^']+)'", raw)
        m = re.search(r"-b '([^']+)'", raw)
        if m:
            headers.append("cookie: " + m.group(1))
        headers_raw = "\n".join(headers)
    else:
        headers_raw = raw

    # ytm_setup writes a file — we have it write to a temp path and then read
    # the JSON back to store in DB.
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        ytm_setup(filepath=tmp_path, headers_raw=headers_raw)
        with open(tmp_path, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not parse the cURL/headers you provided: {e}",
        ) from e
    finally:
        try:
            import os
            os.unlink(tmp_path)
        except Exception:
            pass

    save_user_yt_payload(user_id, payload)

    # Sanity check
    try:
        YTMusic(payload).get_library_playlists(limit=1)
    except Exception as e:
        delete_user_yt_auth(user_id)
        raise HTTPException(
            status_code=400,
            detail=f"Auth saved but a test call failed — cookies may be invalid: {e}",
        ) from e
