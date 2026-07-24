"""
agents/fetcher_utils.py - Fetcher Yardımcı Fonksiyonları ve URL İşlemleri
Tip dönüşümleri, URL doğrulama, Nitter/Twitter CDN çözümleme ve HTTP istekleri burada.
"""
import os
import re
import sys
import time
import requests
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse, unquote
from core.logger import log

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
    "logo", "icon", "avatar", "sprite", "pixel", "ads", "banner", "favicon",
    "editor", "author", "profile", "yazar", "cookie", "uygulama-indir",
    "dh-oneriyor", "dh-cookie", "instagram-big", "populer-",
)
_IMAGE_HINT_PATHS = (
    "/wp-content/uploads/", "/uploads/", "/images/", "/image/", "/img/", "/media/",
)
_TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
_RESIZE_QUERY_KEYS = {"w", "h", "width", "height", "resize", "fit", "crop", "quality", "q"}

_NITTER_PIC_PATTERN = re.compile(
    r"^/pic/(?:orig/)?(?:media%2F|media/)([A-Za-z0-9_\-]+\.[a-zA-Z]{3,4})",
    re.IGNORECASE,
)
_TWITTER_CDN_HOSTS = {"pbs.twimg.com", "ton.twimg.com", "video.twimg.com"}

# ── Tip Dönüşümleri ───────────────────────────────────────────────────────────

def _is_test_mode() -> bool:
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    return "--test" in sys.argv

def _safe_int(value, default: int) -> int:
    try: return int(value)
    except Exception: return default

def _safe_float(value, default: float) -> float:
    try: return float(value)
    except Exception: return default

def _safe_int_min(value, default: int, minimum: int) -> int:
    parsed = _safe_int(value, default)
    return parsed if parsed >= minimum else minimum

def _safe_float_min(value, default: float, minimum: float = 0.0) -> float:
    parsed = _safe_float(value, default)
    return parsed if parsed >= minimum else minimum

def _coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool): return value
    if value is None: return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}: return True
    if s in {"0", "false", "no", "off"}: return False
    return default

def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None: return default
    return _coerce_bool(raw, default)

def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None: return default
    return _safe_int(raw, default)

def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None: return default
    return _safe_float(raw, default)

def _turkish_lower(text: str) -> str:
    return text.replace("I", "i").lower()

# ── URL & Nitter ──────────────────────────────────────────────────────────────

def _is_nitter_feed(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "nitter." in host or host.startswith("nitter")

def _is_nitter_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "nitter." in host or host.startswith("nitter")

def _nitter_to_twitter_url(nitter_url: str) -> str:
    if not nitter_url: return ""
    parsed = urlparse(nitter_url)
    path = parsed.path or ""
    m = re.search(r"(/[^/]+/status/\d+)", path)
    if m: return f"https://x.com{m.group(1)}"
    return ""

def _is_profile_image_url(url: str) -> bool:
    lower = url.lower()
    return "/profile_images/" in lower or "/profile_banners/" in lower

def _resolve_nitter_image_url(raw_url: str, nitter_base: str) -> str:
    if not raw_url: return ""
    parsed_raw = urlparse(raw_url)
    if parsed_raw.netloc in _TWITTER_CDN_HOSTS: return raw_url
    
    path = parsed_raw.path if parsed_raw.scheme else raw_url
    m = _NITTER_PIC_PATTERN.match(path)
    if m:
        filename = m.group(1)
        filename = unquote(filename)
        name_part, _, ext_part = filename.rpartition(".")
        ext_part = ext_part.lower()
        quality = "orig" if "/orig/" in path else "large"
        return f"https://pbs.twimg.com/media/{filename}?format={ext_part}&name={quality}"
        
    if _is_nitter_url(raw_url) and "/pic/" in raw_url: return raw_url
    return ""

# ── HTTP ──────────────────────────────────────────────────────────────────────

def _request_with_retry(url: str, timeout: int = 20, attempts: int = 3, base_wait_seconds: float = 1.5) -> requests.Response:
    last_exc = None
    headers = {"User-Agent": _USER_AGENT}
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            return response
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as exc:
            last_exc = exc
            log(f"HTTP deneme hatasi ({attempt}/{attempts}) url={url} -> {exc}", "WARNING" if attempt < attempts else "ERROR")
            if attempt < attempts: time.sleep(base_wait_seconds * (2 ** (attempt - 1)))
        except Exception as exc:
            last_exc = exc
            log(f"Beklenmeyen istek hatasi ({attempt}/{attempts}) url={url} -> {exc}", "WARNING" if attempt < attempts else "ERROR")
            if attempt < attempts: time.sleep(base_wait_seconds * (2 ** (attempt - 1)))
    if last_exc: raise last_exc
    raise RuntimeError("HTTP request failed without exception detail")

# ── Görsel URL İşlemleri ──────────────────────────────────────────────────────

def _normalize_image_url(raw_url: str, page_url: str = "") -> str:
    if not raw_url: return ""
    candidate = raw_url.strip()
    if not candidate: return ""
    if "/pic/" in candidate:
        resolved = _resolve_nitter_image_url(candidate, page_url)
        if resolved: return resolved
    if candidate.startswith("//"): candidate = f"https:{candidate}"
    if page_url: candidate = urljoin(page_url, candidate)
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc: return ""
    query_items = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in _TRACKING_QUERY_KEYS]
    cleaned = parsed._replace(query=urlencode(query_items), fragment="")
    return urlunparse(cleaned)

def _normalize_path_for_candidate_key(path: str) -> str:
    if not path: return path
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
    path = re.sub(r"-(\d{2,4})x(\d{2,4})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", r"\3", path, flags=re.IGNORECASE)
    filtered_qs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in _RESIZE_QUERY_KEYS]
    return urlunparse(parsed._replace(path=path, query=urlencode(filtered_qs), fragment="")).lower()

def _looks_like_noise_image(url: str) -> bool:
    lower_url = url.lower()
    parsed = urlparse(lower_url)
    path = parsed.path or ""
    host = parsed.netloc or ""
    if host in _TWITTER_CDN_HOSTS:
        if "/profile_images/" in path or "/profile_banners/" in path: return True
        return False
    if _is_nitter_url(lower_url) and "/pic/" in lower_url:
        if "profile_images" in lower_url or "profile_banners" in lower_url: return True
        return False
    if any(hint in lower_url for hint in _IMAGE_NOISE_HINTS): return True
    if "/content/img/" in path: return True
    return False

def _is_probable_image_url(url: str) -> bool:
    lower = url.lower()
    parsed = urlparse(lower)
    host = parsed.netloc or ""
    if host in _TWITTER_CDN_HOSTS: return True
    if _is_nitter_url(lower) and "/pic/" in lower: return True
    if parsed.path.endswith(_DISALLOWED_IMAGE_EXTENSIONS): return False
    if "/images/editor/" in lower or "/images/images/editor/" in lower: return False
    if any(x in lower for x in ("/author/", "/profile/", "/avatar/")): return False
    if any(ext in lower for ext in _IMAGE_EXTENSIONS): return True
    if "image" in lower: return True
    if any(p in lower for p in _IMAGE_HINT_PATHS): return True
    return False

def _donanimhaber_variants(url: str) -> list[str]:
    variants = [url]
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if "donanimhaber.com" not in host: return variants
    upgraded_path = re.sub(r"/src_\d{2,4}x\d{2,4}x", "/src/", path, flags=re.IGNORECASE)
    if upgraded_path != path: variants.append(urlunparse(parsed._replace(path=upgraded_path)))
    m_idx = re.search(r"(\d{4,7})_(\d+)(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", path, re.IGNORECASE)
    if m_idx:
        base_id = m_idx.group(1); ext = m_idx.group(3); prefix = path[: m_idx.start()]
        for i in range(0, 6):
            p = f"{prefix}{base_id}_{i}{ext}"
            variants.append(urlunparse(parsed._replace(path=p)))
    m_plain = re.search(r"(\d{4,7})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", path, re.IGNORECASE)
    if m_plain:
        base_id = m_plain.group(1); ext = m_plain.group(2); prefix = path[: m_plain.start()]
        for i in range(0, 6):
            p = f"{prefix}{base_id}_{i}{ext}"
            variants.append(urlunparse(parsed._replace(path=p)))
    seen = set(); unique = []
    for item in variants:
        if item and item not in seen:
            seen.add(item); unique.append(item)
    return unique

def _thumbnail_to_original_variants(url: str) -> list[str]:
    variants = [url]
    parsed = urlparse(url)
    path = parsed.path or ""
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    wp_thumb_pattern = re.compile(r"-(\d{2,4})x(\d{2,4})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", re.IGNORECASE)
    if wp_thumb_pattern.search(path):
        original_path = wp_thumb_pattern.sub(r"\3", path)
        variants.append(urlunparse(parsed._replace(path=original_path)))
    if query_items:
        filtered_query = [(k, v) for k, v in query_items if k.lower() not in _RESIZE_QUERY_KEYS]
        if len(filtered_query) != len(query_items):
            variants.append(urlunparse(parsed._replace(query=urlencode(filtered_query))))
    filename_cleaned_path = re.sub(r"(?i)([-_](small|thumb|thumbnail|medium|preview))(?=\.)", "", path)
    if filename_cleaned_path != path:
        variants.append(urlunparse(parsed._replace(path=filename_cleaned_path)))
    for item in list(variants):
        variants.extend(_donanimhaber_variants(item))
    unique = []; seen = set()
    for item in variants:
        if item and item not in seen:
            seen.add(item); unique.append(item)
    return unique

def _extract_best_src_from_srcset(srcset: str, page_url: str) -> str:
    best_url = ""; best_score = -1.0
    for item in srcset.split(","):
        item = item.strip()
        if not item: continue
        parts = item.split()
        url_part = _normalize_image_url(parts[0], page_url)
        if not url_part: continue
        score = 1.0
        if len(parts) > 1:
            descriptor = parts[1].lower()
            if descriptor.endswith("w"):
                try: score = float(descriptor[:-1])
                except ValueError: score = 1.0
            elif descriptor.endswith("x"):
                try: score = float(descriptor[:-1]) * 1000
                except ValueError: score = 1.0
        if score > best_score:
            best_score = score; best_url = url_part
    return best_url
