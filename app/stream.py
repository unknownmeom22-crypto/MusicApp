"""Stream URL resolver using yt-dlp.

ytmusicapi gives us a video_id, but not a playable audio URL. We use yt-dlp to ask
YouTube's player API for the available audio formats, then pick the best m4a/opus.

We cache resolved URLs in-process — YouTube stream URLs expire after a few hours,
so we use a TTL.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Iterator

import httpx
from yt_dlp import YoutubeDL

from .config import settings


# Simple in-process TTL cache: { video_id: (expires_at, *fields) }
# Fields match _CACHE_FIELDS below (url, content_type, duration, title, codec, bitrate).
_cache: dict[str, tuple[Any, ...]] = {}

# yt-dlp writes the cookie jar back to the cookiefile after each run, so it must
# be writable. Render Secret Files (/etc/secrets/*) are read-only, so we work off
# a copy seeded into tmp. Re-seeded on container restart (which Render does when
# the secret changes), so updating the cookies just means redeploying.
_WRITABLE_COOKIES = os.path.join(tempfile.gettempdir(), "ytdlp-cookies.txt")


def _cookies_source() -> str | None:
    """Locate the cookies file (may be read-only), or None if we fly blind."""
    if settings.yt_cookies_file and os.path.exists(settings.yt_cookies_file):
        return settings.yt_cookies_file
    if os.path.exists("/etc/secrets/cookies.txt"):  # Render Secret Files mount
        return "/etc/secrets/cookies.txt"
    if os.path.exists("cookies.txt"):  # local convention
        return "cookies.txt"
    return None


def _cookies_path() -> str | None:
    """Return a WRITABLE cookies file path for yt-dlp, or None if we have none."""
    src = _cookies_source()
    if not src:
        return None
    try:
        # Seed the writable copy once; thereafter let yt-dlp read+write it so
        # refreshed cookies persist for the container's lifetime.
        if not os.path.exists(_WRITABLE_COOKIES):
            shutil.copyfile(src, _WRITABLE_COOKIES)
        return _WRITABLE_COOKIES
    except OSError:
        # Copy failed — fall back to the source (read-only writes may still warn,
        # but reading the cookies is what matters most).
        return src


# Which YouTube player clients to try, in order, until one yields a playable
# audio URL. Different clients return formats differently depending on IP and
# auth: on residential IPs `default` is usually enough, but on datacenter IPs
# (Render) — even WITH cookies — some clients return SABR/no-URL formats and
# fail with "Requested format is not available", while a different client still
# gives a direct URL. We can't know which from here, so we try several and take
# the first that works (the result is cached, so the cost is paid once).
_CLIENT_STRATEGIES: list[list[str]] = [
    ["default"],
    ["android_vr", "tv_embedded"],
    ["ios"],
    ["web_safari", "mweb"],
    ["tv"],
    ["android", "web"],
]


def _ydl_opts(
    player_client: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestaudio/best",
        "extract_flat": False,
        "noplaylist": True,
        "extractor_args": {
            "youtube": {
                "player_client": player_client or ["default", "android_vr", "tv_embedded"],
            },
        },
    }
    cookies = _cookies_path()
    if cookies:
        opts["cookiefile"] = cookies
    if settings.yt_proxy:
        opts["proxy"] = settings.yt_proxy
    if extra:
        opts.update(extra)
    return opts


# Kept for backward-compat with any other module that imported it
_YDL_OPTS = _ydl_opts()


def _extract(video_id: str) -> dict[str, Any]:
    """Resolve playable info, trying each client strategy until one returns a
    usable URL. Raises the last error if every strategy fails."""
    url = f"https://music.youtube.com/watch?v={video_id}"
    last_err: Exception | None = None
    for clients in _CLIENT_STRATEGIES:
        try:
            with YoutubeDL(_ydl_opts(player_client=clients)) as ydl:
                info = ydl.extract_info(url, download=False)
            if info and info.get("url"):
                return info
            last_err = RuntimeError(f"no URL from clients={clients}")
        except Exception as e:  # noqa: BLE001 — try the next client strategy
            last_err = e
    raise last_err or RuntimeError(f"No playable format for {video_id}")


# ---------- Audio proxy ----------
# Resolved googlevideo URLs are locked to the IP that fetched them. When we
# extract through a residential proxy, the URL is bound to the proxy's IP — so
# the phone can't fetch it directly. Instead we stream the bytes through the
# backend, fetching upstream through the SAME proxy, with Range support so the
# client can seek.

_PROXY_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# Headers worth forwarding from the upstream response back to the client.
_PASSTHROUGH_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")


def proxy_audio(
    video_id: str,
    range_header: str | None = None,
) -> tuple[int, dict[str, str], Iterator[bytes]]:
    """Open the resolved audio stream THROUGH the residential proxy and return
    (status_code, response_headers, byte_iterator) for a StreamingResponse.
    Retries once with a fresh URL if the upstream 403s (expired/IP mismatch)."""
    for attempt in range(2):
        info = resolve(video_id, force_refresh=(attempt == 1))
        upstream = info["url"]

        req_headers = dict(_PROXY_FETCH_HEADERS)
        if range_header:
            req_headers["Range"] = range_header

        client = httpx.Client(
            proxy=settings.yt_proxy or None,
            timeout=httpx.Timeout(60.0, read=300.0),
            follow_redirects=True,
        )
        resp = client.send(client.build_request("GET", upstream, headers=req_headers), stream=True)

        if resp.status_code in (403, 410) and attempt == 0:
            # Likely an expired/IP-locked URL — drop it and re-resolve once.
            resp.close()
            client.close()
            invalidate(video_id)
            continue

        headers = {k: v for k, v in resp.headers.items() if k.lower() in _PASSTHROUGH_HEADERS}
        headers.setdefault("accept-ranges", "bytes")

        def _body() -> Iterator[bytes]:
            try:
                for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                resp.close()
                client.close()

        return resp.status_code, headers, _body()

    raise RuntimeError(f"Audio upstream kept failing for {video_id}")


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
