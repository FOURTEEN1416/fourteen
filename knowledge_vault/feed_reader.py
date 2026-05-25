"""
RSS/Atom订阅解析器 — 定期采集订阅源内容
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("feed_reader")

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False
    logger.warning("feedparser not installed, RSS reading disabled")


@dataclass
class FeedEntry:
    """RSS条目"""
    title: str
    link: str
    summary: str
    published: datetime | None
    source: str


class FeedReader:
    """
    RSS/Atom订阅解析器

    支持：
    - RSS 2.0
    - Atom 1.0
    - 自定义解析
    """

    def __init__(self):
        self._cache: dict[str, datetime] = {}  # URL -> 最后更新时间

    def read(self, feed_url: str, max_entries: int = 10) -> list[FeedEntry]:
        """
        读取RSS源

        Args:
            feed_url: RSS源URL
            max_entries: 最大条目数

        Returns:
            条目列表
        """
        if not HAS_FEEDPARSER:
            logger.warning("feedparser not available")
            return []

        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo:  # 解析错误
                logger.warning("Feed parse error: %s", feed.bozo_exception)

            entries = []
            for entry in feed.entries[:max_entries]:
                # 解析发布时间
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    with contextlib.suppress(Exception):
                        time_tuple = entry.published_parsed[:6]
                        published = datetime(
                            time_tuple[0], time_tuple[1], time_tuple[2],
                            time_tuple[3], time_tuple[4], time_tuple[5],
                            tzinfo=timezone.utc
                        )

                # 解析摘要
                summary = ""
                if hasattr(entry, "summary"):
                    summary = entry.summary
                elif hasattr(entry, "description"):
                    summary = entry.description

                entries.append(FeedEntry(
                    title=getattr(entry, "title", ""),
                    link=getattr(entry, "link", ""),
                    summary=self._clean_html(summary),
                    published=published,
                    source=feed.feed.get("title", feed_url),
                ))

            logger.info("Read %d entries from %s", len(entries), feed_url)
            return entries

        except Exception as e:  # noqa: BLE001

            logger.error("Failed to read feed %s: %s", feed_url, e)
            return []

    def read_multiple(self, feed_urls: list[str], max_per_feed: int = 5) -> list[FeedEntry]:
        """读取多个RSS源"""
        all_entries = []
        for url in feed_urls:
            entries = self.read(url, max_per_feed)
            all_entries.extend(entries)

        # 按时间排序
        all_entries.sort(
            key=lambda e: e.published or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return all_entries

    def _clean_html(self, text: str) -> str:
        """清理HTML标签"""
        import re
        # 移除HTML标签
        text = re.sub(r"<[^>]+>", "", text)
        # 解码HTML实体
        import html
        text = html.unescape(text)
        return text.strip()

    def to_dict(self, entry: FeedEntry) -> dict[str, Any]:
        """转换为字典"""
        return {
            "title": entry.title,
            "link": entry.link,
            "summary": entry.summary,
            "published": entry.published.isoformat() if entry.published else None,
            "source": entry.source,
        }
