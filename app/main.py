"""FastAPI entry point.

Run with:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

Public routes (no auth needed):
    /, /health, /search, /song, /album, /artist, /playlist, /lyrics,
    /home, /charts, /stream, /stream/{id}/redirect

Auth routes:
    POST /auth/signup, POST /auth/login

User routes (require Bearer token):
    GET  /me                — current user info
    POST /me/link-youtube   — paste cURL to enable library features
    GET  /library/*         — per-user library
    POST /song/{id}/rate    — per-user rating
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import auth, db, download, likes, oauth as ytoauth, stream, ytm
from .config import settings

app = FastAPI(title="MusicApp Backend", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    db.init()


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
    return auth.UserOut(
        id=user["id"],
        email=user["email"],
        has_youtube=ytm.user_has_youtube(user["id"]),
    )


@app.post("/me/link-youtube")
def link_youtube(
    user: Annotated[dict, Depends(auth.get_current_user)],
    payload: Annotated[dict, Body(..., examples=[{"raw": "curl '...' -H 'cookie: ...'"}])],
) -> dict:
    """Legacy cURL-paste fallback. Prefer /me/yt-oauth/start instead."""
    raw = (payload.get("raw") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Provide JSON body: { raw: '<cURL or headers>' }")
    ytm.save_user_youtube_browser_auth(user["id"], raw)
    return {"ok": True, "has_youtube": True}


@app.delete("/me/link-youtube")
def unlink_youtube(user: Annotated[dict, Depends(auth.get_current_user)]) -> dict:
    """Forget this user's YT Music auth (works for both OAuth and browser)."""
    ytm.delete_user_yt_auth(user["id"])
    ytoauth.cancel(user["id"])
    return {"ok": True}


# ---------- OAuth device flow (Sign in with Google) ----------

@app.get("/me/yt-oauth/config")
def yt_oauth_config() -> dict:
    """Public probe — does this server have Google OAuth configured?"""
    return {"configured": ytoauth.is_configured()}


@app.post("/me/yt-oauth/start")
def yt_oauth_start(user: Annotated[dict, Depends(auth.get_current_user)]) -> dict:
    """Begin device flow. Returns {verification_url, user_code, interval, expires_in}."""
    return ytoauth.start_device_flow(user["id"])


@app.post("/me/yt-oauth/poll")
def yt_oauth_poll(user: Annotated[dict, Depends(auth.get_current_user)]) -> dict:
    """Poll for completion. Returns {status: pending|success|denied|expired|error}."""
    return ytoauth.poll_device_flow(user["id"])


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
def home(
    user: Annotated[dict | None, Depends(auth.get_optional_user)] = None,
    limit: int = Query(5, ge=1, le=20),
) -> list[dict]:
    """If the caller is logged in and has linked YT Music, returns their
    personalized home. Otherwise falls back to the guest client."""
    if user and ytm.user_has_youtube(user["id"]):
        return ytm.client_for_user(user["id"]).get_home(limit=limit)
    return ytm.get_home(limit=limit)


@app.get("/charts")
def charts(country: str = "ZZ") -> dict:
    return ytm.get_charts(country=country)


# ---------- Library (per-user) ----------

@app.get("/library/playlists")
def library_playlists(
    user: Annotated[dict, Depends(auth.get_current_user)],
    limit: int = Query(25, ge=1, le=100),
) -> list[dict]:
    return ytm.client_for_user(user["id"]).get_library_playlists(limit=limit)


@app.get("/library/songs")
def library_songs(
    user: Annotated[dict, Depends(auth.get_current_user)],
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    return ytm.client_for_user(user["id"]).get_library_songs(limit=limit)


@app.get("/library/liked")
def library_liked(
    user: Annotated[dict, Depends(auth.get_current_user)],
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    """Liked songs from our local store (works even without YT linked)."""
    return {"tracks": likes.list_for_user(user["id"], limit=limit)}


# ---------- Likes (local, mirrored to YT when linked) ----------

class LikeIn(BaseModel):
    title: str
    artists: str = ""
    thumb_url: str = ""


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


@app.get("/library/history")
def library_history(user: Annotated[dict, Depends(auth.get_current_user)]) -> list[dict]:
    return ytm.client_for_user(user["id"]).get_history()


@app.post("/song/{video_id}/rate")
def rate(
    video_id: str,
    user: Annotated[dict, Depends(auth.get_current_user)],
    rating: str = Query(..., pattern="^(LIKE|DISLIKE|INDIFFERENT)$"),
):
    return ytm.client_for_user(user["id"]).rate_song(video_id, rating)


# ---------- Stream ----------

@app.get("/stream/{video_id}")
def stream_info(video_id: str, fresh: bool = False) -> dict:
    """Resolve a playable audio URL. Pass ?fresh=true to bypass the cache when
    the client detects an expired URL."""
    try:
        return stream.resolve(video_id, force_refresh=fresh)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Stream resolution failed: {e}") from e


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
