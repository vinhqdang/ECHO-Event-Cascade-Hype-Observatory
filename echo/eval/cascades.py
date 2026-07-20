"""Per-video comment-cascade metrics used as RQ3 outcomes.

For each video: breadth (distinct commenters), depth (longest reply chain),
volume (total comments) and reply ratio. These operationalise "cross-channel
diffusion" at the video level, matched against channel/video covariates.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd


def video_covariates_outcomes(db_path: str, event: str) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    vids = pd.read_sql_query(
        "SELECT v.video_id, v.channel_id, v.published_at, v.view_count, "
        "v.is_innovation, c.subscribers, c.video_count AS channel_video_count "
        "FROM videos v LEFT JOIN channels c ON v.channel_id=c.channel_id "
        "WHERE v.event=?", con, params=(event,))
    cmts = pd.read_sql_query(
        "SELECT video_id, comment_id, author_hash, parent_id, published_at "
        "FROM comments WHERE event=?", con, params=(event,))
    con.close()

    cmts["published_at"] = pd.to_datetime(cmts["published_at"], utc=True, errors="coerce")
    rows = []
    for vid, g in cmts.groupby("video_id"):
        breadth = g["author_hash"].nunique()
        volume = len(g)
        n_replies = g["parent_id"].notna().sum()
        depth = _max_depth(g)
        rows.append({"video_id": vid, "breadth": breadth, "volume": volume,
                     "depth": depth, "reply_ratio": n_replies / max(volume, 1)})
    out = vids.merge(pd.DataFrame(rows), on="video_id", how="inner")

    # engineered covariates (days_from_anchor added by add_days_from_anchor)
    out["log_subscribers"] = np.log1p(out["subscribers"].fillna(0))
    out["log_channel_video_count"] = np.log1p(out["channel_video_count"].fillna(0))
    out["log_prior_views"] = np.log1p(out["view_count"].fillna(0))
    pub = pd.to_datetime(out["published_at"], utc=True, errors="coerce")
    out["upload_hour"] = pub.dt.hour.fillna(12)
    return out


def add_days_from_anchor(df: pd.DataFrame, anchor_date: str) -> pd.DataFrame:
    anchor = pd.Timestamp(anchor_date, tz="UTC")
    pub = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
    df = df.copy()
    df["days_from_anchor"] = (pub - anchor).dt.total_seconds() / 86400.0
    df["days_from_anchor"] = df["days_from_anchor"].fillna(df["days_from_anchor"].median())
    return df


def _max_depth(g: pd.DataFrame) -> int:
    """Longest reply chain within a video (top-level = depth 1)."""
    parent = dict(zip(g["comment_id"], g["parent_id"]))
    memo: dict[str, int] = {}

    def depth(cid, seen=None):
        seen = seen or set()
        if cid in memo:
            return memo[cid]
        p = parent.get(cid)
        if p is None or p not in parent or cid in seen:
            memo[cid] = 1
            return 1
        seen.add(cid)
        d = 1 + depth(p, seen)
        memo[cid] = d
        return d

    return int(max((depth(c) for c in parent), default=0))
