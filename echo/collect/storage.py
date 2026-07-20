"""SQLite storage with privacy-preserving anonymisation at write time.

Commenter identities are never persisted in the clear. Each author channel id is
mapped to a salted BLAKE2b digest before storage, so the on-disk dataset
contains only anonymised network nodes (satisfying the ethics/data statement in
the manuscript). The salt is generated once per collection run and is *not*
stored, making re-identification infeasible from the released artefact while
preserving within-dataset identity for edge construction.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id      TEXT PRIMARY KEY,
    event         TEXT NOT NULL,
    channel_id    TEXT,
    title         TEXT,
    published_at  TEXT,
    view_count    INTEGER,
    like_count    INTEGER,
    comment_count INTEGER,
    duration      TEXT,
    is_innovation INTEGER DEFAULT 0    -- IMAX/70mm/format-framed (RQ3 treatment)
);
CREATE TABLE IF NOT EXISTS channels (
    channel_id    TEXT PRIMARY KEY,
    title         TEXT,
    subscribers   INTEGER,
    video_count   INTEGER,
    view_count    INTEGER,
    published_at  TEXT
);
CREATE TABLE IF NOT EXISTS comments (
    comment_id    TEXT PRIMARY KEY,
    video_id      TEXT NOT NULL,
    event         TEXT NOT NULL,
    author_hash   TEXT NOT NULL,       -- anonymised node id
    parent_id     TEXT,                -- NULL for top-level; else parent comment
    published_at  TEXT NOT NULL,
    like_count    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id);
CREATE INDEX IF NOT EXISTS idx_comments_event ON comments(event);
CREATE INDEX IF NOT EXISTS idx_comments_author ON comments(author_hash);
"""


class Store:
    def __init__(self, path: str | Path, salt: bytes | None = None):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        # per-run salt; ephemeral so hashes cannot be reversed post hoc
        self._salt = salt if salt is not None else os.urandom(16)

    def anonymise(self, author_channel_id: str) -> str:
        h = hashlib.blake2b(author_channel_id.encode("utf-8"),
                            salt=self._salt, digest_size=16)
        return h.hexdigest()

    # -- writers -----------------------------------------------------------
    def upsert_video(self, row: dict) -> None:
        self.conn.execute(
            """INSERT INTO videos(video_id,event,channel_id,title,published_at,
                   view_count,like_count,comment_count,duration,is_innovation)
               VALUES(:video_id,:event,:channel_id,:title,:published_at,
                   :view_count,:like_count,:comment_count,:duration,:is_innovation)
               ON CONFLICT(video_id) DO UPDATE SET
                   view_count=excluded.view_count,
                   like_count=excluded.like_count,
                   comment_count=excluded.comment_count,
                   is_innovation=excluded.is_innovation""", row)

    def upsert_channel(self, row: dict) -> None:
        self.conn.execute(
            """INSERT INTO channels(channel_id,title,subscribers,video_count,
                   view_count,published_at)
               VALUES(:channel_id,:title,:subscribers,:video_count,
                   :view_count,:published_at)
               ON CONFLICT(channel_id) DO UPDATE SET
                   subscribers=excluded.subscribers,
                   video_count=excluded.video_count,
                   view_count=excluded.view_count""", row)

    def insert_comment(self, row: dict) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO comments(comment_id,video_id,event,
                   author_hash,parent_id,published_at,like_count)
               VALUES(:comment_id,:video_id,:event,:author_hash,:parent_id,
                   :published_at,:like_count)""", row)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # -- readers -----------------------------------------------------------
    def counts(self) -> dict[str, int]:
        cur = self.conn.cursor()
        return {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("videos", "channels", "comments")}
