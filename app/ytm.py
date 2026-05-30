"""Thin wrapper around ytmusicapi.

A single global guest client serves all public metadata endpoints (search,
home, charts, song/album/artist/playlist lookups). There is no per-user
YouTube auth — all user data (likes, playlists, history) lives in Postgres.

An optional global auth file (browser.json) can be supplied to improve guest
results, but it is not required.
"""

from __future__ import annotations

from functools import lru_cache

from ytmusicapi import YTMusic

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
    """Best-effort lyrics. ytmusicapi can raise (e.g. KeyError: 'endpoint') when
    YouTube changes a watch-playlist payload or a video simply has no lyrics
    tab — treat any failure as "no lyrics" rather than bubbling up a 500."""
    yt = client()
    try:
        watch = yt.get_watch_playlist(video_id)
        browse_id = watch.get("lyrics")
        if not browse_id:
            return None
        return yt.get_lyrics(browse_id)
    except Exception:
        return None


def suggest(q: str) -> list[str]:
    """YouTube's autocomplete suggestions for the search input."""
    return client().get_search_suggestions(q)
