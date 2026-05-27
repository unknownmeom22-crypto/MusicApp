import secrets
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ----- legacy single-tenant YT Music auth (used as guest fallback) -----
    # Path to a global ytmusicapi auth file used when no user is signed in.
    # If the file does not exist, the backend runs in "guest" mode (public
    # search only, no personal library).
    auth_file: str = "browser.json"
    auth_type: str = "browser"  # "oauth" or "browser"

    # ----- CORS -----
    cors_origins: list[str] = ["*"]

    # ----- Stream cache TTL (yt-dlp URLs expire after ~6h) -----
    stream_cache_ttl: int = 60 * 60 * 5

    # ----- yt-dlp cookies (defeats YouTube's "confirm you're not a bot" block
    # that hits any cloud-VM IP). Export a cookies.txt from a browser logged
    # into youtube.com and either put it at this path or mount it via Render's
    # Secret Files feature at /etc/secrets/cookies.txt. -----
    yt_cookies_file: str = ""

    # ----- Multi-user auth -----
    # JWT secret. Override via .env; otherwise a random one is generated per
    # process (which invalidates all sessions on restart — fine for dev, set
    # one explicitly in prod).
    jwt_secret: str = secrets.token_urlsafe(48)
    jwt_alg: str = "HS256"
    # Token lifetime in days.
    jwt_ttl_days: int = 30

    # Postgres connection string. Set via env in production (Supabase, Neon,
    # Render Postgres, etc.). For local-only dev you could point at a local
    # postgres, or leave it blank to skip DB init (won't be able to log in).
    database_url: str = ""

    # ----- Google OAuth (Sign in with Google → YT Music) -----
    # "TVs and Limited Input devices" OAuth Client. Created once in
    # https://console.cloud.google.com/. Until set, /me/yt-oauth/* returns 503.
    google_client_id: str = ""
    google_client_secret: str = ""


settings = Settings()

BACKEND_DIR = Path(__file__).resolve().parent.parent


def auth_path() -> Path:
    """Path to the global (guest) YT Music auth file."""
    return BACKEND_DIR / settings.auth_file


