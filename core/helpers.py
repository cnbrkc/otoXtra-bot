"""
General helper utilities for otoXtra bot.
"""

import os
import re
import time
import random
import difflib
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from core.logger import log
from core.config_loader import get_project_root, load_json, save_json, load_config


def get_turkey_now() -> datetime:
    turkey_tz = timezone(timedelta(hours=3))
    return datetime.now(turkey_tz)


def get_today_str() -> str:
    return get_turkey_now().strftime("%Y-%m-%d")


def clean_html(html_text: str) -> str:
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    return soup.get_text(separator=" ", strip=True)


def is_similar_title(title1: str, title2: str, threshold: float = None) -> bool:
    if not title1 or not title2:
        return False

    if threshold is None:
        try:
            settings = load_config("settings")
            threshold = settings.get("duplicate_detection", {}).get(
                "title_similarity_threshold", 0.80
            )
        except Exception:
            threshold = 0.80

    clean1 = title1.lower().strip()
    clean2 = title2.lower().strip()
    ratio = difflib.SequenceMatcher(None, clean1, clean2).ratio()
    return ratio >= threshold


_TR_MAP = str.maketrans({
    "\u00e7": "c",
    "\u011f": "g",
    "\u0131": "i",
    "\u00f6": "o",
    "\u015f": "s",
    "\u00fc": "u",
    "\u00c7": "c",
    "\u011e": "g",
    "\u0130": "i",
    "\u00d6": "o",
    "\u015e": "s",
    "\u00dc": "u",
})


def _normalize_token(text: str) -> str:
    if not text:
        return ""
    value = text.lower().translate(_TR_MAP)
    value = re.sub(r"[^a-z0-9]", "", value)
    return value


_RAW_STOP_WORDS = {
    "bir", "bu", "su", "ve", "ile", "icin", "ama", "fakat", "ancak", "veya", "ya",
    "de", "da", "ki", "mi", "mu", "den", "dan", "ten", "tan", "nin", "nun", "in",
    "un", "deki", "daki", "teki", "taki", "gibi", "kadar", "daha", "cok", "olan",
    "oldu", "olarak", "uzere", "sonra", "once", "gore", "karsi", "arasinda", "icinde",
    "yeni", "buyuk", "kucuk", "ilk", "son", "en", "artik", "sadece", "bile", "her",
    "hic", "tum", "cikti", "geldi", "edildi", "yapildi", "aciklandi", "duyuruldu",
    "tanitildi", "basladi",
}

_NORMALIZED_STOP_WORDS = {_normalize_token(w) for w in _RAW_STOP_WORDS if w}


def generate_topic_fingerprint(title: str) -> str:
    if not title:
        return ""

    normalized = title.lower().translate(_TR_MAP)
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    words = normalized.split()

    keywords = sorted(
        [
            w for w in words
            if len(w) >= 3 and _normalize_token(w) not in _NORMALIZED_STOP_WORDS
        ]
    )
    return "-".join(keywords)


def _fingerprint_similarity(fp1: str, fp2: str) -> float:
    if not fp1 or not fp2:
        return 0.0
    return difflib.SequenceMatcher(None, fp1, fp2).ratio()


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _extract_keywords_from_title(title: str, min_len: int = 4) -> set:
    title_lower = title.replace("I", "i").lower()
    words = re.findall(r"[a-z0-9]+", title_lower)
    return {w for w in words if len(w) >= min_len and _normalize_token(w) not in _NORMALIZED_STOP_WORDS}


def is_duplicate_article(article1: dict, article2: dict) -> bool:
    try:
        settings = load_config("settings")
        dup_settings = settings.get("duplicate_detection", {})
        title_threshold = dup_settings.get("title_similarity_threshold", 0.80)
        keyword_threshold = dup_settings.get("keyword_overlap_threshold", 0.70)
    except Exception:
        title_threshold = 0.80
        keyword_threshold = 0.70

    url1 = article1.get("url") or article1.get("link", "")
    url2 = article2.get("url") or article2.get("link", "")
    title1 = article1.get("title", "")
    title2 = article2.get("title", "")

    if url1 and url2 and url1 == url2:
        return True

    if is_similar_title(title1, title2, threshold=title_threshold):
        return True

    if title1 and title2:
        keywords1 = _extract_keywords_from_title(title1)
        keywords2 = _extract_keywords_from_title(title2)
        if keywords1 and keywords2:
            intersection = keywords1 & keywords2
            union = keywords1 | keywords2
            if union:
                overlap_ratio = len(intersection) / len(union)
                if overlap_ratio >= keyword_threshold:
                    return True

    return False


def _get_week_key(dt: datetime = None) -> str:
    current = dt or get_turkey_now()
    iso = current.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def get_previous_week_key(reference_dt: datetime = None) -> str:
    current = reference_dt or get_turkey_now()
    prev_week_dt = current - timedelta(days=7)
    return _get_week_key(prev_week_dt)


def _ensure_stats_schema(data: dict) -> None:
    if "stats" not in data or not isinstance(data.get("stats"), dict):
        data["stats"] = {}

    stats = data["stats"]

    if "daily_actions" not in stats or not isinstance(stats.get("daily_actions"), dict):
        stats["daily_actions"] = {}

    if "weekly" not in stats or not isinstance(stats.get("weekly"), dict):
        stats["weekly"] = {}


def _ensure_weekly_bucket(data: dict, week_key: str) -> dict:
    _ensure_stats_schema(data)
    weekly = data["stats"]["weekly"]

    bucket = weekly.get(week_key)
    if not isinstance(bucket, dict):
        bucket = {
            "actions": 0,
            "shares": 0,
            "error_total": 0,
            "errors": {},
            "skip_total": 0,
            "skips": {},
            "report_sent": False,
            "report_sent_at": None,
        }
        weekly[week_key] = bucket
        return bucket

    if "actions" not in bucket:
        bucket["actions"] = 0
    if "shares" not in bucket:
        bucket["shares"] = 0
    if "error_total" not in bucket:
        bucket["error_total"] = 0
    if "errors" not in bucket or not isinstance(bucket.get("errors"), dict):
        bucket["errors"] = {}
    if "skip_total" not in bucket:
        bucket["skip_total"] = 0
    if "skips" not in bucket or not isinstance(bucket.get("skips"), dict):
        bucket["skips"] = {}
    if "report_sent" not in bucket:
        bucket["report_sent"] = False
    if "report_sent_at" not in bucket:
        bucket["report_sent_at"] = None

    return bucket


def increment_action_trigger(posted_data: dict) -> int:
    _ensure_stats_schema(posted_data)

    today = get_today_str()
    daily_actions = posted_data["stats"]["daily_actions"]
    daily_actions[today] = int(daily_actions.get(today, 0)) + 1

    week_key = _get_week_key()
    week_bucket = _ensure_weekly_bucket(posted_data, week_key)
    week_bucket["actions"] = int(week_bucket.get("actions", 0)) + 1

    return daily_actions[today]


def get_today_action_count(posted_data: dict) -> int:
    _ensure_stats_schema(posted_data)
    today = get_today_str()
    return int(posted_data["stats"]["daily_actions"].get(today, 0))


def increment_weekly_share(posted_data: dict) -> None:
    week_key = _get_week_key()
    week_bucket = _ensure_weekly_bucket(posted_data, week_key)
    week_bucket["shares"] = int(week_bucket.get("shares", 0)) + 1


def record_weekly_error(posted_data: dict, error_code: str, error_name: str = "") -> None:
    week_key = _get_week_key()
    week_bucket = _ensure_weekly_bucket(posted_data, week_key)

    clean_code = (error_code or "UNKNOWN").strip().upper()
    clean_name = (error_name or "").strip()
    if clean_name:
        key = f"{clean_code}: {clean_name[:140]}"
    else:
        key = clean_code

    week_bucket["error_total"] = int(week_bucket.get("error_total", 0)) + 1
    errors = week_bucket.get("errors", {})
    errors[key] = int(errors.get(key, 0)) + 1
    week_bucket["errors"] = errors


def record_weekly_skip(posted_data: dict, skip_reason: str = "") -> None:
    week_key = _get_week_key()
    week_bucket = _ensure_weekly_bucket(posted_data, week_key)

    reason = (skip_reason or "UNKNOWN_SKIP").strip()
    if not reason:
        reason = "UNKNOWN_SKIP"

    week_bucket["skip_total"] = int(week_bucket.get("skip_total", 0)) + 1
    skips = week_bucket.get("skips", {})
    skips[reason[:160]] = int(skips.get(reason[:160], 0)) + 1
    week_bucket["skips"] = skips


def get_weekly_stats(posted_data: dict, week_key: str) -> dict:
    bucket = _ensure_weekly_bucket(posted_data, week_key)
    return {
        "actions": int(bucket.get("actions", 0)),
        "shares": int(bucket.get("shares", 0)),
        "error_total": int(bucket.get("error_total", 0)),
        "errors": dict(bucket.get("errors", {})),
        "skip_total": int(bucket.get("skip_total", 0)),
        "skips": dict(bucket.get("skips", {})),
        "report_sent": bool(bucket.get("report_sent", False)),
        "report_sent_at": bucket.get("report_sent_at"),
    }


def is_weekly_report_sent(posted_data: dict, week_key: str) -> bool:
    bucket = _ensure_weekly_bucket(posted_data, week_key)
    return bool(bucket.get("report_sent", False))


def mark_weekly_report_sent(posted_data: dict, week_key: str) -> None:
    bucket = _ensure_weekly_bucket(posted_data, week_key)
    bucket["report_sent"] = True
    bucket["report_sent_at"] = get_turkey_now().isoformat()


def _parse_expiry_datetime(raw_value: str):
    if not raw_value:
        return None

    raw = raw_value.strip()
    if not raw:
        return None

    try:
        # Unix timestamp (seconds or milliseconds)
        if raw.isdigit():
            ts = int(raw)
            if ts > 10_000_000_000:
                ts = ts / 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(
                timezone(timedelta(hours=3))
            )
    except Exception:
        pass

    # ISO format
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=3)))
        return parsed
    except Exception:
        pass

    # YYYY-MM-DD
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        return parsed.replace(tzinfo=timezone(timedelta(hours=3)))
    except Exception:
        pass

    # dateutil fallback
    try:
        from dateutil import parser as dateutil_parser

        parsed = dateutil_parser.parse(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=3)))
        return parsed
    except Exception:
        return None


def get_token_remaining_days():
    env_keys = [
        "FACEBOOK_TOKEN_EXPIRES_AT",
        "FB_TOKEN_EXPIRES_AT",
        "TOKEN_EXPIRES_AT",
    ]
    raw_value = ""
    for key in env_keys:
        candidate = (os.environ.get(key, "") or "").strip()
        if candidate:
            raw_value = candidate
            break

    if not raw_value:
        return None

    expires_at = _parse_expiry_datetime(raw_value)
    if not expires_at:
        return None

    delta_seconds = (expires_at - get_turkey_now()).total_seconds()
    return int(delta_seconds // 86400)


def get_posted_news() -> dict:
    filepath = os.path.join(get_project_root(), "data", "posted_news.json")
    data = load_json(filepath)

    if not data or not isinstance(data, dict):
        data = {"posts": [], "daily_counts": {}, "last_check_time": None, "stats": {}}

    if "posts" not in data or not isinstance(data.get("posts"), list):
        data["posts"] = []
    if "daily_counts" not in data or not isinstance(data.get("daily_counts"), dict):
        data["daily_counts"] = {}
    if "last_check_time" not in data:
        data["last_check_time"] = None

    _ensure_stats_schema(data)
    return data


_HISTORY_DAYS = 30


def _parse_dt_safe(value: str):
    if not value:
        return None
    try:
        from dateutil import parser as dateutil_parser

        dt = dateutil_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=3)))
        return dt
    except Exception:
        return None


def save_posted_news(data: dict) -> bool:
    posts = data.get("posts", [])
    daily_counts = data.get("daily_counts", {})

    now = get_turkey_now()
    cutoff = now - timedelta(days=_HISTORY_DAYS)

    cleaned_posts = []
    old_count = 0
    for post in posts:
        posted_at = post.get("posted_at", "")
        post_dt = _parse_dt_safe(posted_at)
        if post_dt is None:
            cleaned_posts.append(post)
            continue
        if post_dt < cutoff:
            old_count += 1
        else:
            cleaned_posts.append(post)

    # Keep newest records only when still too large.
    def sort_key(item):
        dt = _parse_dt_safe(item.get("posted_at", ""))
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    cleaned_posts.sort(key=sort_key)
    if len(cleaned_posts) > 500:
        removed_extra = len(cleaned_posts) - 300
        cleaned_posts = cleaned_posts[-300:]
        log(f"Safety cleanup removed {removed_extra} extra records", "WARNING")

    cutoff_date_str = cutoff.strftime("%Y-%m-%d")
    cleaned_daily = {
        date: count for date, count in daily_counts.items() if date >= cutoff_date_str
    }

    data["posts"] = cleaned_posts
    data["daily_counts"] = cleaned_daily

    # stats cleanup
    _ensure_stats_schema(data)
    stats = data["stats"]

    daily_actions = stats.get("daily_actions", {})
    stats["daily_actions"] = {
        date: count for date, count in daily_actions.items() if date >= cutoff_date_str
    }

    weekly = stats.get("weekly", {})
    if isinstance(weekly, dict) and len(weekly) > 20:
        sorted_keys = sorted(weekly.keys())
        keep_keys = set(sorted_keys[-16:])
        stats["weekly"] = {k: v for k, v in weekly.items() if k in keep_keys}

    if old_count > 0:
        log(f"Cleanup removed {old_count} records older than {_HISTORY_DAYS} days")

    filepath = os.path.join(get_project_root(), "data", "posted_news.json")
    return save_json(filepath, data)


def is_topic_already_posted(
    fingerprint: str,
    posted_data: dict,
    similarity_threshold: float = 0.75,
) -> bool:
    if not fingerprint:
        return False

    posts = posted_data.get("posts", [])
    for post in posts:
        posted_fp = post.get("topic_fingerprint", "")
        if not posted_fp:
            continue
        if _fingerprint_similarity(fingerprint, posted_fp) >= similarity_threshold:
            return True
    return False


def is_already_posted(url: str, title: str, posted_data: dict) -> bool:
    posts = posted_data.get("posts", [])
    fingerprint = generate_topic_fingerprint(title)

    for post in posts:
        posted_url = post.get("url", "") or post.get("original_url", "")
        if posted_url and url and posted_url == url:
            return True

        posted_title = post.get("title", "")
        if is_similar_title(title, posted_title):
            return True

        posted_fp = post.get("topic_fingerprint", "")
        if posted_fp and fingerprint:
            if _fingerprint_similarity(fingerprint, posted_fp) >= 0.75:
                return True

    return False


def get_today_post_count(posted_data: dict) -> int:
    today = get_today_str()
    daily_counts = posted_data.get("daily_counts", {})
    return daily_counts.get(today, 0)


def get_last_check_time(posted_data: dict) -> datetime:
    turkey_tz = timezone(timedelta(hours=3))
    default_fallback = get_turkey_now() - timedelta(hours=6)
    raw_value = posted_data.get("last_check_time")

    if not raw_value or not isinstance(raw_value, str):
        log("last_check_time not found, fallback to 6 hours ago")
        return default_fallback

    try:
        parsed = datetime.fromisoformat(raw_value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=turkey_tz)

        now = get_turkey_now()
        if parsed > now:
            log("last_check_time is in the future, corrected", "WARNING")
            return now - timedelta(hours=1)

        max_age = now - timedelta(hours=48)
        if parsed < max_age:
            log("last_check_time older than 48h, fallback applied", "WARNING")
            return default_fallback

        return parsed
    except (ValueError, TypeError) as exc:
        log(f"last_check_time parse error: {exc}", "WARNING")
        return default_fallback


def save_last_check_time(posted_data: dict) -> None:
    now = get_turkey_now()
    posted_data["last_check_time"] = now.isoformat()
    log(f"last_check_time updated: {now.strftime('%Y-%m-%d %H:%M:%S')}")


def random_delay(max_minutes: int) -> None:
    if max_minutes <= 0:
        return
    total_seconds = random.randint(0, max_minutes * 60)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    log(f"Random delay: {minutes}m {seconds}s")
    time.sleep(total_seconds)
