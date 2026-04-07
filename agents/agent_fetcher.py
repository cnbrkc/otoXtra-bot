"""
News fetch and filter agent.
"""

import os
import re
import sys
from calendar import timegm
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

from core.logger import log
from core.config_loader import load_config
from core.helpers import (
    clean_html,
    get_turkey_now,
    get_posted_news,
    get_last_check_time,
    is_already_posted,
    is_duplicate_article,
    generate_topic_fingerprint,
    _fingerprint_similarity,
)
from core.state_manager import set_stage


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
_PRIORITY_ORDER = {"high": 3, "medium": 2, "low": 1}
_TREND_BONUSES = [(5, 15), (3, 10), (2, 5)]
_TREND_FINGERPRINT_THRESHOLD = 0.70


def _is_test_mode() -> bool:
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    return "--test" in sys.argv


def _turkish_lower(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "ı")
    return text.lower()


def _extract_image_from_entry(entry) -> str:
    media_content = entry.get("media_content", [])
    for media in media_content:
        media_url = media.get("url", "")
        media_type = media.get("type", "")
        if media_url and ("image" in media_type or media_type == ""):
            return media_url

    media_thumbnail = entry.get("media_thumbnail", [])
    for thumb in media_thumbnail:
        thumb_url = thumb.get("url", "")
        if thumb_url:
            return thumb_url

    enclosures = entry.get("enclosures", [])
    for enc in enclosures:
        enc_type = enc.get("type", "")
        enc_url = enc.get("href", "") or enc.get("url", "")
        if enc_url and "image" in enc_type:
            return enc_url

    for enc in enclosures:
        enc_url = enc.get("href", "") or enc.get("url", "")
        lower_url = enc_url.lower()
        if any(ext in lower_url for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            return enc_url

    html_content = (entry.get("summary", "") or "") + (entry.get("description", "") or "")
    for content_item in entry.get("content", []) or []:
        html_content += content_item.get("value", "") or ""
    html_content += entry.get("content_encoded", "") or ""

    if html_content and ("<img" in html_content or "<figure" in html_content):
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            for img_tag in soup.find_all("img"):
                img_src = (
                    img_tag.get("src", "")
                    or img_tag.get("data-src", "")
                    or img_tag.get("data-lazy-src", "")
                    or img_tag.get("data-original", "")
                    or img_tag.get("data-full-url", "")
                )
                if img_src.startswith("http"):
                    return img_src
        except Exception:
            pass

    image_field = entry.get("image", {})
    if isinstance(image_field, dict):
        img_href = image_field.get("href", "") or image_field.get("url", "")
        if img_href.startswith("http"):
            return img_href
    if isinstance(image_field, str) and image_field.startswith("http"):
        return image_field

    for link_item in entry.get("links", []) or []:
        link_type = link_item.get("type", "")
        link_href = link_item.get("href", "")
        if "image" in link_type and link_href:
            return link_href

    return ""


def _extract_published_date(entry, fallback_iso: str) -> str:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        try:
            ts = timegm(struct)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass

    date_str = entry.get("published", "") or entry.get("updated", "") or ""
    if date_str:
        try:
            dt = dateutil_parser.parse(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except (ValueError, OverflowError):
            pass
    return fallback_iso


def fetch_all_feeds() -> tuple:
    sources_cfg = load_config("sources")
    feeds = sources_cfg.get("feeds", [])
    if not feeds:
        log("No feeds found in sources.json", "ERROR")
        return [], {}

    all_articles = []
    source_health = {}
    now_iso = get_turkey_now().isoformat()

    for feed_info in feeds:
        feed_url = feed_info.get("url", "")
        feed_name = feed_info.get("name", "Unknown")
        feed_priority = feed_info.get("priority", "medium")
        feed_language = feed_info.get("language", "tr")
        feed_can_scrape = feed_info.get("can_scrape_image", False)
        feed_enabled = feed_info.get("enabled", True)

        if not feed_enabled:
            source_health[feed_name] = {"status": "disabled", "count": 0, "detail": "disabled"}
            continue
        if not feed_url:
            source_health[feed_name] = {"status": "error", "count": 0, "detail": "empty_url"}
            continue

        try:
            response = requests.get(
                feed_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=20,
                allow_redirects=True,
            )
            response.raise_for_status()

            parsed_feed = feedparser.parse(response.content)
            if parsed_feed.bozo and not parsed_feed.entries:
                bozo_msg = str(getattr(parsed_feed, "bozo_exception", "parse error"))[:120]
                source_health[feed_name] = {
                    "status": "error",
                    "count": 0,
                    "detail": f"parse_error: {bozo_msg}",
                }
                continue

            if not parsed_feed.entries:
                source_health[feed_name] = {
                    "status": "no_entries",
                    "count": 0,
                    "detail": "Feed has no entries",
                }
                continue

            entry_count = 0
            for entry in parsed_feed.entries:
                title = clean_html(entry.get("title", "")).strip()
                link = entry.get("link", "")
                if not title or not link:
                    continue

                summary_raw = entry.get("summary", "") or entry.get("description", "") or ""
                summary = clean_html(summary_raw).strip()
                if len(summary) < 10:
                    summary = ""

                article = {
                    "title": title,
                    "link": link,
                    "published": _extract_published_date(entry, now_iso),
                    "summary": summary,
                    "image_url": _extract_image_from_entry(entry),
                    "source_name": feed_name,
                    "source_priority": feed_priority,
                    "language": feed_language,
                    "can_scrape_image": feed_can_scrape,
                    "trend_count": 1,
                    "trend_bonus": 0,
                    "topic_fingerprint": generate_topic_fingerprint(title),
                }
                all_articles.append(article)
                entry_count += 1

            source_health[feed_name] = {"status": "ok", "count": entry_count, "detail": ""}

        except requests.exceptions.Timeout:
            source_health[feed_name] = {"status": "error", "count": 0, "detail": "timeout"}
        except requests.exceptions.ConnectionError:
            source_health[feed_name] = {"status": "error", "count": 0, "detail": "connection_error"}
        except requests.exceptions.HTTPError as exc:
            source_health[feed_name] = {
                "status": "error",
                "count": 0,
                "detail": f"http_error: {str(exc)[:80]}",
            }
        except Exception as exc:
            source_health[feed_name] = {
                "status": "error",
                "count": 0,
                "detail": f"unknown_error: {str(exc)[:120]}",
            }

    return all_articles, source_health


def apply_keyword_filter(articles: list) -> list:
    keywords_cfg = load_config("keywords")
    include_keywords = keywords_cfg.get("include_keywords") or keywords_cfg.get("must_match_any") or []
    exclude_keywords = keywords_cfg.get("exclude_keywords") or keywords_cfg.get("must_not_match") or []

    if not include_keywords and not exclude_keywords:
        return articles

    include_lower = [_turkish_lower(kw) for kw in include_keywords]
    exclude_lower = [_turkish_lower(kw) for kw in exclude_keywords]

    passed = []
    for article in articles:
        text = _turkish_lower(f"{article.get('title', '')} {article.get('summary', '')}")
        if any(kw in text for kw in exclude_lower):
            continue
        if include_lower and not any(kw in text for kw in include_lower):
            continue
        passed.append(article)
    return passed


def apply_time_filter(articles: list) -> list:
    settings = load_config("settings")
    max_age_hours = settings.get("news", {}).get("max_article_age_hours", 12)

    now_utc = datetime.now(timezone.utc)
    max_cutoff_utc = now_utc - timedelta(hours=max_age_hours)

    if _is_test_mode():
        cutoff_utc = max_cutoff_utc
    else:
        posted_data = get_posted_news()
        last_check = get_last_check_time(posted_data)
        smart_cutoff_utc = (last_check - timedelta(minutes=30)).astimezone(timezone.utc)
        cutoff_utc = max(smart_cutoff_utc, max_cutoff_utc)

    passed = []
    for article in articles:
        published_str = article.get("published", "")
        if not published_str:
            passed.append(article)
            continue
        try:
            pub_dt = dateutil_parser.parse(published_str)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt < cutoff_utc:
                continue
        except Exception:
            pass
        passed.append(article)
    return passed


def remove_already_posted(articles: list) -> list:
    posted_data = get_posted_news()
    if not posted_data.get("posts", []):
        return articles

    passed = []
    for article in articles:
        if not is_already_posted(article.get("link", ""), article.get("title", ""), posted_data):
            passed.append(article)
    return passed


def remove_duplicates(articles: list) -> list:
    if len(articles) <= 1:
        return articles

    used = [False] * len(articles)
    groups = []
    for i in range(len(articles)):
        if used[i]:
            continue
        group = [articles[i]]
        used[i] = True
        for j in range(i + 1, len(articles)):
            if used[j]:
                continue
            if is_duplicate_article(articles[i], articles[j]):
                group.append(articles[j])
                used[j] = True
        groups.append(group)

    unique = []
    for group in groups:
        best = max(group, key=lambda a: _PRIORITY_ORDER.get(a.get("source_priority", "low"), 0))
        unique.append(best)
    return unique


def _detect_trends(articles: list) -> list:
    if not articles:
        return articles
    n = len(articles)
    fps = []
    for article in articles:
        fp = article.get("topic_fingerprint") or generate_topic_fingerprint(article.get("title", ""))
        article["topic_fingerprint"] = fp
        fps.append(fp)

    trend_counts = [1] * n
    for i in range(n):
        for j in range(i + 1, n):
            if not fps[i] or not fps[j]:
                continue
            if _fingerprint_similarity(fps[i], fps[j]) >= _TREND_FINGERPRINT_THRESHOLD:
                trend_counts[i] += 1
                trend_counts[j] += 1

    for i, article in enumerate(articles):
        count = trend_counts[i]
        article["trend_count"] = count
        bonus = 0
        for min_count, b in _TREND_BONUSES:
            if count >= min_count:
                bonus = b
                break
        article["trend_bonus"] = bonus
    return articles


def scrape_full_article(url: str) -> str:
    if not url:
        return ""
    try:
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for unwanted in soup.find_all(["script", "style", "nav", "footer", "aside", "iframe"]):
            unwanted.decompose()
        paragraphs = soup.find_all("p")
        full_text = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) >= 30)
        full_text = re.sub(r"\s+", " ", full_text).strip()
        if len(full_text) > 5000:
            full_text = full_text[:5000].rsplit(" ", 1)[0] + "..."
        return full_text
    except Exception:
        return ""


def fetch_and_filter_news() -> tuple:
    articles, source_health = fetch_all_feeds()
    if not articles:
        return [], source_health

    articles = apply_keyword_filter(articles)
    if not articles:
        return [], source_health

    articles = apply_time_filter(articles)
    if not articles:
        return [], source_health

    if not _is_test_mode():
        articles = remove_already_posted(articles)
        if not articles:
            return [], source_health

    articles = remove_duplicates(articles)
    articles = _detect_trends(articles)
    return articles, source_health


def run() -> bool:
    set_stage("fetch", "running")
    try:
        articles, source_health = fetch_and_filter_news()
        if not articles:
            set_stage(
                "fetch",
                "error",
                output={"articles": [], "count": 0, "source_health": source_health},
                error="No article found",
            )
            return False

        set_stage(
            "fetch",
            "done",
            output={"articles": articles, "count": len(articles), "source_health": source_health},
        )
        return True
    except Exception as exc:
        set_stage("fetch", "error", error=str(exc))
        return False


if __name__ == "__main__":
    run()
