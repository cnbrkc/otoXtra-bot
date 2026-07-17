"""
agents/agent_image.py - Gorsel Isleme Ajani (v5.2 - Nitter Image Fix)

v5.2:
  - FIX: _is_probable_image_url() Nitter /pic/ ve pbs.twimg.com URL'lerini taniyor.
  - FIX: _looks_like_noise() Twitter CDN ve Nitter gorsel URL'lerini koruyuyor.
  - FIX: scrape_article_image_urls() Nitter tweet sayfalarindan gorsel cekiyor.
  - YENI: _is_nitter_url(), _resolve_nitter_image_url(),
          _extract_nitter_images_from_page() fonksiyonlari eklendi.

v5.1 FIXED:
  - CRITICAL FIX: Minimum image size lowered (900x500 -> 738x400)
  - CRITICAL FIX: Aspect ratio relaxed (0.8-2.1 -> 0.7-2.3)
  - Better RSS feed compatibility (most feeds use 600-800px images)

v5.0:
  - DonanimHaber gibi sitelerde script/json icinden gorsel URL toplama.
  - Domain-ozel URL varyant uretimi.
  - Gurultu gorseller icin daha erken eleme.
  - picture/source[srcset] tagleri taramaya dahil edildi.
  - Aday deneme limiti multi-image senaryoda dinamik artirildi.
  - article_script kaynak tipi eklendi.
"""

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse, unquote

import requests
from PIL import Image, ImageDraw
from bs4 import BeautifulSoup

from core.config_loader import get_project_root, load_config
from core.logger import log
from core.state_manager import get_stage, init_pipeline, set_stage


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_REQUEST_TIMEOUT = 15
_MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024

_DEFAULT_MIN_IMAGE_WIDTH = 738
_DEFAULT_MIN_IMAGE_HEIGHT = 400
_DEFAULT_MIN_IMAGE_AREA = 337500
_DEFAULT_MIN_ASPECT_RATIO = 0.7
_DEFAULT_MAX_ASPECT_RATIO = 2.3

_FALLBACK_BG_COLOR = (18, 25, 44)
_FALLBACK_STRIPE_COLOR = (24, 35, 60)

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
_DISALLOWED_IMAGE_EXTENSIONS = (".svg", ".ico")
_NOISE_HINTS = (
    "logo",
    "icon",
    "avatar",
    "sprite",
    "favicon",
    "ads",
    "banner",
    "pixel",
    "editor",
    "author",
    "profile",
    "yazar",
    "cookie",
    "uygulama-indir",
    "dh-oneriyor",
    "dh-cookie",
    "instagram-big",
    "populer-",
)
_IMAGE_HINT_PATHS = ("/wp-content/uploads/", "/uploads/", "/images/", "/image/", "/img/", "/media/")
_TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
_RESIZE_QUERY_KEYS = {"w", "h", "width", "height", "resize", "fit", "crop", "quality", "q"}

_DEFAULT_PERCEPTUAL_HASH_THRESHOLD = 6

_DEFAULT_PLATFORM_MAX_IMAGE_WIDTH = 4096
_DEFAULT_PLATFORM_MAX_IMAGE_HEIGHT = 4096
_DEFAULT_PLATFORM_MAX_IMAGE_AREA = 16_000_000
_DEFAULT_PLATFORM_MAX_IMAGE_BYTES = 20 * 1024 * 1024

_SOURCE_PRIORITY = {
    "meta_og": 0,
    "meta_twitter": 0,
    "nitter_still": 0,       # v5.2: Nitter still-image en yuksek oncelik
    "nitter_card": 1,        # v5.2: Nitter card image
    "article_script": 1,
    "article_img": 1,
    "article_field": 2,
    "rss_field": 2,
    "article_candidates_field": 2,
    "unknown": 3,
}

_SOURCE_SCORE_BONUS = {
    "meta_og": 12.0,
    "meta_twitter": 10.0,
    "nitter_still": 14.0,    # v5.2: Tweet'in ana gorseli en yuksek bonus
    "nitter_card": 10.0,     # v5.2: Tweet card gorseli
    "article_script": 8.0,
    "article_img": 7.0,
    "article_field": 4.0,
    "rss_field": 3.0,
    "article_candidates_field": 3.0,
    "unknown": 0.0,
}

# v5.2: Nitter sabitleri
_NITTER_PIC_PATTERN = re.compile(
    r"^/pic/(?:orig/)?(?:media%2F|media/)([A-Za-z0-9_\-]+\.[a-zA-Z]{3,4})",
    re.IGNORECASE,
)
_TWITTER_CDN_HOSTS = {"pbs.twimg.com", "ton.twimg.com", "video.twimg.com"}


def _read_int_env(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except Exception:
        return None


def _read_float_env(name: str) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return float(raw.strip())
    except Exception:
        return None


def _read_bool_env(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ── v5.2: Nitter yardimci fonksiyonlari ──────────────────────────────────────

def _is_nitter_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "nitter." in host or host.startswith("nitter")


def _nitter_to_twitter_url(nitter_url: str) -> str:
    """
    Nitter tweet URL'sini orijinal Twitter/x.com URL'sine cevirir.
    """
    if not nitter_url:
        return ""
    parsed = urlparse(nitter_url)
    path = parsed.path or ""
    m = re.search(r"(/[^/]+/status/\d+)", path)
    if m:
        return f"https://x.com{m.group(1)}"
    return ""


def _extract_twitter_og_image(tweet_url: str) -> list[dict]:
    """
    x.com tweet sayfasindan og:image meta tagini ceker.
    Twitter/X sayfalari pbs.twimg.com URL'leri icerir.
    """
    if not tweet_url:
        return []
    results: list[dict] = []
    seen: set[str] = set()
    try:
        response = requests.get(
            tweet_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup.select('meta[property="og:image"]'):
            img_url = tag.get("content", "")
            if img_url and "pbs.twimg.com" in img_url:
                parsed = urlparse(img_url)
                if "name=" not in parsed.query:
                    img_url = f"{img_url}&name=orig" if "?" in img_url else f"{img_url}?name=orig"
                if img_url not in seen:
                    seen.add(img_url)
                    results.append({"url": img_url, "source_type": "nitter_still"})

        for tag in soup.select('meta[name="twitter:image"]'):
            img_url = tag.get("content", "")
            if img_url and "pbs.twimg.com" in img_url:
                parsed = urlparse(img_url)
                if "name=" not in parsed.query:
                    img_url = f"{img_url}&name=orig" if "?" in img_url else f"{img_url}?name=orig"
                if img_url not in seen:
                    seen.add(img_url)
                    results.append({"url": img_url, "source_type": "nitter_card"})

    except Exception as exc:
        log(f"Twitter og:image cekme hatasi: {tweet_url[:80]} -> {exc}", "WARNING")
    return results



def _resolve_nitter_image_url(raw_url: str, nitter_base: str = "") -> str:
    """
    Nitter /pic/orig/media%2FABCdef.jpg  ->
    https://pbs.twimg.com/media/ABCdef.jpg?format=jpg&name=orig
    """
    if not raw_url:
        return ""

    parsed_raw = urlparse(raw_url)
    if parsed_raw.netloc in _TWITTER_CDN_HOSTS:
        return raw_url  # Zaten Twitter CDN

    path = parsed_raw.path if parsed_raw.scheme else raw_url

    m = _NITTER_PIC_PATTERN.match(path)
    if m:
        filename = unquote(m.group(1))
        name_part, _, ext_part = filename.rpartition(".")
        ext_part = ext_part.lower()
        quality = "orig" if "/orig/" in path else "large"
        return f"https://pbs.twimg.com/media/{filename}?format={ext_part}&name={quality}"

    if _is_nitter_url(raw_url) and "/pic/" in raw_url:
        return raw_url  # Taninamayan Nitter formati, olduğu gibi dene

    return ""


def _extract_nitter_images_from_page(tweet_url: str) -> list[dict]:
    """
    Nitter tweet sayfasindan gorsel URL'lerini toplar.
    Her gorsel icin {"url": ..., "source_type": "nitter_still"|"nitter_card"} dict'i doner.
    """
    if not tweet_url or not _is_nitter_url(tweet_url):
        return []

    parsed = urlparse(tweet_url)
    nitter_base = f"{parsed.scheme}://{parsed.netloc}"

    try:
        response = requests.get(
            tweet_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as exc:
        log(f"Nitter tweet sayfasi alinamadi: {tweet_url[:80]} -> {exc}", "WARNING")
        return []

    results: list[dict] = []
    seen: set[str] = set()

    def _add(url: str, stype: str) -> None:
        if url and url not in seen:
            seen.add(url)
            results.append({"url": url, "source_type": stype})

    # 1. <a class="still-image"> - tweet'in ana gorselleri
    for a_tag in soup.find_all("a", class_="still-image"):
        href = a_tag.get("href", "")
        resolved = _resolve_nitter_image_url(href, nitter_base)
        if resolved:
            _add(resolved, "nitter_still")
        img = a_tag.find("img")
        if img:
            src = img.get("src", "")
            resolved_src = _resolve_nitter_image_url(src, nitter_base)
            if resolved_src:
                _add(resolved_src, "nitter_still")

    # 2. card-image div'leri
    for div in soup.find_all("div", class_=lambda c: c and ("card-image" in c or "attachment" in c)):
        for img in div.find_all("img"):
            src = img.get("src", "")
            resolved = _resolve_nitter_image_url(src, nitter_base)
            if resolved:
                _add(resolved, "nitter_card")
        for a_tag in div.find_all("a"):
            href = a_tag.get("href", "")
            resolved = _resolve_nitter_image_url(href, nitter_base)
            if resolved:
                _add(resolved, "nitter_card")

    # 3. Tum <img> src="/pic/..."
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "/pic/" in src:
            resolved = _resolve_nitter_image_url(src, nitter_base)
            if resolved:
                _add(resolved, "nitter_card")

    # 4. Tum <a href="/pic/...">
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        if "/pic/" in href:
            resolved = _resolve_nitter_image_url(href, nitter_base)
            if resolved:
                _add(resolved, "nitter_card")

    log(f"Nitter sayfasindan {len(results)} gorsel bulundu: {tweet_url[:80]}")

    # v6.0: Nitter bos donerse orijinal Twitter URL'sine fallback yap
    if not results:
        twitter_url = _nitter_to_twitter_url(tweet_url)
        if twitter_url:
            log(f"Nitter sayfa bos, Twitter fallback deneniyor: {twitter_url[:80]}")
            twitter_images = _extract_twitter_og_image(twitter_url)
            if twitter_images:
                log(f"Twitter fallback'tan {len(twitter_images)} gorsel bulundu")
                results.extend(twitter_images)

    return results

# ─────────────────────────────────────────────────────────────────────────────


def _get_image_validation_limits() -> dict:
    min_width = _read_int_env("IMAGE_MIN_WIDTH")
    min_height = _read_int_env("IMAGE_MIN_HEIGHT")
    min_area = _read_int_env("IMAGE_MIN_AREA")
    min_aspect = _read_float_env("IMAGE_MIN_ASPECT_RATIO")
    max_aspect = _read_float_env("IMAGE_MAX_ASPECT_RATIO")

    limits = {
        "min_width": min_width if min_width and min_width > 0 else _DEFAULT_MIN_IMAGE_WIDTH,
        "min_height": min_height if min_height and min_height > 0 else _DEFAULT_MIN_IMAGE_HEIGHT,
        "min_area": min_area if min_area and min_area > 0 else _DEFAULT_MIN_IMAGE_AREA,
        "min_aspect": min_aspect if min_aspect and min_aspect > 0 else _DEFAULT_MIN_ASPECT_RATIO,
        "max_aspect": max_aspect if max_aspect and max_aspect > 0 else _DEFAULT_MAX_ASPECT_RATIO,
    }

    if limits["min_aspect"] >= limits["max_aspect"]:
        limits["min_aspect"] = _DEFAULT_MIN_ASPECT_RATIO
        limits["max_aspect"] = _DEFAULT_MAX_ASPECT_RATIO

    return limits


def _build_relaxed_limits(limits: dict) -> dict:
    min_w = int(limits.get("min_width", _DEFAULT_MIN_IMAGE_WIDTH))
    min_h = int(limits.get("min_height", _DEFAULT_MIN_IMAGE_HEIGHT))
    min_area = int(limits.get("min_area", _DEFAULT_MIN_IMAGE_AREA))
    min_aspect = float(limits.get("min_aspect", _DEFAULT_MIN_ASPECT_RATIO))
    max_aspect = float(limits.get("max_aspect", _DEFAULT_MAX_ASPECT_RATIO))

    relaxed = {
        "min_width": max(700, int(min_w * 0.82)),
        "min_height": max(390, int(min_h * 0.80)),
        "min_area": max(320000, int(min_area * 0.75)),
        "min_aspect": max(0.70, min_aspect - 0.12),
        "max_aspect": min(2.35, max_aspect + 0.18),
    }

    if relaxed["min_aspect"] >= relaxed["max_aspect"]:
        relaxed["min_aspect"] = 0.70
        relaxed["max_aspect"] = 2.35

    return relaxed


def _get_platform_resize_limits() -> dict:
    max_width = _read_int_env("IMAGE_PLATFORM_MAX_WIDTH")
    max_height = _read_int_env("IMAGE_PLATFORM_MAX_HEIGHT")
    max_area = _read_int_env("IMAGE_PLATFORM_MAX_AREA")
    max_bytes = _read_int_env("IMAGE_PLATFORM_MAX_BYTES")

    return {
        "max_width": max_width if max_width and max_width > 0 else _DEFAULT_PLATFORM_MAX_IMAGE_WIDTH,
        "max_height": max_height if max_height and max_height > 0 else _DEFAULT_PLATFORM_MAX_IMAGE_HEIGHT,
        "max_area": max_area if max_area and max_area > 0 else _DEFAULT_PLATFORM_MAX_IMAGE_AREA,
        "max_bytes": max_bytes if max_bytes and max_bytes > 0 else _DEFAULT_PLATFORM_MAX_IMAGE_BYTES,
    }


def _should_resize_for_platform(image_path: str, limits: dict) -> tuple[bool, str]:
    try:
        with Image.open(image_path) as img:
            width, height = img.size
        area = width * height
        file_bytes = os.path.getsize(image_path)

        reasons: list[str] = []

        if width > int(limits["max_width"]):
            reasons.append(f"width>{limits['max_width']}")
        if height > int(limits["max_height"]):
            reasons.append(f"height>{limits['max_height']}")
        if area > int(limits["max_area"]):
            reasons.append(f"area>{limits['max_area']}")
        if file_bytes > int(limits["max_bytes"]):
            reasons.append(f"bytes>{limits['max_bytes']}")

        if reasons:
            return True, ",".join(reasons)

        return False, f"within_limits:{width}x{height}:{file_bytes // 1024}KB"

    except Exception as exc:
        return True, f"meta_read_error:{exc}"


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_url(raw_url: str, base_url: str = "") -> str:
    if not raw_url:
        return ""

    url = raw_url.strip()
    if not url:
        return ""

    # v5.2: Nitter /pic/ URL'lerini coz
    if "/pic/" in url:
        resolved = _resolve_nitter_image_url(url, base_url)
        if resolved:
            return resolved

    if url.startswith("//"):
        url = f"https:{url}"

    if base_url:
        url = urljoin(base_url, url)

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    filtered_qs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_QUERY_KEYS
    ]
    cleaned = parsed._replace(query=urlencode(filtered_qs), fragment="")
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


def _looks_like_noise(url: str) -> bool:
    """
    v5.2: pbs.twimg.com/media/ ve Nitter /pic/ URL'leri asla gurultu degil.
    """
    lower = url.lower()
    parsed = urlparse(lower)
    path = parsed.path or ""
    host = parsed.netloc or ""

    # Twitter CDN ve Nitter gorsel URL'leri hic gurultu degil
    if host in _TWITTER_CDN_HOSTS:
        return False
    if _is_nitter_url(lower) and "/pic/" in lower:
        return False

    if any(hint in lower for hint in _NOISE_HINTS):
        return True
    if "/content/img/" in path:
        return True

    return False


def _is_probable_image_url(url: str) -> bool:
    """
    v5.2: Nitter /pic/ ve pbs.twimg.com URL'lerini taniyor.
    """
    lower = url.lower()
    parsed = urlparse(lower)
    host = parsed.netloc or ""

    # Twitter CDN her zaman gorsel
    if host in _TWITTER_CDN_HOSTS:
        return True

    # Nitter /pic/ her zaman gorsel
    if _is_nitter_url(lower) and "/pic/" in lower:
        return True

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


def _donanimhaber_variants(url: str) -> list[str]:
    variants: list[str] = [url]
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path or ""

    if "donanimhaber.com" not in host:
        return variants

    upgraded_path = re.sub(r"/src_\d{2,4}x\d{2,4}x", "/src/", path, flags=re.IGNORECASE)
    if upgraded_path != path:
        variants.append(urlunparse(parsed._replace(path=upgraded_path)))

    m_idx = re.search(r"(\d{4,7})_(\d+)(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", path, re.IGNORECASE)
    if m_idx:
        base_id = m_idx.group(1)
        ext = m_idx.group(3)
        prefix = path[: m_idx.start()]
        for i in range(0, 6):
            p = f"{prefix}{base_id}_{i}{ext}"
            variants.append(urlunparse(parsed._replace(path=p)))

    m_plain = re.search(r"(\d{4,7})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", path, re.IGNORECASE)
    if m_plain:
        base_id = m_plain.group(1)
        ext = m_plain.group(2)
        prefix = path[: m_plain.start()]
        for i in range(0, 6):
            p = f"{prefix}{base_id}_{i}{ext}"
            variants.append(urlunparse(parsed._replace(path=p)))

    seen: set[str] = set()
    unique: list[str] = []
    for item in variants:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _thumbnail_to_original_variants(url: str) -> list[str]:
    # Twitter CDN URL'leri icin variant uretme - zaten orijinal
    parsed_check = urlparse(url)
    if parsed_check.netloc in _TWITTER_CDN_HOSTS:
        return [url]

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
        filtered_qs = [(k, v) for k, v in query_items if k.lower() not in _RESIZE_QUERY_KEYS]
        if len(filtered_qs) != len(query_items):
            variants.append(urlunparse(parsed._replace(query=urlencode(filtered_qs))))

    filename_cleaned_path = re.sub(
        r"(?i)([-_](small|thumb|thumbnail|medium|preview))(?=\.)",
        "",
        path,
    )
    if filename_cleaned_path != path:
        variants.append(urlunparse(parsed._replace(path=filename_cleaned_path)))

    for v in list(variants):
        for dv in _donanimhaber_variants(v):
            variants.append(dv)

    seen: set[str] = set()
    unique: list[str] = []
    for item in variants:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


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


def _visual_signature(url: str) -> str:
    parsed = urlparse(url.lower())
    path = parsed.path or ""
    filename = path.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0]

    stem = re.sub(r"(^|[-_/])s\d+($|[-_])", r"\1sX\2", stem)
    stem = re.sub(r"[-_](small|thumb|thumbnail|medium|preview)$", "", stem)
    stem = re.sub(r"\d{2,4}x\d{2,4}", "", stem)
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")

    host = parsed.netloc.replace("www.", "")
    return f"{host}:{stem}"


def _dhash(path: str) -> int:
    with Image.open(path) as img:
        gray = img.convert("L").resize((9, 8), Image.LANCZOS)
        pixels = list(gray.getdata())

    bits = 0
    for y in range(8):
        row = pixels[y * 9 : (y + 1) * 9]
        for x in range(8):
            bits = (bits << 1) | (1 if row[x] > row[x + 1] else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _extract_best_src_from_srcset(srcset: str, page_url: str) -> str:
    best_url = ""
    best_score = -1.0

    for item in srcset.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split()
        candidate = _normalize_url(parts[0], page_url)
        if not candidate:
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
            best_url = candidate

    return best_url


def _walk_json_for_image_urls(node, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            lk = str(key).lower()
            if lk in {"image", "imageurl", "thumbnailurl", "contenturl", "url"}:
                if isinstance(value, str):
                    out.append(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            out.append(item)
                        elif isinstance(item, dict):
                            _walk_json_for_image_urls(item, out)
                elif isinstance(value, dict):
                    _walk_json_for_image_urls(value, out)
            else:
                _walk_json_for_image_urls(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_json_for_image_urls(item, out)


def _extract_json_image_urls(script_text: str) -> list[str]:
    if not script_text or not script_text.strip():
        return []
    text = script_text.strip()
    urls: list[str] = []

    try:
        data = json.loads(text)
        _walk_json_for_image_urls(data, urls)
        return urls
    except Exception:
        pass

    for m in re.finditer(r'https?://[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp|gif|bmp|avif)', text, re.IGNORECASE):
        urls.append(m.group(0))
    return urls


def _upsert_candidate(pool: list[dict], candidate: dict) -> None:
    key = candidate.get("key", "")
    if not key:
        return

    for idx, existing in enumerate(pool):
        if existing.get("key") != key:
            continue

        old_prio = int(existing.get("priority", 99))
        new_prio = int(candidate.get("priority", 99))
        if new_prio < old_prio:
            pool[idx] = candidate
        return

    pool.append(candidate)


def _append_field_candidates(
    pool: list[dict],
    value: str,
    base_url: str,
    source_type: str,
) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    normalized = _normalize_url(value.strip(), base_url)
    if not normalized:
        return
    for variant in _thumbnail_to_original_variants(normalized):
        _upsert_candidate(
            pool,
            {
                "url": variant,
                "key": _candidate_key(variant),
                "source_type": source_type,
                "priority": _SOURCE_PRIORITY.get(source_type, 99),
            },
        )


def _add_scrape_candidate(
    pool: list[dict],
    raw_url: str,
    page_url: str,
    source_type: str,
) -> None:
    normalized = _normalize_url(raw_url, page_url)
    if not normalized:
        return

    for variant in _thumbnail_to_original_variants(normalized):
        lower = variant.lower()
        if _looks_like_noise(lower):
            continue
        if not _is_probable_image_url(lower):
            continue
        _upsert_candidate(
            pool,
            {
                "url": variant,
                "key": _candidate_key(variant),
                "source_type": source_type,
                "priority": _SOURCE_PRIORITY.get(source_type, 99),
            },
        )


def _download_image_with_reason(image_url: str, limits: dict) -> tuple[Optional[str], str]:
    if not image_url:
        return None, "empty_url"

    min_width = int(limits.get("min_width", _DEFAULT_MIN_IMAGE_WIDTH))
    min_height = int(limits.get("min_height", _DEFAULT_MIN_IMAGE_HEIGHT))
    min_area = int(limits.get("min_area", _DEFAULT_MIN_IMAGE_AREA))
    min_aspect = float(limits.get("min_aspect", _DEFAULT_MIN_ASPECT_RATIO))
    max_aspect = float(limits.get("max_aspect", _DEFAULT_MAX_ASPECT_RATIO))

    try:
        response = requests.get(
            image_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
            stream=True,
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if content_type and not content_type.startswith("image/"):
            return None, f"not_image_content_type:{content_type}"

        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg",
            delete=False,
            prefix="otoxtra_img_",
        )
        temp_path = temp_file.name

        downloaded = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            downloaded += len(chunk)
            if downloaded > _MAX_DOWNLOAD_BYTES:
                temp_file.close()
                _safe_unlink(temp_path)
                return None, "download_too_large"
            temp_file.write(chunk)
        temp_file.close()

        try:
            with Image.open(temp_path) as img:
                img_width, img_height = img.size

            area = img_width * img_height
            if img_width < min_width or img_height < min_height or area < min_area:
                _safe_unlink(temp_path)
                return None, f"too_small:{img_width}x{img_height}:area={area}"

            ratio = img_width / img_height if img_height else 0.0
            if ratio < min_aspect or ratio > max_aspect:
                _safe_unlink(temp_path)
                return None, f"bad_aspect:{img_width}x{img_height}:ratio={ratio:.3f}"

        except Exception:
            _safe_unlink(temp_path)
            return None, "invalid_image_file"

        return temp_path, f"ok:{img_width}x{img_height}"

    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.RequestException as exc:
        return None, f"http_error:{exc}"
    except Exception as exc:
        return None, f"unexpected_error:{exc}"


def _read_image_meta(path: str) -> tuple[int, int, int]:
    with Image.open(path) as img:
        width, height = img.size
    size_kb = max(1, os.path.getsize(path) // 1024)
    return width, height, size_kb


def _score_image_quality(
    width: int,
    height: int,
    size_kb: int,
    source_type: str,
    target_ratio: float,
) -> tuple[float, str]:
    area = width * height
    ratio = width / height if height else 0.0

    resolution_score = min(25.0, (area / 1_200_000.0) * 25.0)
    ratio_diff = abs(ratio - target_ratio)
    aspect_score = max(0.0, 20.0 * (1.0 - min(ratio_diff / 1.2, 1.0)))

    if size_kb < 80:
        size_score = max(0.0, size_kb / 80.0 * 8.0)
    elif size_kb <= 900:
        size_score = 8.0
    else:
        size_score = max(0.0, 8.0 - min((size_kb - 900) / 600.0 * 8.0, 8.0))

    source_bonus = _SOURCE_SCORE_BONUS.get(source_type, 0.0)

    total = 45.0 + resolution_score + aspect_score + size_score + source_bonus
    detail = (
        f"res={resolution_score:.1f}, aspect={aspect_score:.1f}, "
        f"size={size_score:.1f}, src_bonus={source_bonus:.1f}, "
        f"ratio={ratio:.3f}, size_kb={size_kb}"
    )
    return total, detail


def _adaptive_perceptual_threshold(
    base_threshold: int,
    current_signature: str,
    previous_signature: str,
) -> int:
    if current_signature and previous_signature and current_signature == previous_signature:
        return base_threshold + 3
    return base_threshold


def _ai_search_image_url(article: dict) -> str:
    """
    AI (Gemini) kullanarak haber basligindan uygun bir gorsel URL'si bulur.
    Sadece diger tum yontemler basarisiz oldugunda cagrilir (son care).
    
    Returns:
        Public gorsel URL'si, bulunamazsa bos string
    """
    try:
        from core.ai_client import ask_ai
    except ImportError:
        log("AI gorsel arama: ai_client import edilemedi", "WARNING")
        return ""

    title = (article.get("title", "") or "").strip()
    if not title:
        log("AI gorsel arama: Baslik bos, atlanıyor", "WARNING")
        return ""

    prompt = (
        f"Find a publicly accessible image URL for this news headline. "
        f"Return ONLY the direct image URL (ending in .jpg, .jpeg, or .png), nothing else. "
        f"If you cannot find a suitable image, return the word NONE.\n\n"
        f"Headline: {title}"
    )

    try:
        log(f"AI gorsel arama baslatiliyor: {title[:60]}...")
        response = ask_ai(prompt, stage="image_search")
        if not response or not isinstance(response, str):
            log("AI gorsel arama: Bos/gecersiz yanit", "WARNING")
            return ""

        response = response.strip()

        if response.upper() == "NONE" or not response:
            log("AI gorsel arama: AI gorsel bulamadi", "INFO")
            return ""

        # URL formatini kontrol et
        if response.startswith("http") and any(
            ext in response.lower() for ext in (".jpg", ".jpeg", ".png", ".webp")
        ):
            log(f"AI gorsel arama: URL bulundu! {response[:80]}...")
            return response

        # AI bazen markdown formatinda veya ek metinle donuyor, URL'yi cikar
        import re
        _url_pattern = re.compile(r'https?://[^\\s"\'<>]+\\.(?:jpg|jpeg|png|webp)', re.IGNORECASE)
        url_match = _url_pattern.search(response)
        if url_match:
            found_url = url_match.group(0)
            log(f"AI gorsel arama: URL cikarildi! {found_url[:80]}...")
            return found_url

        log(f"AI gorsel arama: Yanit gecersiz format: {response[:100]}", "WARNING")
        return ""

    except Exception as exc:
        log(f"AI gorsel arama hata: {exc}", "WARNING")
        return ""


def _create_fallback_image(width: int, height: int) -> str:
    project_root = get_project_root()
    logo_candidates = [
        os.path.join(project_root, "assets", "logo_solid.png"),
        os.path.join(project_root, "assets", "logo_solid.jpg"),
        os.path.join(project_root, "assets", "logo.png"),
    ]
    logo_path = next((c for c in logo_candidates if os.path.exists(c)), None)

    try:
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg",
            delete=False,
            prefix="otoxtra_fallback_",
        )
        temp_path = temp_file.name
        temp_file.close()

        img = Image.new("RGB", (width, height), _FALLBACK_BG_COLOR)
        draw = ImageDraw.Draw(img)

        stripe_thickness = 2
        stripe_margin = int(height * 0.22)
        stripe_padding_x = int(width * 0.15)

        draw.rectangle(
            [stripe_padding_x, stripe_margin, width - stripe_padding_x, stripe_margin + stripe_thickness],
            fill=_FALLBACK_STRIPE_COLOR,
        )
        draw.rectangle(
            [
                stripe_padding_x,
                height - stripe_margin - stripe_thickness,
                width - stripe_padding_x,
                height - stripe_margin,
            ],
            fill=_FALLBACK_STRIPE_COLOR,
        )

        if logo_path:
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                logo_max_width = int(width * 0.25)

                logo_orig_w, logo_orig_h = logo_img.size
                aspect = logo_orig_h / logo_orig_w
                logo_new_width = logo_max_width
                logo_new_height = int(logo_new_width * aspect)

                max_logo_height = int(height * 0.30)
                if logo_new_height > max_logo_height:
                    logo_new_height = max_logo_height
                    logo_new_width = int(logo_new_height / aspect)

                logo_img = logo_img.resize((logo_new_width, logo_new_height), Image.LANCZOS)

                paste_x = (width - logo_new_width) // 2
                paste_y = (height - logo_new_height) // 2

                img_rgba = img.convert("RGBA")
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                overlay.paste(logo_img, (paste_x, paste_y))
                img_rgba = Image.alpha_composite(img_rgba, overlay)
                img = img_rgba.convert("RGB")

                logo_img.close()
                overlay.close()
                img_rgba.close()

            except Exception as logo_err:
                log(f"Yedek gorsele logo eklenemedi: {logo_err}", "WARNING")
        else:
            log("Logo dosyasi bulunamadi, sadece arka plan olusturuldu", "WARNING")

        img.save(temp_path, format="JPEG", quality=95)
        img.close()
        log(f"Yedek gorsel hazir: {temp_path}")
        return temp_path

    except Exception as exc:
        log(f"Yedek gorsel olusturma hatasi: {exc}", "ERROR")
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg",
            delete=False,
            prefix="otoxtra_emergency_",
        )
        temp_path = temp_file.name
        temp_file.close()
        img = Image.new("RGB", (width, height), (50, 50, 50))
        img.save(temp_path, format="JPEG", quality=85)
        img.close()
        return temp_path


def resize_and_crop(image_path: str, target_width: int, target_height: int) -> str:
    try:
        img = Image.open(image_path)
        img_width, img_height = img.size

        target_ratio = target_width / target_height
        current_ratio = img_width / img_height

        if current_ratio > target_ratio:
            new_height = target_height
            new_width = int(img_width * (target_height / img_height))
            img = img.resize((new_width, new_height), Image.LANCZOS)
            left = (new_width - target_width) // 2
            img = img.crop((left, 0, left + target_width, new_height))
        elif current_ratio < target_ratio:
            new_width = target_width
            new_height = int(img_height * (target_width / img_width))
            img = img.resize((new_width, new_height), Image.LANCZOS)
            top = (new_height - target_height) // 2
            img = img.crop((0, top, new_width, top + target_height))
        else:
            img = img.resize((target_width, target_height), Image.LANCZOS)

        if img.size != (target_width, target_height):
            img = img.resize((target_width, target_height), Image.LANCZOS)

        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.save(image_path, format="JPEG", quality=90)
        img.close()
        return image_path

    except Exception as exc:
        log(f"Boyutlandirma hatasi: {exc}", "WARNING")
        return image_path


def add_logo(image_path: str) -> str:
    settings_config = load_config("settings")
    images_settings = settings_config.get("images", {})

    logo_position = images_settings.get("logo_position", "bottom_right")
    logo_opacity = images_settings.get("logo_opacity", 0.7)
    logo_size_percent = images_settings.get("logo_size_percent", 12)
    padding = 20

    logo_path = os.path.join(get_project_root(), "assets", "logo.png")
    if not os.path.exists(logo_path):
        log(f"Logo dosyasi bulunamadi: {logo_path}", "WARNING")
        return image_path

    try:
        base_img = Image.open(image_path)
        base_width, base_height = base_img.size
        if base_img.mode != "RGBA":
            base_img = base_img.convert("RGBA")

        logo_img = Image.open(logo_path)
        if logo_img.mode != "RGBA":
            logo_img = logo_img.convert("RGBA")

        logo_target_width = int(base_width * logo_size_percent / 100)
        logo_orig_w, logo_orig_h = logo_img.size
        aspect = logo_orig_h / logo_orig_w
        logo_target_height = int(logo_target_width * aspect)
        logo_img = logo_img.resize((logo_target_width, logo_target_height), Image.LANCZOS)

        r, g, b, alpha = logo_img.split()
        alpha = alpha.point(lambda p: int(p * logo_opacity))
        logo_img = Image.merge("RGBA", (r, g, b, alpha))

        logo_w, logo_h = logo_img.size
        position_map = {
            "bottom_right": (base_width - logo_w - padding, base_height - logo_h - padding),
            "bottom_left": (padding, base_height - logo_h - padding),
            "top_right": (base_width - logo_w - padding, padding),
            "top_left": (padding, padding),
        }
        pos_x, pos_y = position_map.get(logo_position, position_map["bottom_right"])
        pos_x = max(0, pos_x)
        pos_y = max(0, pos_y)

        overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        overlay.paste(logo_img, (pos_x, pos_y))
        base_img = Image.alpha_composite(base_img, overlay)

        final_img = base_img.convert("RGB")
        final_img.save(image_path, format="JPEG", quality=90)

        base_img.close()
        logo_img.close()
        overlay.close()
        final_img.close()
        return image_path

    except Exception as exc:
        log(f"Logo ekleme hatasi: {exc}", "WARNING")
        return image_path


def download_image(image_url: str) -> Optional[str]:
    limits = _get_image_validation_limits()
    image_path, reason = _download_image_with_reason(image_url, limits)
    if image_path:
        dims = reason.replace("ok:", "")
        log(f"Gorsel indirildi: {dims}")
        return image_path
    log(f"Gorsel indirilemedi: {reason}", "WARNING")
    return None


def scrape_article_image_urls(url: str, max_candidates: int = 8) -> list[dict]:
    """
    v5.2: Nitter URL'si gelirse _extract_nitter_images_from_page() kullanilir.
    Normal siteler icin eski BeautifulSoup logici korunur.
    """
    if not url:
        return []

    # v6.0: Nitter URL'si ise ozel fonksiyon + Twitter fallback
    if _is_nitter_url(url):
        nitter_results = _extract_nitter_images_from_page(url)
        pool: list[dict] = []
        for item in nitter_results[:max_candidates]:
            candidate_url = item.get("url", "")
            stype = item.get("source_type", "nitter_still")
            if candidate_url:
                _upsert_candidate(
                    pool,
                    {
                        "url": candidate_url,
                        "key": _candidate_key(candidate_url),
                        "source_type": stype,
                        "priority": _SOURCE_PRIORITY.get(stype, 0),
                    },
                )

        # v6.0: Nitter'dan gorsel gelmezse direkt Twitter/x.com sayfasini dene
        if not pool:
            twitter_url = _nitter_to_twitter_url(url)
            if twitter_url:
                log(f"Nitter scrape bosa dustu, Twitter article scrape deneniyor: {twitter_url[:80]}")
                # Twitter URL'sini normal site gibi scrape et
                try:
                    response = requests.get(
                        twitter_url,
                        headers={"User-Agent": _USER_AGENT},
                        timeout=_REQUEST_TIMEOUT,
                    )
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, "html.parser")

                    meta_selectors = [
                        ('meta[property="og:image"]', "content", "meta_og"),
                        ('meta[property="og:image:url"]', "content", "meta_og"),
                        ('meta[name="twitter:image"]', "content", "meta_twitter"),
                        ('meta[name="twitter:image:src"]', "content", "meta_twitter"),
                    ]
                    for selector, attr, source_type in meta_selectors:
                        for tag in soup.select(selector):
                            _add_scrape_candidate(pool, tag.get(attr, ""), twitter_url, source_type)

                    log(f"Twitter scrape'tan {len(pool)} gorsel adayi bulundu")
                except Exception as exc:
                    log(f"Twitter article scrape hatasi: {exc}", "WARNING")

        return pool

    try:
        log(f"Sayfadan gorsel adaylari araniyor: {url[:100]}")

        response = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        pool_normal: list[dict] = []

        meta_selectors = [
            ('meta[property="og:image"]', "content", "meta_og"),
            ('meta[property="og:image:url"]', "content", "meta_og"),
            ('meta[name="twitter:image"]', "content", "meta_twitter"),
            ('meta[name="twitter:image:src"]', "content", "meta_twitter"),
        ]
        for selector, attr, source_type in meta_selectors:
            for tag in soup.select(selector):
                _add_scrape_candidate(pool_normal, tag.get(attr, ""), url, source_type)

        for img in soup.find_all("img"):
            src_list = [
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
                    src_list.append(best_srcset)

            for src in src_list:
                _add_scrape_candidate(pool_normal, src, url, "article_img")

        for source in soup.find_all("source"):
            srcset = source.get("srcset", "") or source.get("data-srcset", "")
            if srcset:
                best_srcset = _extract_best_src_from_srcset(srcset, url)
                if best_srcset:
                    _add_scrape_candidate(pool_normal, best_srcset, url, "article_img")

        for script in soup.find_all("script"):
            script_text = script.string or script.get_text() or ""
            if not script_text.strip():
                continue
            for script_url in _extract_json_image_urls(script_text):
                _add_scrape_candidate(pool_normal, script_url, url, "article_script")

        ordered = sorted(pool_normal, key=lambda x: (int(x.get("priority", 99)), x.get("url", "")))
        cleaned = ordered[:max_candidates]

        log(f"Sayfadan {len(cleaned)} gorsel adayi bulundu")
        return cleaned

    except requests.exceptions.Timeout:
        log(f"Sayfa cekme zaman asimi: {url[:100]}", "WARNING")
        return []
    except requests.exceptions.RequestException as exc:
        log(f"Sayfa cekme hatasi: {exc}", "WARNING")
        return []
    except Exception as exc:
        log(f"Sayfa gorsel toplama hatasi: {exc}", "WARNING")
        return []


def _collect_article_candidates(article: dict, max_candidates: int) -> list[dict]:
    pool: list[dict] = []
    base_url = article.get("link", "")

    list_candidates = article.get("image_candidates", [])
    if isinstance(list_candidates, list):
        for item in list_candidates:
            _append_field_candidates(pool, item, base_url, "article_candidates_field")

    _append_field_candidates(pool, article.get("image_url", ""), base_url, "article_field")
    _append_field_candidates(pool, article.get("rss_image_url", ""), base_url, "rss_field")

    ordered = sorted(pool, key=lambda x: (int(x.get("priority", 99)), x.get("url", "")))
    return ordered[:max_candidates]


def prepare_images(article: dict) -> list[str]:
    settings_config = load_config("settings")
    images_settings = settings_config.get("images", {})

    should_add_logo = bool(images_settings.get("add_logo", True))
    feed_image_width = int(images_settings.get("feed_image_width", 1200))
    feed_image_height = int(images_settings.get("feed_image_height", 630))
    max_candidates_to_try = int(images_settings.get("max_candidates_per_article", 10))
    enable_selected_article_scrape = bool(images_settings.get("enable_article_image_scrape", True))
    env_selected_article_scrape = _read_bool_env("ENABLE_ARTICLE_IMAGE_SCRAPE")
    if env_selected_article_scrape is not None:
        enable_selected_article_scrape = env_selected_article_scrape
    perceptual_threshold = int(
        images_settings.get("perceptual_hash_threshold", _DEFAULT_PERCEPTUAL_HASH_THRESHOLD)
    )
    limits = _get_image_validation_limits()
    resize_limits = _get_platform_resize_limits()
    target_ratio = feed_image_width / feed_image_height

    env_max_images = _read_int_env("MAX_IMAGES_PER_NEWS")
    if env_max_images is not None and env_max_images > 0:
        max_images_per_news = env_max_images
        source = "env"
    else:
        max_images_per_news = int(images_settings.get("max_images_per_news", 1))
        source = "settings"

    if max_images_per_news < 1:
        max_images_per_news = 1

    effective_try_limit = max_candidates_to_try * max(1, min(max_images_per_news, 4))
    effective_try_limit = max(effective_try_limit, max_candidates_to_try)
    effective_try_limit = min(effective_try_limit, 60)

    article_title = article.get("title", "")[:120]
    article_link = article.get("link", "")

    # v6.0: Secilen haber icin scrape HER ZAMAN aktif
    # Eger RSS'ten gorsel gelmemisse, sayfa scrape zorunludur.
    has_rss_image = bool(article.get("image_url", "") or article.get("rss_image_url", "") or article.get("image_candidates", []))
    is_nitter_article = _is_nitter_url(article_link)
    effective_scrape = True  # v6.0: Her zaman aktif - gorselsiz haber paylasilmasin

    log("-" * 40)
    log(f"Gorsel hazirlama basladi: {article_title}")
    log(
        f"Image limits: max_images_per_news={max_images_per_news} ({source}), "
        f"max_candidates_to_try={max_candidates_to_try}, effective_try_limit={effective_try_limit}, "
        f"perceptual_threshold={perceptual_threshold}, "
        f"selected_article_scrape={effective_scrape} (nitter={is_nitter_article})"
    )
    log(
        "Validation limits: "
        f"min_width={limits['min_width']}, min_height={limits['min_height']}, "
        f"min_area={limits['min_area']}, ratio={limits['min_aspect']:.2f}-{limits['max_aspect']:.2f}"
    )
    log(
        "Resize limits: "
        f"max_width={resize_limits['max_width']}, max_height={resize_limits['max_height']}, "
        f"max_area={resize_limits['max_area']}, max_bytes={resize_limits['max_bytes']}"
    )

    prepared_paths: list[str] = []
    used_sources: list[str] = []

    candidate_pool = _collect_article_candidates(article, effective_try_limit)
    if effective_scrape and article.get("can_scrape_image", True) and article_link:
        log(f"Secilen haber icin sayfa gorsel scrape aktif (nitter={is_nitter_article})")
        for c in scrape_article_image_urls(article_link, max_candidates=effective_try_limit):
            _upsert_candidate(candidate_pool, c)
    elif not effective_scrape:
        log("Secilen haber sayfa gorsel scrape kapali", "INFO")

    candidate_pool = sorted(candidate_pool, key=lambda x: (int(x.get("priority", 99)), x.get("url", "")))
    candidate_pool = candidate_pool[:effective_try_limit]
    log(f"Toplam aday URL (canonical): {len(candidate_pool)}")

    tried_keys: set[str] = set()
    seen_content_hashes: set[str] = set()
    seen_perceptual_records: list[tuple[int, str]] = []
    fail_reasons: Counter[str] = Counter()
    tried_count = 0

    accepted: list[dict] = []
    retry_relaxed_pool: list[dict] = []

    for idx, candidate in enumerate(candidate_pool, start=1):
        candidate_url = candidate.get("url", "")
        source_type = candidate.get("source_type", "unknown")
        key = candidate.get("key", "") or _candidate_key(candidate_url)

        if not candidate_url:
            continue
        if key in tried_keys:
            fail_reasons["duplicate_candidate_key"] += 1
            continue

        tried_keys.add(key)
        tried_count += 1

        log(f"Aday deneniyor ({idx}/{len(candidate_pool)}): {candidate_url[:120]} | source={source_type}")

        downloaded, reason = _download_image_with_reason(candidate_url, limits)
        if not downloaded:
            fail_reasons[reason] += 1
            log(f"Aday elendi: {reason}", "WARNING")
            if reason.startswith("too_small:") or reason.startswith("bad_aspect:"):
                retry_relaxed_pool.append(candidate)
            continue

        try:
            width, height, size_kb = _read_image_meta(downloaded)
            content_hash = _file_sha256(downloaded)

            if content_hash in seen_content_hashes:
                fail_reasons["duplicate_image_content"] += 1
                log("Aday elendi: duplicate_image_content", "WARNING")
                _safe_unlink(downloaded)
                continue

            current_signature = _visual_signature(candidate_url)

            try:
                current_phash = _dhash(downloaded)
                is_near_dup = False
                for prev_phash, prev_signature in seen_perceptual_records:
                    dynamic_threshold = _adaptive_perceptual_threshold(
                        perceptual_threshold, current_signature, prev_signature
                    )
                    if _hamming(current_phash, prev_phash) <= dynamic_threshold:
                        is_near_dup = True
                        break

                if is_near_dup:
                    fail_reasons["near_duplicate_perceptual"] += 1
                    log("Aday elendi: near_duplicate_perceptual", "WARNING")
                    _safe_unlink(downloaded)
                    continue
            except Exception as ph_exc:
                fail_reasons["perceptual_hash_error"] += 1
                log(f"Perceptual hash atlandi: {ph_exc}", "WARNING")
                current_phash = None

            processed = downloaded
            needs_resize, resize_reason = _should_resize_for_platform(downloaded, resize_limits)

            if needs_resize:
                log(f"Resize uygulanacak: {resize_reason}")
                processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
            else:
                log(f"Resize atlandi: {resize_reason}")

            if should_add_logo:
                processed = add_logo(processed)

            score, score_detail = _score_image_quality(
                width=width,
                height=height,
                size_kb=size_kb,
                source_type=source_type,
                target_ratio=target_ratio,
            )

            accepted.append(
                {
                    "path": processed,
                    "url": candidate_url,
                    "source_type": source_type,
                    "score": score,
                    "score_detail": score_detail,
                    "phash": current_phash,
                    "signature": current_signature,
                    "content_hash": content_hash,
                }
            )

            seen_content_hashes.add(content_hash)
            if current_phash is not None:
                seen_perceptual_records.append((current_phash, current_signature))

            log(f"Aday basarili: {reason} -> quality={score:.1f} ({score_detail})")

        except Exception as exc:
            fail_reasons[f"processing_error:{exc}"] += 1
            log(f"Aday islenemedi: {exc}", "WARNING")
            _safe_unlink(downloaded)

    if len(accepted) < max_images_per_news and retry_relaxed_pool:
        relaxed_limits = _build_relaxed_limits(limits)
        relaxed_threshold = max(2, perceptual_threshold - 2)

        log(
            "Relaxed pass devrede: "
            f"need={max_images_per_news - len(accepted)}, retry_candidates={len(retry_relaxed_pool)}, "
            f"ratio={relaxed_limits['min_aspect']:.2f}-{relaxed_limits['max_aspect']:.2f}, "
            f"min={relaxed_limits['min_width']}x{relaxed_limits['min_height']}, "
            f"area={relaxed_limits['min_area']}"
        )

        for candidate in retry_relaxed_pool:
            if len(accepted) >= max_images_per_news:
                break

            candidate_url = candidate.get("url", "")
            source_type = candidate.get("source_type", "unknown")
            if not candidate_url:
                continue

            downloaded, reason = _download_image_with_reason(candidate_url, relaxed_limits)
            if not downloaded:
                fail_reasons[f"relaxed_{reason}"] += 1
                continue

            try:
                width, height, size_kb = _read_image_meta(downloaded)
                content_hash = _file_sha256(downloaded)

                if content_hash in seen_content_hashes:
                    fail_reasons["relaxed_duplicate_image_content"] += 1
                    _safe_unlink(downloaded)
                    continue

                current_signature = _visual_signature(candidate_url)

                try:
                    current_phash = _dhash(downloaded)
                    is_near_dup = False
                    for prev_phash, prev_signature in seen_perceptual_records:
                        dynamic_threshold = _adaptive_perceptual_threshold(
                            relaxed_threshold, current_signature, prev_signature
                        )
                        if _hamming(current_phash, prev_phash) <= dynamic_threshold:
                            is_near_dup = True
                            break
                    if is_near_dup:
                        fail_reasons["relaxed_near_duplicate_perceptual"] += 1
                        _safe_unlink(downloaded)
                        continue
                except Exception:
                    current_phash = None

                processed = downloaded
                needs_resize, resize_reason = _should_resize_for_platform(downloaded, resize_limits)

                if needs_resize:
                    log(f"Resize uygulanacak (relaxed): {resize_reason}")
                    processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
                else:
                    log(f"Resize atlandi (relaxed): {resize_reason}")

                if should_add_logo:
                    processed = add_logo(processed)

                score, score_detail = _score_image_quality(
                    width=width,
                    height=height,
                    size_kb=size_kb,
                    source_type=source_type,
                    target_ratio=target_ratio,
                )
                score = max(0.0, score - 7.0)

                accepted.append(
                    {
                        "path": processed,
                        "url": candidate_url,
                        "source_type": source_type,
                        "score": score,
                        "score_detail": f"{score_detail}, relaxed_penalty=7.0",
                        "phash": current_phash,
                        "signature": current_signature,
                        "content_hash": content_hash,
                    }
                )

                seen_content_hashes.add(content_hash)
                if current_phash is not None:
                    seen_perceptual_records.append((current_phash, current_signature))

                log(f"Relaxed aday basarili: {reason} -> quality={score:.1f}")

            except Exception as exc:
                fail_reasons[f"relaxed_processing_error:{exc}"] += 1
                _safe_unlink(downloaded)

    if accepted:
        accepted_sorted = sorted(accepted, key=lambda x: x.get("score", 0.0), reverse=True)
        selected = accepted_sorted[:max_images_per_news]
        discarded = accepted_sorted[max_images_per_news:]

        for item in selected:
            prepared_paths.append(item["path"])
            used_sources.append(item.get("source_type", "unknown"))
            log(
                f"Secilen gorsel: score={item.get('score', 0.0):.1f} "
                f"source={item.get('source_type', 'unknown')} "
                f"url={item.get('url', '')[:110]}"
            )

        for item in discarded:
            path = item.get("path", "")
            if path and os.path.exists(path):
                _safe_unlink(path)

    if not prepared_paths:
        # SON CARE: AI ile gorsel URL'si arama
        ai_url = _ai_search_image_url(article)
        if ai_url:
            log(f"AI gorsel arama: URL bulundu, deneniyor: {ai_url[:80]}...")
            downloaded, reason = _download_image_with_reason(ai_url, limits)
            if downloaded:
                try:
                    processed = downloaded
                    needs_resize, resize_reason = _should_resize_for_platform(downloaded, resize_limits)
                    if needs_resize:
                        log(f"AI gorsel resize: {resize_reason}")
                        processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
                    else:
                        log(f"AI gorsel resize atlandi: {resize_reason}")
                    if should_add_logo:
                        processed = add_logo(processed)
                    prepared_paths.append(processed)
                    used_sources.append("ai_search")
                    article["image_source"] = "ai_search"
                    log(f"AI gorsel basarili! Gorsel hazirlandi.")
                except Exception as exc:
                    log(f"AI gorsel isleme hatasi: {exc}", "WARNING")
                    _safe_unlink(downloaded)
            else:
                log(f"AI gorsel indirilemedi: {reason}", "WARNING")

    if not prepared_paths:
        # v6.0: Logo fallback KALDIRILDI. Gorsel yoksa haber paylasilmaz.
        log("GORSEL YOK: Bu haber icin hicbir gorsel bulunamadi (RSS + scrape + AI hepsi bosa). Haber SKIP edilecek.", "WARNING")
        article["image_source"] = "no_image"
        article["image_sources"] = ["no_image"]
        article["prepared_image_count"] = 0
        article["original_image_urls"] = []
        log(f"Gorsel hazirlama bitti. Adet=0 kaynak=no_image (SKIP)")
        log("-" * 40)
        return []  # Bos liste - publisher bu haberi atlayacak

    article["image_source"] = used_sources[0] if used_sources else "unknown"
    article["image_sources"] = used_sources
    article["prepared_image_count"] = len(prepared_paths)

    # Basariyla indirilen gorsellerin orijinal public URL'lerini kaydet
    # Bu URL'ler Threads paylasiminda kullanilir (upload gerektirmez)
    original_urls: list[str] = []
    if accepted:
        accepted_sorted_for_urls = sorted(accepted, key=lambda x: x.get("score", 0.0), reverse=True)
        for item in accepted_sorted_for_urls[:max_images_per_news]:
            url = item.get("url", "")
            if url and url.startswith("http"):
                original_urls.append(url)
    article["original_image_urls"] = original_urls
    if original_urls:
        log(f"Orijinal URL'ler kaydedildi: {len(original_urls)} adet")

    if fail_reasons:
        fail_summary = ", ".join([f"{k}={v}" for k, v in fail_reasons.items()])
        log(f"Gorsel deneme ozeti: tried={tried_count}, success={len(prepared_paths)}, fails=({fail_summary})")
    else:
        log(f"Gorsel deneme ozeti: tried={tried_count}, success={len(prepared_paths)}, fails=(yok)")

    log(f"Gorsel hazirlama bitti. Adet={len(prepared_paths)} kaynak={article.get('image_source')}")
    log("-" * 40)
    return prepared_paths


def prepare_image(article: dict) -> str:
    paths = prepare_images(article)
    return paths[0]


def run() -> bool:
    log("-" * 55)
    log("agent_image basliyor")
    log("-" * 55)

    write_stage = get_stage("write")
    if write_stage.get("status") != "done":
        log("write asamasi tamamlanmamis, image calistirilamaz", "ERROR")
        set_stage("image", "error", error="write asamasi tamamlanmamis")
        return False

    write_output = write_stage.get("output", {})
    article = write_output.get("article", {})
    post_text = write_output.get("post_text", "")

    if not article:
        log("Write ciktisinda haber yok", "WARNING")
        set_stage("image", "error", error="Write ciktisinda haber yok")
        return False

    set_stage("image", "running")

    try:
        image_paths = prepare_images(article)
        first_image_path = image_paths[0] if image_paths else ""

        output = {
            "article": article,
            "post_text": post_text,
            "image_path": first_image_path,
            "image_paths": image_paths,
            "image_source": article.get("image_source", "unknown"),
            "image_count": len(image_paths),
        }
        set_stage("image", "done", output=output)

        log(
            f"agent_image tamamlandi -> kaynak={article.get('image_source', '?')} "
            f"adet={len(image_paths)}"
        )
        return True

    except Exception as exc:
        log(f"agent_image kritik hata: {exc}", "ERROR")
        set_stage("image", "error", error=str(exc))
        return False


if __name__ == "__main__":
    log("=== agent_image.py modul testi basliyor ===")

    init_pipeline("test-image")

    fake_article = {
        "title": "Test: Yeni Elektrikli SUV Turkiye'de Satisa Cikti",
        "link": "https://nitter.net/murattosunMSE/status/1234567890",
        "summary": "Test ozet metni.",
        "image_url": "",
        "rss_image_url": "",
        "image_candidates": [],
        "source_name": "MuratTosun",
        "source_priority": "medium",
        "can_scrape_image": True,
        "score": 78,
    }
    fake_post_text = (
        "Yeni elektrikli SUV Turkiye'de.\n\n"
        "Test post metni burada yer aliyor.\n\n"
        "#elektrikli #SUV #otomotiv"
    )

    set_stage("fetch", "done", output={"articles": [fake_article], "count": 1})
    set_stage(
        "score",
        "done",
        output={
            "selected_article": fake_article,
            "score": 78,
            "title": fake_article["title"],
        },
    )
    set_stage(
        "write",
        "done",
        output={
            "article": fake_article,
            "post_text": fake_post_text,
            "post_text_length": len(fake_post_text),
        },
    )

    success = run()

    if success:
        image_stage = get_stage("image")
        output = image_stage.get("output", {})

        log("-" * 50)
        log("SONUC:")
        log(f"Haber      : {output.get('article', {}).get('title', 'YOK')[:60]}")
        log(f"Ilk gorsel : {output.get('image_path', 'YOK')}")
        log(f"Gorsel adet: {output.get('image_count', 0)}")
        log(f"Kaynak     : {output.get('image_source', 'YOK')}")
        log(f"Post metni : {len(output.get('post_text', ''))} karakter")
        log("-" * 50)
    else:
        log("Ajan basarisiz oldu", "WARNING")

    log("=== agent_image.py modul testi tamamlandi ===")
