# MusicApp backend

FastAPI server exposing YouTube Music search, audio streaming, and per-user
accounts (liked songs, playlists, history) stored locally in Postgres. There is
no YouTube account linking.

## Setup

```powershell
# From D:\MusicApp\backend
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Set `DATABASE_URL` and `JWT_SECRET` in `.env` (see `.env.example`). Users sign
up / log in from the app; their personal data is keyed to their account.

## (Optional) Global guest auth for better search

Entirely optional and **not** per-user — it's a single shared file used only to
improve public search/browse results. The app runs fine in plain guest mode
without it.

```powershell
.venv\Scripts\python scripts\setup_auth.py
```

Follow the prompts (paste request headers from a logged-in music.youtube.com
tab). The script writes `browser.json`, which is picked up automatically.

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
| GET | `/health` | `{ok, guest_authed}` |
| POST | `/auth/signup`, `/auth/login` | returns a JWT |
| GET | `/me` | current account (Bearer token) |
| GET | `/search?q=...&filter=songs&limit=20` | mixed or filtered |
| GET | `/song/{videoId}` | song metadata |
| GET | `/album/{browseId}` | album metadata |
| GET | `/artist/{channelId}` | artist page |
| GET | `/playlist/{playlistId}` | public playlist with tracks |
| GET | `/lyrics/{videoId}` | lyrics (when available) |
| GET | `/home` | guest home shelves |
| GET | `/charts?country=ZZ` | charts |
| GET | `/library/liked` · `PUT`/`DELETE` `/me/likes/{videoId}` | liked songs — **auth** |
| GET/POST | `/me/playlists`, `/me/playlists/{id}` (+ `/items/{videoId}`) | playlists — **auth** |
| GET | `/me/history` · `POST` `/me/history/{videoId}` | recently played — **auth** |
| GET | `/stream/{videoId}` | `{url, content_type, duration}` |
| GET | `/stream/{videoId}/redirect` | 302 to the audio URL |

## Things to know

- **yt-dlp moves fast.** If stream resolution breaks, `pip install -U yt-dlp`
  is your first try.
- **Stream URLs expire** after a few hours. We cache them in-process for 5h.
  Restart the server if you suspect stale URLs.
- **Auth files contain credentials.** They're already in `.gitignore` — keep
  them out of git.
