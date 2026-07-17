from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from html import unescape
from urllib.parse import quote_plus

import httpx
from loguru import logger


_STOP_TERMS = {
    "http",
    "https",
    "com",
    "www",
    "微信",
    "聊天",
    "图片",
    "链接",
    "视频",
    "消息",
    "今天",
    "昨天",
    "最近",
    "一下",
    "这个",
    "那个",
    "可以",
    "没有",
    "不是",
    "什么",
    "怎么",
    "为什么",
    "the",
    "and",
    "for",
    "with",
}

_IMPORTANT_HINTS = (
    "美股",
    "港股",
    "A股",
    "股票",
    "股价",
    "财报",
    "投资",
    "市场",
    "币",
    "BTC",
    "ETH",
    "SOL",
    "ETF",
    "奈飞",
    "英伟达",
    "特斯拉",
    "苹果",
    "微软",
    "谷歌",
    "亚马逊",
    "Meta",
    "比亚迪",
    "小米汽车",
    "蔚来",
    "理想",
    "小鹏",
    "项目",
    "合作",
    "融资",
    "上市",
    "政策",
    "法律",
    "求职",
    "招聘",
)

_ALIASES = {
    "奈飞": "Netflix NFLX",
    "英伟达": "NVIDIA NVDA",
    "特斯拉": "Tesla TSLA",
    "苹果": "Apple AAPL",
    "微软": "Microsoft MSFT",
    "谷歌": "Google Alphabet GOOGL",
    "亚马逊": "Amazon AMZN",
    "比亚迪": "BYD 比亚迪",
    "小米汽车": "Xiaomi EV 小米汽车",
    "蔚来": "NIO 蔚来",
    "理想": "Li Auto 理想汽车",
    "小鹏": "XPeng 小鹏汽车",
    "ETH": "Ethereum ETH",
    "BTC": "Bitcoin BTC",
    "SOL": "Solana SOL",
}


@dataclass
class SearchResult:
    title: str
    source: str
    url: str
    published: str
    snippet: str


class ExternalContextBuilder:
    """Build lightweight current-web context for chat summaries."""

    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds

    async def build(
        self,
        messages: list[dict],
        *,
        max_topics: int = 8,
        results_per_topic: int = 3,
    ) -> str:
        topics = self.extract_topics(messages, max_topics=max_topics)
        if not topics:
            return ""

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 WeChatAI/1.0"},
        ) as client:
            tasks = [
                self._search_topic(client, topic, limit=results_per_topic)
                for topic in topics
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        sections = []
        for topic, result in zip(topics, results, strict=False):
            if isinstance(result, Exception):
                logger.debug(f"External context search failed for {topic}: {result}")
                continue
            if not result:
                continue
            lines = [f"### {topic}"]
            for item in result[:results_per_topic]:
                source = f"｜{item.source}" if item.source else ""
                published = f"｜{item.published}" if item.published else ""
                snippet = f"：{item.snippet}" if item.snippet else ""
                lines.append(f"- {item.title}{source}{published}{snippet} ({item.url})")
            sections.append("\n".join(lines))

        if not sections:
            return ""

        return (
            "## 外部背景快照（自动抓取，供校准用）\n"
            "说明：这些是根据聊天高频主题抓到的公开网页/新闻摘要，只能作为背景；"
            "日报必须区分“微信里实际说了什么”和“外部公开信息可能意味着什么”。\n\n"
            + "\n\n".join(sections)
        )

    def extract_topics(self, messages: list[dict], *, max_topics: int = 8) -> list[str]:
        text_parts: list[str] = []
        for msg in messages:
            text_parts.append(msg.get("content") or "")
            text_parts.append(msg.get("display_content") or "")
            for enriched in msg.get("source_enrichments") or []:
                if enriched.get("status") == "ok":
                    text_parts.append(enriched.get("extracted_text") or "")
        text = "\n".join(text_parts)
        if not text.strip():
            return []

        weighted: Counter[str] = Counter()
        for hint in _IMPORTANT_HINTS:
            count = text.count(hint)
            if count:
                weighted[_ALIASES.get(hint, hint)] += count + 2

        for token in re.findall(r"\b[A-Z]{2,6}\b", text):
            if token in _STOP_TERMS:
                continue
            weighted[_ALIASES.get(token, token)] += 3

        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", text):
            if token in _STOP_TERMS or token.lower() in _STOP_TERMS:
                continue
            if any(hint in token for hint in _IMPORTANT_HINTS) or token in _ALIASES:
                weighted[_ALIASES.get(token, token)] += 2

        topics = []
        for topic, _count in weighted.most_common(max_topics * 2):
            clean = " ".join(topic.split())
            if not clean or clean in topics:
                continue
            topics.append(clean)
            if len(topics) >= max_topics:
                break
        return topics

    async def _search_topic(self, client: httpx.AsyncClient, topic: str, *, limit: int) -> list[SearchResult]:
        queries = [
            f"{topic} 最新 新闻 市场",
            f"{topic} latest news",
        ]
        merged: list[SearchResult] = []
        seen: set[str] = set()
        for query in queries:
            for item in await self._bing_news_rss(client, query):
                marker = item.url or item.title
                if marker in seen:
                    continue
                seen.add(marker)
                merged.append(item)
                if len(merged) >= limit:
                    return merged
        return merged

    async def _bing_news_rss(self, client: httpx.AsyncClient, query: str) -> list[SearchResult]:
        url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss"
        response = await client.get(url)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        out: list[SearchResult] = []
        for item in root.findall(".//item"):
            title = self._xml_text(item, "title")
            link = self._xml_text(item, "link")
            desc = self._clean_html(self._xml_text(item, "description"))
            pub_date = self._xml_text(item, "pubDate")
            source = self._xml_text_by_local_name(item, "source") or self._source_from_title(title)
            if not title and not desc:
                continue
            out.append(
                SearchResult(
                    title=title[:180],
                    source=source[:80],
                    url=link,
                    published=pub_date[:32],
                    snippet=desc[:260],
                )
            )
        return out

    def _xml_text(self, item: ET.Element, tag: str) -> str:
        found = item.find(tag)
        return (found.text or "").strip() if found is not None else ""

    def _xml_text_by_local_name(self, item: ET.Element, local_name: str) -> str:
        for child in item:
            if child.tag.rsplit("}", 1)[-1].lower() == local_name.lower():
                return (child.text or "").strip()
        return ""

    def _clean_html(self, value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", value or "")
        text = unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    def _source_from_title(self, title: str) -> str:
        if " - " not in title:
            return ""
        return title.rsplit(" - ", 1)[-1].strip()


external_context_builder = ExternalContextBuilder()
