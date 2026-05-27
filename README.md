# MusicApp backend

FastAPI server exposing YouTube Music search, library, and audio streaming.

## Setup

```powershell
# From D:\MusicApp\backend
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## (Optional) Log in to your YouTube Music account

Without this, search and public endpoints work but `/library/*` returns 401.

```powershell
.venv\Scripts\python scripts\setup_auth.py
```

Pick option 2 (Browser cookies) — it's easier than OAuth. Follow the prompts.
The script writes `browser.json` (or `oauth.json`) next to this README.

If you used the browser method, also edit `.env`:
```
AUTH_FILE=browser.json
AUTH_TYPE=browser
```

## Run

```powershell
.venv\Scripts\python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API docs: http://localhost:8000/docs (Swagger UI — try endpoints live)
- Health: http://localhost:8000/health

`--host 0.0.0.0` (instead of the default 127.0.0.1) is important if your phone
needs to reach this server over the LAN.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | `{ok, authed}` |
| GET | `/search?q=...&filter=songs&limit=20` | mixed or filtered |
| GET | `/song/{videoId}` | song metadata |
| GET | `/album/{browseId}` | album metadata |
| GET | `/artist/{channelId}` | artist page |
| GET | `/playlist/{playlistId}` | playlist with tracks |
| GET | `/lyrics/{videoId}` | lyrics (when available) |
| GET | `/home` | YT Music home shelves (better when authed) |
| GET | `/charts?country=ZZ` | charts |
| GET | `/library/playlists` | **auth required** |
| GET | `/library/liked` | **auth required** |
| GET | `/library/history` | **auth required** |
| POST | `/song/{id}/rate?rating=LIKE` | **auth required** |
| GET | `/stream/{videoId}` | `{url, content_type, duration}` |
| GET | `/stream/{videoId}/redirect` | 302 to the audio URL |

## Things to know

- **yt-dlp moves fast.** If stream resolution breaks, `pip install -U yt-dlp`
  is your first try.
- **Stream URLs expire** after a few hours. We cache them in-process for 5h.
  Restart the server if you suspect stale URLs.
- **Auth files contain credentials.** They're already in `.gitignore` — keep
  them out of git.
