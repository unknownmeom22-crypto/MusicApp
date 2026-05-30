"""Per-user 'recently played' history, stored locally in Postgres.

Each track appears once, at its most recent play time (we upsert played_at).
Returned tracks use the SearchResult-compatible shape so the mobile rendering
paths just work.
"""

from __future__ import annotations

from typing import Any

from . import db


def _row_to_track(r: dict[str, Any]) -> dict[str, Any]:
    artists = [{"name": a.strip(), "id": None} for a in (r["artists"] or "").split(",") if a.strip()]
    thumbs = [{"url": r["thumb_url"], "width": 0, "height": 0}] if r["thumb_url"] else []
    return {
        "videoId": r["video_id"],
        "title": r["title"],
        "artists": artists,
        "thumbnails": thumbs,
        "played_at": r["played_at"].isoformat() if r["played_at"] else None,
    }


def record(
    user_id: int,
    video_id: str,
    title: str,
    artists: str = "",
    thumb_url: str = "",
) -> None:
    """Mark a track as just-played. Upserts so each track keeps a single row at
    its latest play time."""
    if not video_id:
        return
    with db.conn() as c:
        c.execute(
            """
            INSERT INTO play_history (user_id, video_id, title, artists, thumb_url, played_at)
                 VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, video_id) DO UPDATE SET
                title     = EXCLUDED.title,
                artists   = EXCLUDED.artists,
                thumb_url = EXCLUDED.thumb_url,
                played_at = NOW()
            """,
            (user_id, video_id, title, artists, thumb_url),
        )


def list_for_user(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with db.conn() as c:
        rows = c.execute(
            """
            SELECT video_id, title, artists, thumb_url, played_at
              FROM play_history
             WHERE user_id = %s
          ORDER BY played_at DESC
             LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    return [_row_to_track(r) for r in rows]


def clear(user_id: int) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM play_history WHERE user_id = %s", (user_id,))
