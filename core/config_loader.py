"""
core/config_loader.py
Merkezi config ve JSON okuma/yazma modulu.
"""

import json
import os
import tempfile
from typing import Any

from core.logger import log


def get_project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _empty_for_config(config_name: str) -> Any:
    if config_name == "sources":
        return {"feeds": []}
    return {}


def _as_int(value: Any, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        result = int(value)
    except Exception:
        result = default

    if min_value is not None and result < min_value:
        result = min_value
    if max_value is not None and result > max_value:
        result = max_value
    return result


def _as_float(value: Any, default: float, min_value: float | None = None, max_value: float | None = None) -> float:
    try:
        result = float(value)
    except Exception:
        result = default

    if min_value is not None and result < min_value:
        result = min_value
    if max_value is not None and result > max_value:
        result = max_value
    return result


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_str(value: Any, default: str) -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else default
    return default


def _normalize_sources(data: Any) -> dict:
    if isinstance(data, list):
        return {"feeds": data}

    if isinstance(data, dict):
        if isinstance(data.get("feeds"), list):
            return {"feeds": data.get("feeds", [])}
        if isinstance(data.get("sources"), list):
            return {"feeds": data.get("sources", [])}
        if isinstance(data.get("rss"), list):
            return {"feeds": data.get("rss", [])}
        if isinstance(data.get("rss_feeds"), list):
            return {"feeds": data.get("rss_feeds", [])}
        if isinstance(data.get("items"), list):
            return {"feeds": data.get("items", [])}

    return {"feeds": []}


def _sanitize_sources(data: Any) -> dict:
    normalized = _normalize_sources(data)
    feeds = normalized.get("feeds", [])
    if not isinstance(feeds, list):
        return {"feeds": []}

    safe_feeds = []
    for i, feed in enumerate(feeds, start=1):
        if not isinstance(feed, dict):
            continue

        url = _as_str(feed.get("url"), "")
        if not url:
            continue

        priority = _as_str(feed.get("priority"), "medium").lower()
        if priority not in ("high", "medium", "low"):
            priority = "medium"

        safe_feeds.append(
            {
                "name": _as_str(feed.get("name"), f"Source {i}"),
                "url": url,
                "priority": priority,
                "language": _as_str(feed.get("language"), "tr"),
                "enabled": _as_bool(feed.get("enabled"), True),
                "can_scrape_image": _as_bool(feed.get("can_scrape_image"), False),
            }
        )

    return {"feeds": safe_feeds}


def _sanitize_settings(data: Any) -> dict:
    if not isinstance(data, dict):
        return {}

    posting = data.get("posting", {})
    images = data.get("images", {})
    news = data.get("news", {})
    duplicate = data.get("duplicate_detection", {})
    ai = data.get("ai", {})

    if not isinstance(posting, dict):
        posting = {}
    if not isinstance(images, dict):
        images = {}
    if not isinstance(news, dict):
        news = {}
    if not isinstance(duplicate, dict):
        duplicate = {}
    if not isinstance(ai, dict):
        ai = {}

    safe = {
        "posting": {
            "max_daily_posts": _as_int(posting.get("max_daily_posts"), 9, 1, 50),
            "random_delay_max_minutes": _as_int(posting.get("random_delay_max_minutes"), 8, 0, 60),
            "min_post_interval_hours": _as_int(posting.get("min_post_interval_hours"), 1, 0, 24),
            "skip_probability_percent": _as_int(posting.get("skip_probability_percent"), 10, 0, 100),
            "max_posts_per_run": _as_int(posting.get("max_posts_per_run"), 1, 1, 10),
            "dry_run": _as_bool(posting.get("dry_run"), False),
        },
        "images": {
            "add_logo": _as_bool(images.get("add_logo"), True),
            "logo_position": _as_str(images.get("logo_position"), "bottom_right"),
            "logo_opacity": _as_float(images.get("logo_opacity"), 0.7, 0.0, 1.0),
            "logo_size_percent": _as_int(images.get("logo_size_percent"), 15, 1, 100),
            "feed_image_width": _as_int(images.get("feed_image_width"), 1200, 300, 4000),
            "feed_image_height": _as_int(images.get("feed_image_height"), 630, 300, 4000),

            # Yeni akista aktif kullandigin ayarlar
            "enable_article_image_scrape": _as_bool(images.get("enable_article_image_scrape"), True),
            "max_candidates_per_article": _as_int(images.get("max_candidates_per_article"), 8, 1, 50),
            "max_article_scrapes_per_feed": _as_int(images.get("max_article_scrapes_per_feed"), 6, 0, 50),
            "max_images_per_news": _as_int(images.get("max_images_per_news"), 1, 1, 10),
            "perceptual_hash_threshold": _as_int(images.get("perceptual_hash_threshold"), 6, 0, 64),
        },
        "news": {
            "max_article_age_hours": _as_int(news.get("max_article_age_hours"), 16, 1, 168),
            "max_articles_per_source": _as_int(news.get("max_articles_per_source"), 10, 1, 100),
            "min_summary_length": _as_int(news.get("min_summary_length"), 30, 0, 1000),
        },
        "duplicate_detection": {
            "title_similarity_threshold": _as_float(
                duplicate.get("title_similarity_threshold"), 0.80, 0.0, 1.0
            ),
            "keyword_overlap_threshold": _as_float(
                duplicate.get("keyword_overlap_threshold"), 0.70, 0.0, 1.0
            ),
        },
        "ai": {
            "temperature": _as_float(ai.get("temperature"), 0.7, 0.0, 2.0),
            "max_output_tokens": _as_int(ai.get("max_output_tokens"), 2048, 1, 8192),
            "enable_gemini": _as_bool(ai.get("enable_gemini"), True),
            "gemini_model": _as_str(ai.get("gemini_model"), "gemini-2.5-flash-lite"),
            "groq_model": _as_str(ai.get("groq_model"), "llama-3.3-70b-versatile"),
        },
    }

    return safe


def _sanitize_keywords(data: Any) -> dict:
    if not isinstance(data, dict):
        return {"include_keywords": [], "exclude_keywords": []}

    include_keywords = data.get("include_keywords", [])
    exclude_keywords = data.get("exclude_keywords", [])

    if not isinstance(include_keywords, list):
        include_keywords = []
    if not isinstance(exclude_keywords, list):
        exclude_keywords = []

    include_keywords = [str(x).strip() for x in include_keywords if str(x).strip()]
    exclude_keywords = [str(x).strip() for x in exclude_keywords if str(x).strip()]

    return {
        "include_keywords": include_keywords,
        "exclude_keywords": exclude_keywords,
    }


def _sanitize_scoring(data: Any) -> dict:
    if not isinstance(data, dict):
        data = {}

    thresholds = data.get("thresholds", {})
    if not isinstance(thresholds, dict):
        thresholds = {}

    publish_score = _as_int(thresholds.get("publish_score"), 65, 0, 100)
    slow_day_score = _as_int(thresholds.get("slow_day_score"), 50, 0, 100)

    if slow_day_score > publish_score:
        slow_day_score = publish_score

    return {
        "thresholds": {
            "publish_score": publish_score,
            "slow_day_score": slow_day_score,
        }
    }


def _sanitize_prompts(data: Any) -> dict:
    if not isinstance(data, dict):
        data = {}

    return {
        "viral_scorer": _as_str(data.get("viral_scorer"), ""),
        "post_writer": _as_str(data.get("post_writer"), ""),
    }


def load_json(filepath: str) -> Any:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log(f"JSON file not found: {filepath}", "WARNING")
        return {}
    except json.JSONDecodeError as exc:
        log(f"JSON parse error ({filepath}): {exc}", "ERROR")
        return {}
    except Exception as exc:
        log(f"JSON read error ({filepath}): {exc}", "ERROR")
        return {}


def save_json(filepath: str, data: Any) -> bool:
    directory = os.path.dirname(filepath) or "."
    tmp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            delete=False,
        ) as tmp_file:
            json.dump(data, tmp_file, indent=2, ensure_ascii=False)
            tmp_path = tmp_file.name

        os.replace(tmp_path, filepath)
        return True
    except Exception as exc:
        log(f"JSON write error ({filepath}): {exc}", "ERROR")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False


def load_config(config_name: str) -> Any:
    filepath = os.path.join(get_project_root(), "config", f"{config_name}.json")
    data = load_json(filepath)

    if data in ({}, None):
        log(f"Config could not be loaded: {config_name}.json", "WARNING")
        return _empty_for_config(config_name)

    if config_name == "sources":
        return _sanitize_sources(data)
    if config_name == "settings":
        return _sanitize_settings(data)
    if config_name == "keywords":
        return _sanitize_keywords(data)
    if config_name == "scoring":
        return _sanitize_scoring(data)
    if config_name == "prompts":
        return _sanitize_prompts(data)

    return data
