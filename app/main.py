"""FastAPI entry point.

Run with:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

Public routes (no auth needed):
    /, /health, /search, /song, /album, /artist, /playlist, /lyrics,
    /home, /charts, /stream, /stream/{id}/redirect

Auth routes:
    POST /auth/signup, POST /auth/login

User routes (require Bearer token) — all data stored locally per account:
    GET  /me                  — current user info
    GET  /library/liked       — liked songs
    *    /me/likes/*          — like / unlike / status
    *    /me/playlists/*      — create / view / edit playlists
    *    /me/history          — recently-played history
"""

from __future__ import annotations

import os
import subprocess
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import auth, db, download, history, likes, playlists, stream, ytm
from .config import settings

app = FastAPI(title="MusicApp Backend", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_pot_provider_proc: subprocess.Popen | None = None


def _start_pot_provider() -> None:
    """Launch the bundled bgutil PO Token provider HTTP server (127.0.0.1:4416)
    so yt-dlp's plugin can fetch tokens. Done here (not via the container CMD)
    so it runs regardless of how uvicorn is started. No-op when the provider
    isn't bundled (e.g. local dev), so it's harmless everywhere."""
    global _pot_provider_proc
    cwd = os.environ.get("BGUTIL_PROVIDER_CWD", "/app")
    main_js = os.path.join(cwd, "build", "main.js")
    if not os.path.exists(main_js):
        print(f"[pot] provider not bundled ({main_js} missing) — skipping")
        return
    try:
        _pot_provider_proc = subprocess.Popen(
            ["node", "build/main.js"],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[pot] launched provider (pid {_pot_provider_proc.pid}) on 127.0.0.1:4416")
    except Exception as e:  # noqa: BLE001
        print(f"[pot] failed to launch provider: {e}")


@app.on_event("startup")
def _startup() -> None:
    db.init()
    _start_pot_provider()


# ---------- Meta ----------

@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "service": "MusicApp Backend",
        "version": "0.2.0",
        "guest_authed": ytm.is_authed(),
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "guest_authed": ytm.is_authed()}


# ---------- Auth ----------

@app.post("/auth/signup", response_model=auth.TokenOut)
def signup(body: auth.SignupIn) -> auth.TokenOut:
    uid, email = auth.create_user(body.email, body.password)
    return auth.TokenOut(access_token=auth.issue_token(uid, email), user_id=uid, email=email)


@app.post("/auth/login", response_model=auth.TokenOut)
def login(body: auth.LoginIn) -> auth.TokenOut:
    user = auth.find_user_by_email(body.email)
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return auth.TokenOut(
        access_token=auth.issue_token(user["id"], user["email"]),
        user_id=user["id"],
        email=user["email"],
    )


@app.get("/me", response_model=auth.UserOut)
def me(user: Annotated[dict, Depends(auth.get_current_user)]) -> auth.UserOut:
    return auth.UserOut(id=user["id"], email=user["email"])


# ---------- Public: search & browse ----------

@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    filter: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
) -> list[dict]:
    return ytm.search(q, filter=filter, limit=limit)


@app.get("/search/suggest")
def search_suggest(q: str = Query(..., min_length=1)) -> list[str]:
    """YT autocomplete — cheap (~50-100ms), call per keystroke."""
    try:
        return ytm.suggest(q)
    except Exception:
        return []


_SEARCH_ALL_TYPES = ("songs", "artists", "albums", "playlists", "videos")


@app.get("/search/all")
def search_all(
    q: str = Query(..., min_length=1),
    per_category: int = Query(12, ge=1, le=30),
) -> dict[str, list[dict]]:
    """Parallel fetch of each category — gives a real "browse-style" mixed view.
    ytmusicapi's own mixed search returns ~4 items total, which is useless."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(_SEARCH_ALL_TYPES)) as ex:
        futures = {t: ex.submit(ytm.search, q, t, per_category) for t in _SEARCH_ALL_TYPES}
        out: dict[str, list[dict]] = {}
        for t, f in futures.items():
            try:
                out[t] = f.result()
            except Exception:
                out[t] = []
    return out


@app.get("/song/{video_id}")
def song(video_id: str) -> dict:
    return ytm.get_song(video_id)


@app.get("/album/{browse_id}")
def album(browse_id: str) -> dict:
    return ytm.get_album(browse_id)


@app.get("/artist/{channel_id}")
def artist(channel_id: str) -> dict:
    return ytm.get_artist(channel_id)


@app.get("/playlist/{playlist_id}")
def playlist(playlist_id: str, limit: int = Query(100, ge=1, le=500)) -> dict:
    return ytm.get_playlist(playlist_id, limit=limit)


@app.get("/lyrics/{video_id}")
def lyrics(video_id: str) -> dict | None:
    return ytm.get_lyrics(video_id)


@app.get("/home")
def home(limit: int = Query(5, ge=1, le=20)) -> list[dict]:
    """Guest home feed (public, same for everyone)."""
    return ytm.get_home(limit=limit)


@app.get("/charts")
def charts(country: str = "ZZ") -> dict:
    return ytm.get_charts(country=country)


# ---------- Liked songs (local) ----------

class LikeIn(BaseModel):
    title: str
    artists: str = ""
    thumb_url: str = ""


@app.get("/library/liked")
def library_liked(
    user: Annotated[dict, Depends(auth.get_current_user)],
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    """Liked songs from our local store."""
    return {"tracks": likes.list_for_user(user["id"], limit=limit)}


@app.get("/me/likes/{video_id}")
def like_get(
    video_id: str,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    return {"liked": likes.is_liked(user["id"], video_id)}


@app.put("/me/likes/{video_id}")
def like_put(
    video_id: str,
    body: LikeIn,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    likes.add(user["id"], video_id, body.title, body.artists, body.thumb_url)
    return {"liked": True}


@app.delete("/me/likes/{video_id}")
def like_delete(
    video_id: str,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    likes.remove(user["id"], video_id)
    return {"liked": False}


# ---------- Playlists (local, per-user) ----------

class PlaylistIn(BaseModel):
    name: str


class PlaylistItemIn(BaseModel):
    title: str
    artists: str = ""
    thumb_url: str = ""


@app.get("/me/playlists")
def playlists_list(user: Annotated[dict, Depends(auth.get_current_user)]) -> list[dict]:
    return playlists.list_playlists(user["id"])


@app.post("/me/playlists")
def playlists_create(
    body: PlaylistIn,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    return playlists.create_playlist(user["id"], body.name)


@app.get("/me/playlists/{playlist_id}")
def playlists_get(
    playlist_id: int,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    return playlists.get_playlist(user["id"], playlist_id)


@app.patch("/me/playlists/{playlist_id}")
def playlists_rename(
    playlist_id: int,
    body: PlaylistIn,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    return playlists.rename_playlist(user["id"], playlist_id, body.name)


@app.delete("/me/playlists/{playlist_id}")
def playlists_delete(
    playlist_id: int,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    playlists.delete_playlist(user["id"], playlist_id)
    return {"ok": True}


@app.put("/me/playlists/{playlist_id}/items/{video_id}")
def playlists_add_item(
    playlist_id: int,
    video_id: str,
    body: PlaylistItemIn,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    playlists.add_item(
        user["id"], playlist_id, video_id, body.title, body.artists, body.thumb_url
    )
    return {"ok": True}


@app.delete("/me/playlists/{playlist_id}/items/{video_id}")
def playlists_remove_item(
    playlist_id: int,
    video_id: str,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    playlists.remove_item(user["id"], playlist_id, video_id)
    return {"ok": True}


# ---------- Play history (local, per-user) ----------

class HistoryIn(BaseModel):
    title: str
    artists: str = ""
    thumb_url: str = ""


@app.get("/me/history")
def history_list(
    user: Annotated[dict, Depends(auth.get_current_user)],
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    return {"tracks": history.list_for_user(user["id"], limit=limit)}


@app.post("/me/history/{video_id}")
def history_record(
    video_id: str,
    body: HistoryIn,
    user: Annotated[dict, Depends(auth.get_current_user)],
) -> dict:
    history.record(user["id"], video_id, body.title, body.artists, body.thumb_url)
    return {"ok": True}


@app.delete("/me/history")
def history_clear(user: Annotated[dict, Depends(auth.get_current_user)]) -> dict:
    history.clear(user["id"])
    return {"ok": True}


# ---------- Stream ----------

@app.get("/stream/{video_id}")
def stream_info(video_id: str, fresh: bool = False) -> dict:
    """Resolve a playable audio URL. Pass ?fresh=true to bypass the cache when
    the client detects an expired URL."""
    try:
        return stream.resolve(video_id, force_refresh=fresh)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Stream resolution failed: {e}") from e


@app.get("/debug/formats/{video_id}")
def debug_formats(video_id: str) -> dict:
    """TEMP diagnostic — lists the audio formats each client strategy sees on
    this server's IP (helps debug datacenter SABR/format issues)."""
    return stream.debug_formats(video_id)


@app.get("/stream/{video_id}/redirect")
def stream_redirect(video_id: str):
    try:
        info = stream.resolve(video_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Stream resolution failed: {e}") from e
    return RedirectResponse(url=info["url"], status_code=302)


# ---------- Downloads ----------

@app.get("/download/audio/{video_id}")
def download_audio(video_id: str) -> StreamingResponse:
    """Proxy-stream the best-audio YT URL with a Content-Disposition header so
    browsers save it instead of navigating."""
    try:
        info = download.get_audio_info(video_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not resolve audio: {e}") from e
    filename = download.audio_filename(info["title"], info["ext"])
    return StreamingResponse(
        download.stream_audio(info["url"]),
        media_type=info["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/download/video/{video_id}")
def download_video(video_id: str) -> FileResponse:
    """Download bestvideo+bestaudio (ffmpeg-merged) and return as mp4. Requires
    ffmpeg on PATH; falls back to single-file best (~720p) if merge fails."""
    try:
        path, filename = download.download_video_to_temp(video_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not download video: {e}") from e
    return FileResponse(
        path=str(path),
        media_type="video/mp4",
        filename=filename,
        background=BackgroundTask(download.cleanup_file, path),
    )
