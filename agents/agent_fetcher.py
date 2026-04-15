"""
News fetch and filter agent.
"""

import json
import os
import re
import sys
import time
from calendar import timegm
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

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

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
_DISALLOWED_IMAGE_EXTENSIONS = (".svg", ".ico")
_IMAGE_NOISE_HINTS = (
    "logo",
    "icon",
    "avatar",
    "sprite",
    "pixel",
    "ads",
    "banner",
    "favicon",
    "editor",
    "author",
    "profile",
    "yazar",
)
_IMAGE_HINT_PATHS = (
    "/wp-content/uploads/",
    "/uploads/",
    "/images/",
    "/image/",
    "/img/",
    "/media/",
)
_TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
}
_RESIZE_QUERY_KEYS = {"w", "h", "width", "height", "resize", "fit", "crop", "quality", "q"}


def _is_test_mode() -> bool:
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    return "--test" in sys.argv


def _turkish_lower(text: str) -> str:
    return text.replace("I", "i").lower()


def _request_with_retry(
    url: str,
    timeout: int = 20,
    attempts: int = 3,
    base_wait_seconds: float = 1.5,
) -> requests.Response:
    """
    Gecici ag hatalarinda exponential backoff ile tekrar dener.
    Son denemede hata varsa exception disari firlatilir.
    """
    last_exc: Exception | None = None
    headers = {"User-Agent": _USER_AGENT}

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
        ) as exc:
            last_exc = exc
            log(
                f"HTTP deneme hatasi ({attempt}/{attempts}) url={url} -> {exc}",
                "WARNING" if attempt < attempts else "ERROR",
            )
            if attempt < attempts:
                time.sleep(base_wait_seconds * (2 ** (attempt - 1)))
        except Exception as exc:
            last_exc = exc
            log(
                f"Beklenmeyen istek hatasi ({attempt}/{attempts}) url={url} -> {exc}",
                "WARNING" if attempt < attempts else "ERROR",
            )
            if attempt < attempts:
                time.sleep(base_wait_seconds * (2 ** (attempt - 1)))

    if last_exc:
        raise last_exc
    raise RuntimeError("HTTP request failed without exception detail")


def _extract_image_from_entry(entry: Any) -> str:
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
        except Exception as exc:
            log(f"Image parse warning: {exc}", "WARNING")

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


def _extract_published_date(entry: Any, fallback_iso: str) -> str:
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


def _normalize_image_url(raw_url: str, page_url: str = "") -> str:
    if not raw_url:
        return ""

    candidate = raw_url.strip()
    if not candidate:
        return ""

    if candidate.startswith("//"):
        candidate = f"https:{candidate}"

    if page_url:
        candidate = urljoin(page_url, candidate)

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    query_items = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k not in _TRACKING_QUERY_KEYS
    ]
    cleaned = parsed._replace(query=urlencode(query_items), fragment="")
    return urlunparse(cleaned)


def _normalize_path_for_candidate_key(path: str) -> str:
    if not path:
        return path

    dir_part, _, filename = path.rpartition("/")
    name, dot, ext = filename.partition(".")
    lower_name = name.lower()

    # src_340x1912xslug..., 1400x788slug... gibi prefix boyutlari temizle
    lower_name = re.sub(r"^src_\d{2,4}x\d{2,4}x", "", lower_name, flags=re.IGNORECASE)
    lower_name = re.sub(r"^\d{2,4}x\d{2,4}", "", lower_name, flags=re.IGNORECASE)

    normalized_filename = f"{lower_name}{dot}{ext}" if dot else lower_name
    return f"{dir_part}/{normalized_filename}" if dir_part else normalized_filename


def _candidate_key(url: str) -> str:
    parsed = urlparse(url)
    path = _normalize_path_for_candidate_key(parsed.path or "")
    path = re.sub(
        r"-(\d{2,4})x(\d{2,4})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$",
        r"\3",
        path,
        flags=re.IGNORECASE,
    )
    filtered_qs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _RESIZE_QUERY_KEYS
    ]
    return urlunparse(parsed._replace(path=path, query=urlencode(filtered_qs), fragment="")).lower()


def _looks_like_noise_image(url: str) -> bool:
    lower_url = url.lower()
    return any(hint in lower_url for hint in _IMAGE_NOISE_HINTS)


def _is_probable_image_url(url: str) -> bool:
    lower = url.lower()
    parsed = urlparse(lower)

    if parsed.path.endswith(_DISALLOWED_IMAGE_EXTENSIONS):
        return False

    # Haberle ilgisiz editor/profil gorsellerini ele
    if "/images/editor/" in lower or "/images/images/editor/" in lower:
        return False
    if any(x in lower for x in ("/author/", "/profile/", "/avatar/")):
        return False

    if any(ext in lower for ext in _IMAGE_EXTENSIONS):
        return True
    if "image" in lower:
        return True
    if any(p in lower for p in _IMAGE_HINT_PATHS):
        return True
    return False


def _thumbnail_to_original_variants(url: str) -> list[str]:
    """
    Kucuk thumbnail URL'lerinden daha buyuk/orijinal varyantlar ureterek
    daha kaliteli gorsel yakalama sansini arttirir.
    """
    variants: list[str] = [url]
    parsed = urlparse(url)
    path = parsed.path or ""
    query_items = parse_qsl(parsed.query, keep_blank_values=True)

    wp_thumb_pattern = re.compile(
        r"-(\d{2,4})x(\d{2,4})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$",
        re.IGNORECASE,
    )
    if wp_thumb_pattern.search(path):
        original_path = wp_thumb_pattern.sub(r"\3", path)
        variants.append(urlunparse(parsed._replace(path=original_path)))

    if query_items:
        filtered_query = [(k, v) for k, v in query_items if k.lower() not in _RESIZE_QUERY_KEYS]
        if len(filtered_query) != len(query_items):
            variants.append(urlunparse(parsed._replace(query=urlencode(filtered_query))))

    filename_cleaned_path = re.sub(
        r"(?i)([-_](small|thumb|thumbnail|medium|preview))(?=\.)",
        "",
        path,
    )
    if filename_cleaned_path != path:
        variants.append(urlunparse(parsed._replace(path=filename_cleaned_path)))

    unique: list[str] = []
    seen: set[str] = set()
    for item in variants:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _extract_best_src_from_srcset(srcset: str, page_url: str) -> str:
    best_url = ""
    best_score = -1.0

    for item in srcset.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split()
        url_part = _normalize_image_url(parts[0], page_url)
        if not url_part:
            continue

        score = 1.0
        if len(parts) > 1:
            descriptor = parts[1].lower()
            if descriptor.endswith("w"):
                try:
                    score = float(descriptor[:-1])
                except ValueError:
                    score = 1.0
            elif descriptor.endswith("x"):
                try:
                    score = float(descriptor[:-1]) * 1000
                except ValueError:
                    score = 1.0

        if score > best_score:
            best_score = score
            best_url = url_part

    return best_url


def _collect_jsonld_images(node: Any, page_url: str, collector: list[str]) -> None:
    if isinstance(node, dict):
        image_value = node.get("image")
        if isinstance(image_value, str):
            normalized = _normalize_image_url(image_value, page_url)
            if normalized:
                collector.append(normalized)
        elif isinstance(image_value, list):
            for item in image_value:
                if isinstance(item, str):
                    normalized = _normalize_image_url(item, page_url)
                    if normalized:
                        collector.append(normalized)
                elif isinstance(item, dict):
                    candidate = item.get("url") or item.get("contentUrl")
                    normalized = _normalize_image_url(candidate or "", page_url)
                    if normalized:
                        collector.append(normalized)
        elif isinstance(image_value, dict):
            candidate = image_value.get("url") or image_value.get("contentUrl")
            normalized = _normalize_image_url(candidate or "", page_url)
            if normalized:
                collector.append(normalized)

        for value in node.values():
            _collect_jsonld_images(value, page_url, collector)

    elif isinstance(node, list):
        for item in node:
            _collect_jsonld_images(item, page_url, collector)


def extract_images_from_article(url: str, max_candidates: int = 8) -> list[str]:
    if not url:
        return []

    try:
        response = _request_with_retry(url, timeout=15, attempts=2, base_wait_seconds=1.0)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        raw_candidates: list[str] = []

        meta_selectors = [
            ('meta[property="og:image"]', "content"),
            ('meta[property="og:image:url"]', "content"),
            ('meta[name="twitter:image"]', "content"),
            ('meta[name="twitter:image:src"]', "content"),
        ]
        for selector, attr in meta_selectors:
            for tag in soup.select(selector):
                normalized = _normalize_image_url(tag.get(attr, ""), url)
                if normalized:
                    raw_candidates.extend(_thumbnail_to_original_variants(normalized))

        for script in soup.select('script[type="application/ld+json"]'):
            text = (script.string or script.get_text() or "").strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
                tmp: list[str] = []
                _collect_jsonld_images(parsed, url, tmp)
                for item in tmp:
                    raw_candidates.extend(_thumbnail_to_original_variants(item))
            except Exception:
                continue

        for img in soup.find_all("img"):
            width_attr = img.get("width")
            height_attr = img.get("height")
            try:
                if width_attr and height_attr and int(width_attr) < 200 and int(height_attr) < 200:
                    continue
            except Exception:
                pass

            src_candidates = [
                img.get("src", ""),
                img.get("data-src", ""),
                img.get("data-lazy-src", ""),
                img.get("data-original", ""),
                img.get("data-full-url", ""),
            ]

            srcset = img.get("srcset", "") or img.get("data-srcset", "")
            if srcset:
                best_srcset = _extract_best_src_from_srcset(srcset, url)
                if best_srcset:
                    src_candidates.append(best_srcset)

            for src in src_candidates:
                normalized = _normalize_image_url(src, url)
                if normalized:
                    raw_candidates.extend(_thumbnail_to_original_variants(normalized))

        raw_count = len(raw_candidates)
        unique_candidates: list[str] = []
        seen_keys: set[str] = set()
        for candidate in raw_candidates:
            lower_candidate = candidate.lower()
            key = _candidate_key(candidate)

            if not candidate or key in seen_keys:
                continue
            if _looks_like_noise_image(lower_candidate):
                continue
            if not _is_probable_image_url(lower_candidate):
                continue

            seen_keys.add(key)
            unique_candidates.append(candidate)
            if len(unique_candidates) >= max_candidates:
                break

        log(f"extract_images_from_article: raw={raw_count}, canonical={len(unique_candidates)}, url={url[:120]}")
        return unique_candidates

    except Exception as exc:
        log(f"extract_images_from_article warning: {exc}", "WARNING")
        return []


def fetch_all_feeds() -> tuple[list[dict], dict]:
    sources_cfg = load_config("sources")
    feeds = sources_cfg.get("feeds", []) if isinstance(sources_cfg, dict) else []
    if not feeds:
        log("No feeds found in sources.json", "ERROR")
        return [], {}

    all_articles: list[dict] = []
    source_health: dict = {}
    now_iso = get_turkey_now().isoformat()

    settings_cfg = load_config("settings")
    images_cfg = settings_cfg.get("images", {}) if isinstance(settings_cfg, dict) else {}

    enable_article_image_scrape = bool(images_cfg.get("enable_article_image_scrape", True))
    max_candidates_per_article = int(images_cfg.get("max_candidates_per_article", 8))
    max_article_scrapes_per_feed = int(images_cfg.get("max_article_scrapes_per_feed", 6))

    log(
        f"fetch image config: enable_article_image_scrape={enable_article_image_scrape}, "
        f"max_candidates_per_article={max_candidates_per_article}, "
        f"max_article_scrapes_per_feed={max_article_scrapes_per_feed}"
    )

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
            response = _request_with_retry(feed_url, timeout=20, attempts=3, base_wait_seconds=1.5)

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
            scraped_in_feed = 0

            for entry in parsed_feed.entries:
                title = clean_html(entry.get("title", "")).strip()
                link = entry.get("link", "")
                if not title or not link:
                    continue

                summary_raw = entry.get("summary", "") or entry.get("description", "") or ""
                summary = clean_html(summary_raw).strip()
                if len(summary) < 10:
                    summary = ""

                rss_image_url = _extract_image_from_entry(entry)
                normalized_rss_image = _normalize_image_url(rss_image_url, link) if rss_image_url else ""

                article_image_candidates: list[str] = []

                if (
                    feed_can_scrape
                    and enable_article_image_scrape
                    and scraped_in_feed < max_article_scrapes_per_feed
                ):
                    article_image_candidates = extract_images_from_article(
                        link,
                        max_candidates=max_candidates_per_article,
                    )
                    scraped_in_feed += 1

                if normalized_rss_image:
                    rss_variants = _thumbnail_to_original_variants(normalized_rss_image)
                    for rss_item in reversed(rss_variants):
                        if rss_item in article_image_candidates:
                            article_image_candidates.remove(rss_item)
                    article_image_candidates = rss_variants + article_image_candidates

                raw_candidate_count = len(article_image_candidates)
                deduped_candidates: list[str] = []
                seen_candidate_keys: set[str] = set()
                for c in article_image_candidates:
                    if not c:
                        continue
                    c_key = _candidate_key(c)
                    if c_key in seen_candidate_keys:
                        continue
                    seen_candidate_keys.add(c_key)
                    deduped_candidates.append(c)
                    if len(deduped_candidates) >= max_candidates_per_article:
                        break
                article_image_candidates = deduped_candidates

                if raw_candidate_count != len(article_image_candidates):
                    log(f"{feed_name}: image candidates raw={raw_candidate_count}, canonical={len(article_image_candidates)}")

                primary_image = (
                    article_image_candidates[0]
                    if article_image_candidates
                    else (normalized_rss_image or rss_image_url)
                )

                article = {
                    "title": title,
                    "link": link,
                    "published": _extract_published_date(entry, now_iso),
                    "summary": summary,
                    "image_url": primary_image or "",
                    "rss_image_url": normalized_rss_image or rss_image_url or "",
                    "image_candidates": article_image_candidates,
                    "image_source": (
                        "article"
                        if article_image_candidates
                        and (not normalized_rss_image or article_image_candidates[0] != normalized_rss_image)
                        else ("rss" if primary_image else "none")
                    ),
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


def apply_keyword_filter(articles: list[dict]) -> list[dict]:
    keywords_cfg = load_config("keywords")
    if not isinstance(keywords_cfg, dict):
        return articles

    include_keywords = keywords_cfg.get("include_keywords") or keywords_cfg.get("must_match_any") or []
    exclude_keywords = keywords_cfg.get("exclude_keywords") or keywords_cfg.get("must_not_match") or []

    if not include_keywords and not exclude_keywords:
        return articles

    include_lower = [_turkish_lower(str(kw)) for kw in include_keywords if str(kw).strip()]
    exclude_lower = [_turkish_lower(str(kw)) for kw in exclude_keywords if str(kw).strip()]

    passed = []
    for article in articles:
        text = _turkish_lower(f"{article.get('title', '')} {article.get('summary', '')}")
        if any(kw in text for kw in exclude_lower):
            continue
        if include_lower and not any(kw in text for kw in include_lower):
            continue
        passed.append(article)
    return passed


def apply_time_filter(articles: list[dict]) -> list[dict]:
    settings = load_config("settings")
    news_cfg = settings.get("news", {}) if isinstance(settings, dict) else {}
    max_age_hours = int(news_cfg.get("max_article_age_hours", 12))

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


def remove_already_posted(articles: list[dict]) -> list[dict]:
    posted_data = get_posted_news()
    if not posted_data.get("posts", []):
        return articles

    passed = []
    for article in articles:
        if not is_already_posted(article.get("link", ""), article.get("title", ""), posted_data):
            passed.append(article)
    return passed


def remove_duplicates(articles: list[dict]) -> list[dict]:
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


def _detect_trends(articles: list[dict]) -> list[dict]:
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
        resp = _request_with_retry(url, timeout=15, attempts=2, base_wait_seconds=1.0)
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
    except Exception as exc:
        log(f"scrape_full_article warning: {exc}", "WARNING")
        return ""


def fetch_and_filter_news() -> tuple[list[dict], dict]:
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
