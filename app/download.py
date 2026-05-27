"""Audio / video file downloads.

Audio: yt-dlp gives us a direct YT CDN URL for the best audio-only stream; we
proxy-stream that to the client with a Content-Disposition header so browsers
save it instead of navigating.

Video: YouTube splits higher-than-720p content into separate video + audio
streams. We let yt-dlp download both and ffmpeg merge them into a single mp4
in a temp file, then stream that file to the client. The temp file is removed
after the response finishes.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import httpx
from yt_dlp import YoutubeDL

from . import stream as _stream


@lru_cache(maxsize=1)
def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


_PLAYER_CLIENTS = ["tv_embedded", "mweb", "ios"]


def _with_cookies(opts: dict[str, Any]) -> dict[str, Any]:
    cookies = _stream._cookies_path()
    if cookies:
        opts.setdefault("cookiefile", cookies)
    # Same bot-block bypass as stream.py uses.
    opts.setdefault("extractor_args", {"youtube": {"player_client": _PLAYER_CLIENTS}})
    return opts


# Common headers — YT will 403 some requests without a real-looking UA.
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def _safe_filename(name: str, ext: str) -> str:
    """Strip filesystem-unsafe chars and clamp length."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1F]', "_", name).strip(" .")
    if not cleaned:
        cleaned = "track"
    cleaned = cleaned[:120]
    return f"{cleaned}.{ext.lstrip('.')}"


# ---------- Audio ----------

def get_audio_info(video_id: str) -> dict[str, Any]:
    """Return {'url', 'ext', 'title', 'content_type'} for the best audio stream."""
    opts = _with_cookies({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestaudio/best",
        "noplaylist": True,
    })
    url = f"https://music.youtube.com/watch?v={video_id}"
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    stream_url = info.get("url")
    if not stream_url:
        raise RuntimeError(f"No audio URL for {video_id}")

    ext = info.get("ext", "m4a")
    content_type = {
        "m4a": "audio/mp4",
        "webm": "audio/webm",
        "opus": "audio/ogg",
        "mp3": "audio/mpeg",
    }.get(ext, "application/octet-stream")

    return {
        "url": stream_url,
        "ext": ext,
        "title": info.get("title") or video_id,
        "content_type": content_type,
    }


def stream_audio(url: str) -> Iterator[bytes]:
    """Yield bytes from the YT CDN URL, holding the httpx client open until done."""
    with httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as client:
        with client.stream("GET", url, headers=_FETCH_HEADERS) as r:
            r.raise_for_status()
            for chunk in r.iter_bytes(chunk_size=64 * 1024):
                if chunk:
                    yield chunk


# ---------- Video ----------

def download_video_to_temp(video_id: str) -> tuple[Path, str]:
    """Download bestvideo+bestaudio (merged via ffmpeg) into a temp mp4.

    Returns (path, suggested_filename). Caller is responsible for deleting the
    file after the response is sent.
    """
    tmp_dir = Path(tempfile.gettempdir()) / "musicapp_dl"
    tmp_dir.mkdir(exist_ok=True)

    # Unique stem so concurrent downloads don't collide
    stem = uuid.uuid4().hex
    outtmpl = str(tmp_dir / f"{stem}.%(ext)s")

    # With ffmpeg: prefer mp4-friendly streams and merge to mp4 (1080p+ possible).
    # Without ffmpeg: single-file `best` only (~720p max — YT splits higher res).
    if _has_ffmpeg():
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
        opts = _with_cookies({
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "restrictfilenames": False,
        })
    else:
        opts = _with_cookies({
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "best[ext=mp4]/best",
            "outtmpl": outtmpl,
            "restrictfilenames": False,
        })
    url = f"https://music.youtube.com/watch?v={video_id}"
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # yt-dlp returns the final merged path in `requested_downloads`; fall back
    # to scanning the temp dir for the matching stem.
    final_path: Path | None = None
    requested = info.get("requested_downloads") or []
    if requested:
        p = requested[0].get("filepath")
        if p:
            final_path = Path(p)
    if not final_path or not final_path.exists():
        for p in tmp_dir.glob(f"{stem}.*"):
            if p.is_file():
                final_path = p
                break

    if not final_path or not final_path.exists():
        raise RuntimeError(f"Download produced no file for {video_id}")

    suggested = _safe_filename(info.get("title") or video_id, final_path.suffix or ".mp4")
    return final_path, suggested


def cleanup_file(path: Path) -> None:
    """Best-effort delete of a temp file."""
    try:
        os.unlink(path)
    except Exception:
        pass


def audio_filename(title: str, ext: str) -> str:
    return _safe_filename(title, ext)
