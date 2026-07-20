"""Seed-driven, quota-efficient collection orchestrator.

Design (see manuscript Section 3.2): a small number of ``search.list`` seeding
passes (100u each) discover the top videos per event; the resolved video ids are
then expanded only through cheap endpoints. Video-publish date is *not* used to
bound the sample because the temporal unit of analysis is the *comment*
timestamp, not the upload time — trailers uploaded weeks earlier accrue comments
throughout the observation window. Comments are sliced to the analysis window
downstream (:mod:`echo.build.network`).

Innovation framing (RQ3 treatment): a video is flagged ``is_innovation`` when
its title/description surfaces the format-novelty narrative (IMAX, 70mm, film
format, etc.). This operationalises the "technology attribute" whose diffusion
effect RQ3 estimates.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .storage import Store
from .youtube_client import YouTubeClient, QuotaExceeded

logger = logging.getLogger("echo.collect")

INNOVATION_RE = re.compile(
    r"\b(imax|70\s*mm|70mm|15/70|film\s*format|large\s*format|"
    r"shot on film|celluloid|projection|aspect ratio)\b", re.I)


def _to_int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


@dataclass
class CollectionResult:
    event: str
    n_videos: int
    n_channels: int
    n_comments: int
    quota: dict


class Collector:
    def __init__(self, client: YouTubeClient, store: Store, settings: dict):
        self.client = client
        self.store = store
        self.s = settings

    # -- seeding -----------------------------------------------------------
    def discover_videos(self, seeds: dict, discovery_after: str,
                        discovery_before: str, per_query: int = 50) -> list[str]:
        """Run seed searches across several orderings; return unique video ids."""
        ids: list[str] = []
        seen: set[str] = set()
        frozen = seeds.get("frozen_video_ids") or []
        for vid in frozen:
            if vid not in seen:
                seen.add(vid); ids.append(vid)
        if frozen:
            logger.info("using %d frozen video ids", len(frozen))
            return ids
        for query in seeds.get("search_seeds", []):
            for order in ("relevance", "viewCount"):
                try:
                    items = self.client.search_videos(
                        query, discovery_after, discovery_before,
                        max_results=per_query, order=order)
                except QuotaExceeded:
                    logger.warning("quota exhausted during discovery")
                    return ids
                for it in items:
                    vid = it["id"].get("videoId")
                    if vid and vid not in seen:
                        seen.add(vid); ids.append(vid)
        cap = int(self.s["api"].get("max_videos_per_event", 0) or 0)
        if cap and len(ids) > cap:
            logger.info("capping discovered videos %d -> %d", len(ids), cap)
            ids = ids[:cap]
        logger.info("discovered %d unique videos", len(ids))
        return ids

    # -- metadata ----------------------------------------------------------
    def fetch_video_meta(self, video_ids: list[str], event: str) -> list[str]:
        channel_ids: set[str] = set()
        items = self.client.videos(video_ids)
        for it in items:
            sn = it["snippet"]; st = it.get("statistics", {})
            text = f"{sn.get('title','')} {sn.get('description','')}"
            self.store.upsert_video({
                "video_id": it["id"], "event": event,
                "channel_id": sn.get("channelId"),
                "title": sn.get("title"),
                "published_at": sn.get("publishedAt"),
                "view_count": _to_int(st.get("viewCount")),
                "like_count": _to_int(st.get("likeCount")),
                "comment_count": _to_int(st.get("commentCount")),
                "duration": it.get("contentDetails", {}).get("duration"),
                "is_innovation": int(bool(INNOVATION_RE.search(text))),
            })
            if sn.get("channelId"):
                channel_ids.add(sn["channelId"])
        self.store.commit()
        return list(channel_ids)

    def fetch_channel_meta(self, channel_ids: list[str]) -> None:
        for it in self.client.channels(channel_ids):
            st = it.get("statistics", {}); sn = it["snippet"]
            self.store.upsert_channel({
                "channel_id": it["id"], "title": sn.get("title"),
                "subscribers": _to_int(st.get("subscriberCount")),
                "video_count": _to_int(st.get("videoCount")),
                "view_count": _to_int(st.get("viewCount")),
                "published_at": sn.get("publishedAt"),
            })
        self.store.commit()

    # -- comments ----------------------------------------------------------
    def fetch_comments(self, video_id: str, event: str, max_pages: int) -> int:
        n = 0
        for th in self.client.comment_threads(video_id, max_pages=max_pages):
            top = th["snippet"]["topLevelComment"]
            tsn = top["snippet"]
            author = (tsn.get("authorChannelId", {}) or {}).get("value") \
                or tsn.get("authorDisplayName", "unknown")
            self.store.insert_comment({
                "comment_id": top["id"], "video_id": video_id, "event": event,
                "author_hash": self.store.anonymise(author),
                "parent_id": None,
                "published_at": tsn.get("publishedAt"),
                "like_count": _to_int(tsn.get("likeCount")) or 0,
            })
            n += 1
            for rep in (th.get("replies", {}) or {}).get("comments", []):
                rsn = rep["snippet"]
                rauthor = (rsn.get("authorChannelId", {}) or {}).get("value") \
                    or rsn.get("authorDisplayName", "unknown")
                self.store.insert_comment({
                    "comment_id": rep["id"], "video_id": video_id, "event": event,
                    "author_hash": self.store.anonymise(rauthor),
                    "parent_id": top["id"],
                    "published_at": rsn.get("publishedAt"),
                    "like_count": _to_int(rsn.get("likeCount")) or 0,
                })
                n += 1
        self.store.commit()
        return n

    # -- orchestration -----------------------------------------------------
    def run_event(self, event: str, seeds: dict, discovery_after: str,
                  discovery_before: str) -> CollectionResult:
        max_pages = int(self.s["api"]["max_comment_pages_per_video"])
        video_ids = self.discover_videos(seeds, discovery_after, discovery_before)
        channel_ids = self.fetch_video_meta(video_ids, event)
        self.fetch_channel_meta(channel_ids)
        total_comments = 0
        for i, vid in enumerate(video_ids, 1):
            try:
                c = self.fetch_comments(vid, event, max_pages)
            except QuotaExceeded:
                logger.warning("quota exhausted after %d/%d videos", i, len(video_ids))
                break
            total_comments += c
            if i % 10 == 0:
                logger.info("[%s] %d/%d videos, %d comments, quota %d/%d",
                            event, i, len(video_ids), total_comments,
                            self.client.meter.spent, self.client.meter.budget)
        counts = self.store.counts()
        return CollectionResult(event, len(video_ids), len(channel_ids),
                                total_comments, self.client.meter.report())
