"""Per-user playlists, stored locally in Postgres.

Playlists belong to the logged-in account (user_id). Each playlist holds a set
of tracks in `playlist_items`. Tracks are returned in a SearchResult-compatible
shape (artists/thumbnails as arrays of objects) so the existing mobile
rendering paths just work.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from . import db


# ---------- Helpers ----------

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


def _owned_or_404(c, user_id: int, playlist_id: int) -> dict[str, Any]:
    """Fetch a playlist row, raising 404 if it doesn't exist or isn't the
    caller's. Centralizes the ownership check so no endpoint can leak across
    accounts."""
    row = c.execute(
        "SELECT id, name, created_at FROM playlists WHERE id = %s AND user_id = %s",
        (playlist_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return row


# ---------- Playlist CRUD ----------

def list_playlists(user_id: int) -> list[dict[str, Any]]:
    """All of the user's playlists, each with a track count and a cover thumb
    (the most recently added track's thumbnail)."""
    with db.conn() as c:
        rows = c.execute(
            """
            SELECT p.id,
                   p.name,
                   p.created_at,
                   COUNT(i.video_id)                          AS count,
                   (SELECT thumb_url
                      FROM playlist_items
                     WHERE playlist_id = p.id AND thumb_url <> ''
                  ORDER BY added_at DESC
                     LIMIT 1)                                 AS thumb_url
              FROM playlists p
         LEFT JOIN playlist_items i ON i.playlist_id = p.id
             WHERE p.user_id = %s
          GROUP BY p.id
          ORDER BY p.created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "count": r["count"],
            "thumb_url": r["thumb_url"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


def create_playlist(user_id: int, name: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name is required")
    with db.conn() as c:
        row = c.execute(
            "INSERT INTO playlists (user_id, name) VALUES (%s, %s) RETURNING id, name, created_at",
            (user_id, name),
        ).fetchone()
    return {
        "id": row["id"],
        "name": row["name"],
        "count": 0,
        "thumb_url": "",
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def rename_playlist(user_id: int, playlist_id: int, name: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name is required")
    with db.conn() as c:
        _owned_or_404(c, user_id, playlist_id)
        c.execute("UPDATE playlists SET name = %s WHERE id = %s", (name, playlist_id))
    return {"id": playlist_id, "name": name}


def delete_playlist(user_id: int, playlist_id: int) -> None:
    with db.conn() as c:
        _owned_or_404(c, user_id, playlist_id)
        # playlist_items rows cascade on delete.
        c.execute("DELETE FROM playlists WHERE id = %s", (playlist_id,))


def get_playlist(user_id: int, playlist_id: int) -> dict[str, Any]:
    """Playlist detail: metadata + ordered tracks."""
    with db.conn() as c:
        meta = _owned_or_404(c, user_id, playlist_id)
        rows = c.execute(
            """
            SELECT video_id, title, artists, thumb_url, added_at
              FROM playlist_items
             WHERE playlist_id = %s
          ORDER BY added_at DESC
            """,
            (playlist_id,),
        ).fetchall()
    tracks = [_row_to_track(r) for r in rows]
    return {
        "id": meta["id"],
        "name": meta["name"],
        "created_at": meta["created_at"].isoformat() if meta["created_at"] else None,
        "tracks": tracks,
    }


# ---------- Track membership ----------

def add_item(
    user_id: int,
    playlist_id: int,
    video_id: str,
    title: str,
    artists: str = "",
    thumb_url: str = "",
) -> None:
    with db.conn() as c:
        _owned_or_404(c, user_id, playlist_id)
        c.execute(
            """
            INSERT INTO playlist_items (playlist_id, video_id, title, artists, thumb_url)
                 VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (playlist_id, video_id) DO UPDATE SET
                title     = EXCLUDED.title,
                artists   = EXCLUDED.artists,
                thumb_url = EXCLUDED.thumb_url
            """,
            (playlist_id, video_id, title, artists, thumb_url),
        )


def remove_item(user_id: int, playlist_id: int, video_id: str) -> None:
    with db.conn() as c:
        _owned_or_404(c, user_id, playlist_id)
        c.execute(
            "DELETE FROM playlist_items WHERE playlist_id = %s AND video_id = %s",
            (playlist_id, video_id),
        )
