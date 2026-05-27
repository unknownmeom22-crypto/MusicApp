"""Per-user liked songs, stored locally in Postgres.

This is the source of truth for whether a track is liked in our UI. When the
user has a YT account linked we additionally mirror the like to YouTube via
`rate_song` on a best-effort basis — failures there don't fail the request.

Rows are returned in a SearchResult-compatible shape (artists/thumbnails as
arrays of objects) so the existing mobile rendering paths just work.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from . import db, ytm


def add(
    user_id: int,
    video_id: str,
    title: str,
    artists: str = "",
    thumb_url: str = "",
) -> None:
    with db.conn() as c:
        c.execute(
            """
            INSERT INTO liked_songs (user_id, video_id, title, artists, thumb_url)
                 VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, video_id) DO UPDATE SET
                title     = EXCLUDED.title,
                artists   = EXCLUDED.artists,
                thumb_url = EXCLUDED.thumb_url
            """,
            (user_id, video_id, title, artists, thumb_url),
        )
    _mirror_to_yt(user_id, video_id, "LIKE")


def remove(user_id: int, video_id: str) -> None:
    with db.conn() as c:
        c.execute(
            "DELETE FROM liked_songs WHERE user_id = %s AND video_id = %s",
            (user_id, video_id),
        )
    _mirror_to_yt(user_id, video_id, "INDIFFERENT")


def is_liked(user_id: int, video_id: str) -> bool:
    with db.conn() as c:
        row = c.execute(
            "SELECT 1 FROM liked_songs WHERE user_id = %s AND video_id = %s",
            (user_id, video_id),
        ).fetchone()
    return row is not None


def list_for_user(user_id: int, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    """Return rows shaped like ytmusicapi `tracks` entries."""
    with db.conn() as c:
        rows = c.execute(
            """
            SELECT video_id, title, artists, thumb_url, added_at
              FROM liked_songs
             WHERE user_id = %s
          ORDER BY added_at DESC
             LIMIT %s OFFSET %s
            """,
            (user_id, limit, offset),
        ).fetchall()
    return [_row_to_track(r) for r in rows]


def _row_to_track(r: dict[str, Any]) -> dict[str, Any]:
    artists = [{"name": a.strip(), "id": None} for a in (r["artists"] or "").split(",") if a.strip()]
    thumbs = [{"url": r["thumb_url"], "width": 0, "height": 0}] if r["thumb_url"] else []
    return {
        "videoId": r["video_id"],
        "title": r["title"],
        "artists": artists,
        "thumbnails": thumbs,
        "added_at": r["added_at"].isoformat() if r["added_at"] else None,
    }


def _mirror_to_yt(user_id: int, video_id: str, rating: str) -> None:
    if not ytm.user_has_youtube(user_id):
        return
    try:
        ytm.client_for_user(user_id).rate_song(video_id, rating)
    except HTTPException:
        # 412 — YT not actually usable for this user; silently skip
        pass
    except Exception:
        # YT-side failures must not break local likes
        pass
