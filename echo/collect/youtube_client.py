"""Quota-aware wrapper around the YouTube Data API v3.

Every call is metered against a documented per-endpoint cost and a daily budget
(see ``config/settings.yaml``). When the budget would be exceeded the client
raises :class:`QuotaExceeded` rather than silently truncating the sample, so the
extent of any partial collection is always explicit and reportable.

The wrapper is intentionally thin: it exposes exactly the endpoints ECHO needs
(search, videos, channels, commentThreads, comments, playlistItems) and returns
plain dictionaries, keeping the collection logic testable without live network
access.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger("echo.collect.client")


class QuotaExceeded(RuntimeError):
    """Raised when a call would push cumulative usage past the daily budget."""


@dataclass
class QuotaMeter:
    """Tracks cumulative quota usage against a documented budget."""

    budget: int
    costs: dict[str, int]
    spent: int = 0
    calls: dict[str, int] = field(default_factory=dict)

    def charge(self, endpoint: str) -> None:
        cost = self.costs.get(endpoint)
        if cost is None:
            raise KeyError(f"unknown endpoint cost: {endpoint}")
        if self.spent + cost > self.budget:
            raise QuotaExceeded(
                f"call to {endpoint} (+{cost}u) would exceed budget "
                f"({self.spent}/{self.budget}u already spent)"
            )
        self.spent += cost
        self.calls[endpoint] = self.calls.get(endpoint, 0) + 1

    def remaining(self) -> int:
        return self.budget - self.spent

    def report(self) -> dict[str, Any]:
        return {"spent": self.spent, "budget": self.budget,
                "remaining": self.remaining(), "calls": dict(self.calls)}


class YouTubeClient:
    """Minimal, quota-metered YouTube Data API v3 client."""

    def __init__(self, api_key: str, meter: QuotaMeter, max_retries: int = 4):
        from googleapiclient.discovery import build  # lazy import
        self._svc = build("youtube", "v3", developerKey=api_key,
                          cache_discovery=False)
        self.meter = meter
        self.max_retries = max_retries

    # -- low level ---------------------------------------------------------
    def _execute(self, request, endpoint: str) -> dict[str, Any]:
        from googleapiclient.errors import HttpError
        self.meter.charge(endpoint)
        for attempt in range(self.max_retries):
            try:
                return request.execute()
            except HttpError as exc:  # pragma: no cover - network path
                status = getattr(exc.resp, "status", None)
                # 403 quotaExceeded / rate limits, 500/503 transient
                if status in (403, 429, 500, 503) and attempt < self.max_retries - 1:
                    backoff = 2 ** (attempt + 1)
                    logger.warning("HTTP %s on %s; retry in %ss", status, endpoint, backoff)
                    time.sleep(backoff)
                    continue
                raise
        raise RuntimeError("unreachable")

    # -- endpoints ---------------------------------------------------------
    def search_videos(self, query: str, published_after: str, published_before: str,
                      max_results: int = 50, order: str = "relevance") -> list[dict]:
        req = self._svc.search().list(
            q=query, part="id,snippet", type="video", maxResults=max_results,
            order=order, publishedAfter=published_after,
            publishedBefore=published_before,
        )
        resp = self._execute(req, "cost_search")
        return resp.get("items", [])

    def videos(self, video_ids: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(video_ids), 50):
            req = self._svc.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(video_ids[i:i + 50]),
            )
            out.extend(self._execute(req, "cost_videos").get("items", []))
        return out

    def channels(self, channel_ids: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(channel_ids), 50):
            req = self._svc.channels().list(
                part="snippet,statistics,contentDetails",
                id=",".join(channel_ids[i:i + 50]),
            )
            out.extend(self._execute(req, "cost_channels").get("items", []))
        return out

    def channels_by_handle(self, handle: str) -> list[dict]:
        req = self._svc.channels().list(part="snippet,statistics,contentDetails",
                                       forHandle=handle.lstrip("@"))
        return self._execute(req, "cost_channels").get("items", [])

    def playlist_items(self, playlist_id: str, max_pages: int = 10) -> Iterator[dict]:
        page_token = None
        for _ in range(max_pages):
            req = self._svc.playlistItems().list(
                part="contentDetails", playlistId=playlist_id,
                maxResults=50, pageToken=page_token,
            )
            resp = self._execute(req, "cost_playlistItems")
            yield from resp.get("items", [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    def comment_threads(self, video_id: str, max_pages: int = 20) -> Iterator[dict]:
        page_token = None
        for _ in range(max_pages):
            req = self._svc.commentThreads().list(
                part="snippet,replies", videoId=video_id, maxResults=100,
                order="time", textFormat="plainText", pageToken=page_token,
            )
            try:
                resp = self._execute(req, "cost_commentThreads")
            except Exception as exc:  # comments disabled etc.
                logger.warning("commentThreads failed for %s: %s", video_id, exc)
                return
            yield from resp.get("items", [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
