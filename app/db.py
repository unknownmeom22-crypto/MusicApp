"""Postgres connection pool + schema migrations.

Uses psycopg3 with a small pool. The schema is two tables:

    users      — email/password accounts
    yt_auth    — per-user YouTube Music auth blob (JSON), 1:1 with users
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash BYTEA NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS yt_auth (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    payload    JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS liked_songs (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    video_id   TEXT NOT NULL,
    title      TEXT NOT NULL,
    artists    TEXT NOT NULL DEFAULT '',
    thumb_url  TEXT NOT NULL DEFAULT '',
    added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, video_id)
);

CREATE INDEX IF NOT EXISTS liked_songs_user_added_idx
    ON liked_songs (user_id, added_at DESC);
"""


_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    """Return the process-wide connection pool, lazy-initialized."""
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not configured. Set it in backend/.env "
                "(e.g. postgresql://user:pass@host:port/dbname)."
            )
        # min_size=1 keeps at least one warm connection; max_size=5 is plenty
        # for a 10-person team. Supabase free tier allows ~60 connections.
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


@contextmanager
def conn() -> Iterator[Connection]:
    """Borrow a connection from the pool. Commits on success, rolls back on error."""
    with pool().connection() as c:
        yield c


def init() -> None:
    """Run schema migrations. Idempotent — safe to call on every startup."""
    with conn() as c:
        c.execute(SCHEMA)
