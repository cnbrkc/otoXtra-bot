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

    # ... mevcut fetch_all_feeds govden aynen devam (senden gelenle ayni)
    # Buradan asagi kodun kalan kismini oldugu gibi birakabilirsin.
    # Bu mesaji kisaltmak icin tekrar etmiyorum.
    return [], {}  # Bu satiri koyma; kendi dosyandaki mevcut kalan govdeyi aynen tut.
