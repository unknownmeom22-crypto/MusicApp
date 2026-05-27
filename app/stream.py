"""Stream URL resolver using yt-dlp.

ytmusicapi gives us a video_id, but not a playable audio URL. We use yt-dlp to ask
YouTube's player API for the available audio formats, then pick the best m4a/opus.

We cache resolved URLs in-process — YouTube stream URLs expire after a few hours,
so we use a TTL.
"""

from __future__ import annotations

import time
from typing import Any

from yt_dlp import YoutubeDL

from .config import settings


# Simple in-process TTL cache: { video_id: (expires_at, *fields) }
# Fields match _CACHE_FIELDS below (url, content_type, duration, title, codec, bitrate).
_cache: dict[str, tuple[Any, ...]] = {}


_YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    # Prefer audio-only; fall back to any best format. yt-dlp picks the best
    # available audio across all containers (m4a, webm/opus, etc).
    "format": "bestaudio/best",
    "extract_flat": False,
    "noplaylist": True,
}


def _extract(video_id: str) -> dict[str, Any]:
    url = f"https://music.youtube.com/watch?v={video_id}"
    with YoutubeDL(_YDL_OPTS) as ydl:
        return ydl.extract_info(url, download=False)


_CACHE_FIELDS = ("url", "content_type", "duration", "title", "codec", "bitrate_kbps")


def resolve(video_id: str, force_refresh: bool = False) -> dict[str, str]:
    """Return {url, content_type, duration, title, codec, bitrate_kbps, cached}.

    Cached for `settings.stream_cache_ttl` seconds. Pass force_refresh=True to
    bypass the cache (used when a stream URL is detected to have expired).
    """
    now = time.time()
    if not force_refresh:
        cached = _cache.get(video_id)
        if cached and cached[0] > now:
            data = dict(zip(_CACHE_FIELDS, cached[1:]))
            data["cached"] = "true"
            return data

    info = _extract(video_id)
    url = info.get("url")
    if not url:
        raise RuntimeError(f"No playable URL for {video_id}")

    ext = info.get("ext", "m4a")
    content_type = {
        "m4a": "audio/mp4",
        "webm": "audio/webm",
        "opus": "audio/ogg",
        "mp3": "audio/mpeg",
    }.get(ext, "audio/mpeg")

    codec_raw = (info.get("acodec") or "").split(".")[0].lower()
    codec = {"mp4a": "AAC", "opus": "Opus", "vorbis": "Vorbis"}.get(codec_raw, codec_raw.upper() or "?")
    abr = info.get("abr")
    bitrate_kbps = int(round(abr)) if isinstance(abr, (int, float)) else 0

    duration = str(info.get("duration") or 0)
    title = info.get("title") or ""

    _cache[video_id] = (now + settings.stream_cache_ttl, url, content_type, duration, title, codec, str(bitrate_kbps))
    return {
        "url": url,
        "content_type": content_type,
        "duration": duration,
        "title": title,
        "codec": codec,
        "bitrate_kbps": str(bitrate_kbps),
        "cached": "false",
    }


def invalidate(video_id: str) -> None:
    """Drop the cached URL so the next resolve() goes back to YouTube."""
    _cache.pop(video_id, None)
