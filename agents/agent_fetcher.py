"""
News fetch and filter agent. (v4.2 - Nitter Image Fix)

v5.0:
  - FIX: _extract_nitter_images_from_tweet_page() Nitter bos donerse otomatik
          olarak orijinal Twitter/x.com URL'sine fallback yapar.
  - YENI: _nitter_to_twitter_url() fonksiyonu - Nitter URL'lerini x.com'a cevirir.
  - FIX: _extract_twitter_og_image() fonksiyonu - x.com tweet sayfasindan
          og:image meta tagini ceker.

v4.2:
  - FIX: _is_probable_image_url() Nitter /pic/orig/media... URL'lerini taniyor.
  - FIX: _looks_like_noise_image() Nitter URL'lerini yanlisl filtreden koruyor.
  - FIX: _extract_image_from_entry() Nitter RSS summary HTML'inden gorsel cekiyor.
  - FIX: Nitter feed'leri icin enable_fetch_article_image_scrape otomatik True.
  - YENI: _is_nitter_url() ve _resolve_nitter_image_url() yardimci fonksiyonlari.
  - YENI: _extract_nitter_images_from_tweet_page() Nitter tweet sayfasindan
          orijinal Twitter CDN URL'lerini dogrudan cekmek icin eklendi.

v4.1:
  - Feed bazli tekrar denemesi guclendirildi (ozellikle nitter kaynaklari icin).
  - Feedler arasi kontrollu bekleme + jitter eklendi.
  - no_entries/parse_error durumlarinda tekrar deneme eklendi.
  - Kaynak saglik loglari attempts ve son hata detayi ile genisletildi.
"""

import json
import os
import random
import re
import sys
import time
from calendar import timegm
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse, unquote

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

from core.config_loader import load_config
from core.helpers import (
    _fingerprint_similarity,
    clean_html,
    generate_topic_fingerprint,
    get_last_check_time,
    get_posted_news,
    get_turkey_now,
    is_already_posted,
    is_duplicate_article,
    is_shared_variant_in_cooldown,
)
from core.logger import log
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
    "cookie",
    "uygulama-indir",
    "dh-oneriyor",
    "dh-cookie",
    "instagram-big",
    "populer-",
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

# Nitter gorsel URL kaliplari
# Ornek: /pic/orig/media%2FABCdef123.jpg
# Ornek: /pic/media%2FABCdef123.jpg
_NITTER_PIC_PATTERN = re.compile(
    r"^/pic/(?:orig/)?(?:media%2F|media/)([A-Za-z0-9_\-]+\.[a-zA-Z]{3,4})",
    re.IGNORECASE,
)
_TWITTER_CDN_HOSTS = {"pbs.twimg.com", "ton.twimg.com", "video.twimg.com"}


def _is_test_mode() -> bool:
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    return "--test" in sys.argv


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int_min(value: Any, default: int, minimum: int) -> int:
    parsed = _safe_int(value, default)
    return parsed if parsed >= minimum else minimum


def _safe_float_min(value: Any, default: float, minimum: float = 0.0) -> float:
    parsed = _safe_float(value, default)
    return parsed if parsed >= minimum else minimum


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return default


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return _coerce_bool(raw, default)


def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return _safe_int(raw, default)


def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return _safe_float(raw, default)


def _turkish_lower(text: str) -> str:
    return text.replace("I", "i").lower()


def _is_nitter_feed(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "nitter." in host or host.startswith("nitter")


def _is_nitter_url(url: str) -> bool:
    """Verilen URL bir Nitter instance'ina mi ait?"""
    host = (urlparse(url).netloc or "").lower()
    return "nitter." in host or host.startswith("nitter")


def _nitter_to_twitter_url(nitter_url: str) -> str:
    """
    Nitter tweet URL'sini orijinal Twitter/x.com URL'sine cevirir.

    Ornekler:
      https://nitter.net/eozpeynirci/status/1234567890  ->  https://x.com/eozpeynirci/status/1234567890
      https://nitter.poast.org/ahmetcelik2016/status/999  ->  https://x.com/ahmetcelik2016/status/999
    """
    if not nitter_url:
        return ""
    parsed = urlparse(nitter_url)
    path = parsed.path or ""
    # /kullanici/status/ID formatini yakala
    m = re.search(r"(/[^/]+/status/\d+)", path)
    if m:
        return f"https://x.com{m.group(1)}"
    return ""


def _is_profile_image_url(url: str) -> bool:
    """v7.0: Profil fotosu URL'lerini tespit eder."""
    lower = url.lower()
    return "/profile_images/" in lower or "/profile_banners/" in lower


def _extract_tweet_images_via_fxtwitter(tweet_url: str, timeout: int = 20) -> list[str]:
    """
    v7.0: FxTwitter API kullanarak tweet gorsel URL'lerini ceker.
    Nitter sayfalari bos dondugunde ve x.com sadece profil fotosu dondugunde
    bu API gercek tweet gorsellerini guvenilir sekilde verir.

    Ornek: https://x.com/eozpeynirci/status/12345 ->
           https://api.fxtwitter.com/eozpeynirci/status/12345
    """
    if not tweet_url:
        return []

    parsed = urlparse(tweet_url)
    path = parsed.path or ""
    if "/status/" not in path:
        return []

    api_url = f"https://api.fxtwitter.com{path}"
    results: list[str] = []

    try:
        response = requests.get(
            api_url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        if data.get("code") != 200:
            return []

        tweet = data.get("tweet", {})
        media = tweet.get("media", {})

        for photo in media.get("photos", []):
            url = photo.get("url", "")
            if url and not _is_profile_image_url(url):
                if "pbs.twimg.com" in url and "name=" not in urlparse(url).query:
                    url = f"{url}?name=orig" if "?" in url else f"{url}?name=orig"
                if url not in results:
                    results.append(url)

        for video in media.get("videos", []):
            thumb = video.get("thumbnail_url", "")
            if thumb and not _is_profile_image_url(thumb) and thumb not in results:
                if "pbs.twimg.com" in thumb and "name=" not in urlparse(thumb).query:
                    thumb = f"{thumb}?name=orig" if "?" in thumb else f"{thumb}?name=orig"
                results.append(thumb)

        if results:
            log(f"FxTwitter API: {len(results)} gorsel bulundu: {tweet_url[:80]}")

    except Exception as exc:
        log(f"FxTwitter API hatasi: {tweet_url[:80]} -> {exc}", "WARNING")

    return results


def _extract_twitter_og_image(tweet_url: str, timeout: int = 20) -> list[str]:
    """
    v7.0: x.com tweet sayfasindan og:image meta tagini ceker.
    PROFIL FOTOGRAFLARI FILTRELENIR - /profile_images/ iceren URL'ler atlanir.
    Bu fonksiyon artik SON CARE fallback olarak kullanilir.
    """
    if not tweet_url:
        return []
    results: list[str] = []
    try:
        response = _request_with_retry(
            tweet_url, timeout=timeout, attempts=2, base_wait_seconds=1.5
        )
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        # og:image meta tag
        for tag in soup.select('meta[property="og:image"]'):
            img_url = tag.get("content", "")
            # v7.0: Profil fotosu filtrele
            if not img_url or _is_profile_image_url(img_url):
                continue
            if "pbs.twimg.com" in img_url:
                parsed = urlparse(img_url)
                if "name=" not in parsed.query:
                    img_url = f"{img_url}&name=orig" if "?" in img_url else f"{img_url}?name=orig"
                results.append(img_url)

        # twitter:image meta tag
        for tag in soup.select('meta[name="twitter:image"]'):
            img_url = tag.get("content", "")
            # v7.0: Profil fotosu filtrele
            if not img_url or _is_profile_image_url(img_url):
                continue
            if "pbs.twimg.com" in img_url and img_url not in results:
                results.append(img_url)

    except Exception as exc:
        log(f"Twitter og:image cekme hatasi: {tweet_url[:80]} -> {exc}", "WARNING")

    return results



def _resolve_nitter_image_url(raw_url: str, nitter_base: str) -> str:
    """
    Nitter'dan gelen gorsel URL'ini orijinal Twitter CDN URL'ine cevirir.

    Desteklenen formatlar:
      /pic/orig/media%2FABCdef.jpg  ->  https://pbs.twimg.com/media/ABCdef.jpg?format=jpg&name=orig
      /pic/media%2FABCdef.jpg       ->  https://pbs.twimg.com/media/ABCdef.jpg?format=jpg&name=large
      https://nitter.net/pic/...    ->  ayni donusum, tam URL olarak

    Eger zaten pbs.twimg.com ise degistirmeden dondurur.
    """
    if not raw_url:
        return ""

    # Zaten Twitter CDN'deyse dokunma
    parsed_raw = urlparse(raw_url)
    if parsed_raw.netloc in _TWITTER_CDN_HOSTS:
        return raw_url

    # Tam URL ise path'i al
    path = parsed_raw.path if parsed_raw.scheme else raw_url

    # /pic/orig/media%2F... veya /pic/media%2F... formatini yakala
    m = _NITTER_PIC_PATTERN.match(path)
    if m:
        filename = m.group(1)  # "ABCdef123.jpg"
        # URL-encoded olabilir, decode et
        filename = unquote(filename)
        name_part, _, ext_part = filename.rpartition(".")
        ext_part = ext_part.lower()

        # "orig" geçen path daha yuksek kalite
        quality = "orig" if "/orig/" in path else "large"

        return (
            f"https://pbs.twimg.com/media/{filename}"
            f"?format={ext_part}&name={quality}"
        )

    # Nitter host'u ile tam URL geldi ama /pic/ formati taninamadi
    # Ornegin: https://nitter.net/pic/enc/... gibi nadir formatlar
    if _is_nitter_url(raw_url) and "/pic/" in raw_url:
        # Orijinal haliyle dene, belki direkt indirilir
        return raw_url

    return ""


def _extract_nitter_images_from_tweet_page(tweet_url: str, timeout: int = 20) -> list[str]:
    """
    Nitter tweet sayfasini ac, icerisindeki tum gorsel URL'lerini topla.
    Oncelik sirasi: still-image > card-image > attachment gorsel

    Dondurduğu liste: orijinal Twitter CDN URL'leri (pbs.twimg.com)
    """
    if not tweet_url or not _is_nitter_url(tweet_url):
        return []

    parsed = urlparse(tweet_url)
    nitter_base = f"{parsed.scheme}://{parsed.netloc}"

    try:
        response = _request_with_retry(tweet_url, timeout=timeout, attempts=3, base_wait_seconds=1.8)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as exc:
        log(f"Nitter tweet sayfasi alinamadi: {tweet_url[:80]} -> {exc}", "WARNING")
        return []

    results: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            results.append(url)

    # 1. <a class="still-image"> href="/pic/orig/media%2F..."
    for a_tag in soup.find_all("a", class_="still-image"):
        href = a_tag.get("href", "")
        resolved = _resolve_nitter_image_url(href, nitter_base)
        if resolved:
            _add(resolved)
        # <img> icindeyse onu da al
        img = a_tag.find("img")
        if img:
            src = img.get("src", "")
            resolved_src = _resolve_nitter_image_url(src, nitter_base)
            if resolved_src:
                _add(resolved_src)

    # 2. <div class="attachment image"> veya <div class="card-image">
    for div in soup.find_all("div", class_=lambda c: c and ("attachment" in c or "card-image" in c)):
        for img in div.find_all("img"):
            src = img.get("src", "")
            resolved = _resolve_nitter_image_url(src, nitter_base)
            if resolved:
                _add(resolved)
        for a_tag in div.find_all("a"):
            href = a_tag.get("href", "")
            resolved = _resolve_nitter_image_url(href, nitter_base)
            if resolved:
                _add(resolved)

    # 3. Tum <img> tagleri - /pic/ ile baslayan src'leri yakala
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "/pic/" in src:
            resolved = _resolve_nitter_image_url(src, nitter_base)
            if resolved:
                _add(resolved)

    # 4. Tum <a> href'leri - /pic/orig/ ile baslayanlari yakala
    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        if "/pic/" in href:
            resolved = _resolve_nitter_image_url(href, nitter_base)
            if resolved:
                _add(resolved)

    log(f"Nitter tweet sayfasindan {len(results)} gorsel bulundu: {tweet_url[:80]}")

    # v7.0: Nitter bos donerse FxTwitter API'ye fallback yap (ONCELIKLI)
    # Nitter sayfalari 2025 ortasindan itibaren bos donmeye basladi
    # (HTTP 200, content-length: 0). FxTwitter API guvenilir alternatiftir.
    if not results:
        twitter_url = _nitter_to_twitter_url(tweet_url)
        if twitter_url:
            # 1. FxTwitter API (en guvenilir)
            log(f"Nitter bos, FxTwitter API deneniyor: {twitter_url[:80]}")
            fxtwitter_images = _extract_tweet_images_via_fxtwitter(twitter_url, timeout=timeout)
            if fxtwitter_images:
                log(f"FxTwitter API'den {len(fxtwitter_images)} gorsel bulundu")
                results.extend(fxtwitter_images)
            else:
                # 2. x.com HTML scrape (son care, profil fotosu filtreli)
                log(f"FxTwitter bosa dustu, x.com HTML scrape deneniyor: {twitter_url[:80]}")
                twitter_images = _extract_twitter_og_image(twitter_url, timeout=timeout)
                if twitter_images:
                    log(f"x.com scrape'tan {len(twitter_images)} gorsel bulundu (profil fotosu filtrelendi)")
                    results.extend(twitter_images)

    return results


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
    """
    RSS entry'den gorsel URL'i cikar.
    v4.2: Nitter RSS summary HTML'i icindeki /pic/orig/... URL'leri de tanir.
    """
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
                if not img_src:
                    continue

                # v4.2: Nitter /pic/ URL'lerini orijinal CDN'e cevir
                if "/pic/" in img_src:
                    resolved = _resolve_nitter_image_url(img_src, "")
                    if resolved:
                        return resolved

                if img_src.startswith("http"):
                    return img_src

            # v4.2: <a href="/pic/orig/..."> linkleri de dene (Nitter RSS'te gorulur)
            for a_tag in soup.find_all("a", href=True):
                href = a_tag.get("href", "")
                if "/pic/" in href:
                    resolved = _resolve_nitter_image_url(href, "")
                    if resolved:
                        return resolved

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

    # v4.2: Nitter /pic/ URL'lerini burada da coz
    if "/pic/" in candidate:
        resolved = _resolve_nitter_image_url(candidate, page_url)
        if resolved:
            return resolved

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
        if k.lower() not in _TRACKING_QUERY_KEYS
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
    """
    v7.0: pbs.twimg.com/media/ URL'leri asla gurultu degil.
    Nitter /pic/ URL'leri de gercek gorsel.
    PROFIL FOTOLARI gurultu olarak isaretlenir.
    """
    lower_url = url.lower()
    parsed = urlparse(lower_url)
    path = parsed.path or ""
    host = parsed.netloc or ""

    # Twitter CDN: /media/ gercek gorsel, /profile_images/ gurultu
    if host in _TWITTER_CDN_HOSTS:
        if "/profile_images/" in path or "/profile_banners/" in path:
            return True
        return False

    # Nitter /pic/ - profil fotosu ise gurultu
    if _is_nitter_url(lower_url) and "/pic/" in lower_url:
        if "profile_images" in lower_url or "profile_banners" in lower_url:
            return True
        return False

    if any(hint in lower_url for hint in _IMAGE_NOISE_HINTS):
        return True
    if "/content/img/" in path:
        return True
    return False


def _is_probable_image_url(url: str) -> bool:
    """
    v4.2: Nitter /pic/ ve pbs.twimg.com/media/ URL'leri taniyor.
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

    for item in list(variants):
        variants.extend(_donanimhaber_variants(item))

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


def _walk_json_for_image_urls(node: Any, collector: list[str], page_url: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            lk = str(key).lower()
            if lk in {"image", "imageurl", "thumbnailurl", "contenturl", "url"}:
                if isinstance(value, str):
                    normalized = _normalize_image_url(value, page_url)
                    if normalized:
                        collector.append(normalized)
                elif isinstance(value, list):
                    for item in value:
                        _walk_json_for_image_urls(item, collector, page_url)
                elif isinstance(value, dict):
                    _walk_json_for_image_urls(value, collector, page_url)
            else:
                _walk_json_for_image_urls(value, collector, page_url)
    elif isinstance(node, list):
        for item in node:
            _walk_json_for_image_urls(item, collector, page_url)
    elif isinstance(node, str):
        normalized = _normalize_image_url(node, page_url)
        if normalized and _is_probable_image_url(normalized.lower()):
            collector.append(normalized)


def _extract_script_image_urls(script_text: str, page_url: str) -> list[str]:
    out: list[str] = []
    if not script_text or not script_text.strip():
        return out

    text = script_text.strip()
    try:
        parsed = json.loads(text)
        _walk_json_for_image_urls(parsed, out, page_url)
    except Exception:
        pass

    for m in re.finditer(
        r'https?://[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp|gif|bmp|avif)',
        text,
        flags=re.IGNORECASE,
    ):
        normalized = _normalize_image_url(m.group(0), page_url)
        if normalized:
            out.append(normalized)

    return out


def extract_images_from_article(url: str, max_candidates: int = 8) -> list[str]:
    """
    v4.2: Nitter tweet URL'i gelirse _extract_nitter_images_from_tweet_page()
    cagrilir, normal siteler icin eski davranis korunur.
    """
    if not url:
        return []

    max_candidates = _safe_int_min(max_candidates, 8, 1)

    # v4.2: Nitter URL'si ise ozel fonksiyon kullan
    if _is_nitter_url(url):
        nitter_imgs = _extract_nitter_images_from_tweet_page(url)
        return nitter_imgs[:max_candidates]

    try:
        response = _request_with_retry(url, timeout=15, attempts=2, base_wait_seconds=1.0)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        raw_candidates: list[str] = []
        collect_limit = max(max_candidates * 3, 18)

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

        for script in soup.find_all("script"):
            script_text = (script.string or script.get_text() or "").strip()
            if not script_text:
                continue
            for item in _extract_script_image_urls(script_text, url):
                raw_candidates.extend(_thumbnail_to_original_variants(item))

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

        for source in soup.find_all("source"):
            srcset = source.get("srcset", "") or source.get("data-srcset", "")
            if srcset:
                best_srcset = _extract_best_src_from_srcset(srcset, url)
                if best_srcset:
                    raw_candidates.extend(_thumbnail_to_original_variants(best_srcset))

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
            if len(unique_candidates) >= collect_limit:
                break

        unique_candidates = unique_candidates[:max_candidates]
        log(f"extract_images_from_article: raw={raw_count}, canonical={len(unique_candidates)}, url={url[:120]}")
        return unique_candidates

    except Exception as exc:
        log(f"extract_images_from_article warning: {exc}", "WARNING")
        return []


def _feed_delay_config() -> tuple[float, float]:
    settings_cfg = load_config("settings")
    posting_cfg = settings_cfg.get("posting", {}) if isinstance(settings_cfg, dict) else {}

    base_delay = _safe_float_min(
        _read_float_env("FEED_FETCH_DELAY_SECONDS", _safe_float(posting_cfg.get("feed_fetch_delay_seconds", 0.35), 0.35)),
        0.0,
    )
    jitter = _safe_float_min(
        _read_float_env("FEED_FETCH_DELAY_JITTER_SECONDS", _safe_float(posting_cfg.get("feed_fetch_delay_jitter_seconds", 0.4), 0.4)),
        0.0,
    )
    return base_delay, jitter


def _feed_attempt_config(feed_url: str) -> tuple[int, int, float, int]:
    settings_cfg = load_config("settings")
    posting_cfg = settings_cfg.get("posting", {}) if isinstance(settings_cfg, dict) else {}

    is_nitter = _is_nitter_feed(feed_url)

    if is_nitter:
        fetch_attempts = _safe_int_min(
            _read_int_env("NITTER_FEED_FETCH_ATTEMPTS", _safe_int(posting_cfg.get("nitter_feed_fetch_attempts", 3), 3)),
            1,
            1,
        )
        http_attempts = _safe_int_min(
            _read_int_env("NITTER_HTTP_ATTEMPTS", _safe_int(posting_cfg.get("nitter_http_attempts", 3), 3)),
            1,
            1,
        )
        base_wait = _safe_float_min(
            _read_float_env("NITTER_HTTP_BASE_WAIT_SECONDS", _safe_float(posting_cfg.get("nitter_http_base_wait_seconds", 1.8), 1.8)),
            0.1,
        )
        timeout = _safe_int_min(
            _read_int_env("NITTER_HTTP_TIMEOUT_SECONDS", _safe_int(posting_cfg.get("nitter_http_timeout_seconds", 22), 22)),
            5,
            1,
        )
        return fetch_attempts, http_attempts, base_wait, timeout

    fetch_attempts = _safe_int_min(
        _read_int_env("FEED_FETCH_ATTEMPTS", _safe_int(posting_cfg.get("feed_fetch_attempts", 1), 1)),
        1,
        1,
    )
    http_attempts = _safe_int_min(
        _read_int_env("FEED_HTTP_ATTEMPTS", _safe_int(posting_cfg.get("feed_http_attempts", 3), 3)),
        1,
        1,
    )
    base_wait = _safe_float_min(
        _read_float_env("FEED_HTTP_BASE_WAIT_SECONDS", _safe_float(posting_cfg.get("feed_http_base_wait_seconds", 1.5), 1.5)),
        0.1,
    )
    timeout = _safe_int_min(
        _read_int_env("FEED_HTTP_TIMEOUT_SECONDS", _safe_int(posting_cfg.get("feed_http_timeout_seconds", 20), 20)),
        5,
        1,
    )
    return fetch_attempts, http_attempts, base_wait, timeout


def _sleep_between_feeds(feed_name: str, base_delay: float, jitter: float) -> None:
    if _is_test_mode():
        return
    total_sleep = base_delay + (random.uniform(0, jitter) if jitter > 0 else 0.0)
    if total_sleep <= 0:
        return
    log(f"Feed delay: {feed_name} icin {total_sleep:.2f}s bekleniyor")
    time.sleep(total_sleep)


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
    news_cfg = settings_cfg.get("news", {}) if isinstance(settings_cfg, dict) else {}

    max_articles_per_source = _safe_int_min(news_cfg.get("max_articles_per_source", 25), 25, 1)

    enable_fetch_article_image_scrape = _coerce_bool(
        images_cfg.get("enable_fetch_article_image_scrape", False), False
    )
    enable_fetch_article_image_scrape = _read_bool_env(
        "ENABLE_FETCH_ARTICLE_IMAGE_SCRAPE", enable_fetch_article_image_scrape
    )

    max_candidates_per_article = _safe_int_min(images_cfg.get("max_candidates_per_article", 8), 8, 1)
    max_article_scrapes_per_feed = _safe_int_min(images_cfg.get("max_article_scrapes_per_feed", 6), 6, 0)

    delay_base, delay_jitter = _feed_delay_config()

    log(
        f"fetch image config: deferred_selected_article_scrape={not enable_fetch_article_image_scrape}, "
        f"enable_fetch_article_image_scrape={enable_fetch_article_image_scrape}, "
        f"max_candidates_per_article={max_candidates_per_article}, "
        f"max_article_scrapes_per_feed={max_article_scrapes_per_feed}, "
        f"max_articles_per_source={max_articles_per_source}"
    )

    for feed_idx, feed_info in enumerate(feeds):
        feed_url = str(feed_info.get("url", "") or "").strip()
        feed_name = str(feed_info.get("name", "Unknown") or "Unknown").strip()
        feed_priority = str(feed_info.get("priority", "medium") or "medium").strip().lower()
        feed_language = str(feed_info.get("language", "tr") or "tr").strip().lower()
        feed_can_scrape = _coerce_bool(feed_info.get("can_scrape_image", True), True)
        feed_enabled = _coerce_bool(feed_info.get("enabled", True), True)

        if not feed_enabled:
            source_health[feed_name] = {"status": "disabled", "count": 0, "detail": "disabled", "attempts": 0}
            continue
        if not feed_url:
            source_health[feed_name] = {"status": "error", "count": 0, "detail": "empty_url", "attempts": 0}
            continue

        fetch_attempts, http_attempts, http_base_wait, timeout = _feed_attempt_config(feed_url)

        # v9.0 HIZLANDIRMA: Fetch asamasinda makale scrape tamamen devre disi birakildi.
        # Nitter dahil tum gorsel tarama islemleri agent_image.py tarafindan secilen 1 haber icin yapilacak.
        is_nitter_source = _is_nitter_feed(feed_url)
        effective_scrape = False
        # if is_nitter_source and not enable_fetch_article_image_scrape:
        #     log(f"{feed_name}: Nitter kaynak, gorsel scrape otomatik aktif")

        if feed_idx > 0:
            _sleep_between_feeds(feed_name, delay_base, delay_jitter)

        last_error_detail = ""
        final_entry_count = 0
        success = False
        attempt_used = 0

        for feed_attempt in range(1, fetch_attempts + 1):
            attempt_used = feed_attempt
            try:
                response = _request_with_retry(
                    feed_url,
                    timeout=timeout,
                    attempts=http_attempts,
                    base_wait_seconds=http_base_wait,
                )

                parsed_feed = feedparser.parse(response.content)
                if parsed_feed.bozo and not parsed_feed.entries:
                    bozo_msg = str(getattr(parsed_feed, "bozo_exception", "parse error"))[:120]
                    last_error_detail = f"parse_error: {bozo_msg}"
                    log(
                        f"{feed_name}: parse bozuk ({feed_attempt}/{fetch_attempts}) -> {bozo_msg}",
                        "WARNING",
                    )
                    if feed_attempt < fetch_attempts:
                        time.sleep(min(2.5, 0.8 * feed_attempt))
                        continue
                    break

                if not parsed_feed.entries:
                    last_error_detail = "no_entries"
                    log(
                        f"{feed_name}: no_entries ({feed_attempt}/{fetch_attempts})",
                        "WARNING" if feed_attempt < fetch_attempts else "INFO",
                    )
                    if feed_attempt < fetch_attempts:
                        time.sleep(min(2.0, 0.6 * feed_attempt))
                        continue
                    break

                entry_count = 0
                scraped_in_feed = 0

                for entry in parsed_feed.entries:
                    if entry_count >= max_articles_per_source:
                        break

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

                    # v9.0 HIZLANDIRMA: Bu blok kapali. agent_image.py zaten secilen haberin gorselini en iyi sekilde cekiyor.
                    # if feed_can_scrape and effective_scrape and scraped_in_feed < max_article_scrapes_per_feed:
                    #     article_image_candidates = extract_images_from_article(
                    #         link,
                    #         max_candidates=max_candidates_per_article,
                    #     )
                    #     scraped_in_feed += 1

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

                    primary_image = article_image_candidates[0] if article_image_candidates else (normalized_rss_image or rss_image_url)

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

                final_entry_count = entry_count
                success = True
                break

            except requests.exceptions.Timeout:
                last_error_detail = "timeout"
                log(f"{feed_name}: timeout ({feed_attempt}/{fetch_attempts})", "WARNING")
            except requests.exceptions.ConnectionError:
                last_error_detail = "connection_error"
                log(f"{feed_name}: connection_error ({feed_attempt}/{fetch_attempts})", "WARNING")
            except requests.exceptions.HTTPError as exc:
                last_error_detail = f"http_error: {str(exc)[:80]}"
                log(f"{feed_name}: http_error ({feed_attempt}/{fetch_attempts}) -> {exc}", "WARNING")
            except Exception as exc:
                last_error_detail = f"unknown_error: {str(exc)[:120]}"
                log(f"{feed_name}: unknown_error ({feed_attempt}/{fetch_attempts}) -> {exc}", "WARNING")

            if feed_attempt < fetch_attempts:
                pause = min(3.5, 0.9 * feed_attempt)
                time.sleep(pause)

        if success:
            source_health[feed_name] = {
                "status": "ok",
                "count": final_entry_count,
                "detail": "",
                "attempts": attempt_used,
            }
        else:
            detail = "Feed has no entries" if last_error_detail == "no_entries" else (last_error_detail or "fetch_failed")
            status = "no_entries" if last_error_detail == "no_entries" else "error"
            source_health[feed_name] = {
                "status": status,
                "count": 0,
                "detail": detail,
                "attempts": attempt_used,
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


def _apply_time_filter_with_hours(
    articles: list[dict],
    max_age_hours: int,
    use_smart_cutoff: bool,
) -> tuple[list[dict], datetime]:
    max_age_hours = max(1, _safe_int(max_age_hours, 12))

    now_utc = datetime.now(timezone.utc)
    max_cutoff_utc = now_utc - timedelta(hours=max_age_hours)

    if _is_test_mode() or not use_smart_cutoff:
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
            passed.append(article)
            continue
        passed.append(article)

    return passed, cutoff_utc


def apply_time_filter(articles: list[dict]) -> list[dict]:
    settings = load_config("settings")
    news_cfg = settings.get("news", {}) if isinstance(settings, dict) else {}
    max_age_hours = _read_int_env(
        "NEWS_MAX_AGE_HOURS",
        _safe_int(news_cfg.get("max_article_age_hours", 24), 24),
    )

    passed, cutoff_utc = _apply_time_filter_with_hours(
        articles=articles,
        max_age_hours=max_age_hours,
        use_smart_cutoff=True,
    )
    log(f"time_filter: in={len(articles)} out={len(passed)} cutoff={cutoff_utc.isoformat()}")
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


def apply_shared_variant_cooldown_filter(articles: list[dict]) -> list[dict]:
    settings = load_config("settings")
    news_cfg = settings.get("news", {}) if isinstance(settings, dict) else {}
    cooldown_hours = _read_int_env(
        "SHARED_VARIANT_COOLDOWN_HOURS",
        _safe_int(news_cfg.get("shared_variant_cooldown_hours", 3), 3),
    )
    if cooldown_hours <= 0:
        return articles

    posted_data = get_posted_news()
    passed = [
        article
        for article in articles
        if not is_shared_variant_in_cooldown(
            article.get("link", ""), article.get("title", ""), posted_data, cooldown_hours
        )
    ]
    log(f"pipeline.shared_variant_cooldown: {len(articles)} -> {len(passed)} (hours={cooldown_hours})")
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
    metrics = {"found": len(articles), "after_keyword": 0, "after_time": 0, "after_posted": 0, "after_duplicate": 0}
    if not articles:
        source_health["_metrics"] = metrics
        return [], source_health
    log(f"pipeline.fetch: {len(articles)}")

    articles = apply_keyword_filter(articles)
    metrics["after_keyword"] = len(articles)
    if not articles:
        log("pipeline.keyword: 0")
        source_health["_metrics"] = metrics
        return [], source_health
    log(f"pipeline.keyword: {len(articles)}")

    original_after_keyword = list(articles)
    articles = apply_time_filter(articles)
    if not articles:
        settings = load_config("settings")
        news_cfg = settings.get("news", {}) if isinstance(settings, dict) else {}
        base_hours = _read_int_env(
            "NEWS_MAX_AGE_HOURS",
            _safe_int(news_cfg.get("max_article_age_hours", 24), 24),
        )
        relaxed_hours = max(base_hours, 36)

        relaxed, relaxed_cutoff = _apply_time_filter_with_hours(
            articles=original_after_keyword,
            max_age_hours=relaxed_hours,
            use_smart_cutoff=False,
        )
        if relaxed:
            log(
                f"time_filter fallback aktif: {len(original_after_keyword)} -> {len(relaxed)} "
                f"(hours={relaxed_hours}, cutoff={relaxed_cutoff.isoformat()})",
                "WARNING",
            )
            articles = relaxed
        else:
            log("pipeline.time: 0 (fallback da bos)")
            source_health["_metrics"] = metrics
            return [], source_health
    metrics["after_time"] = len(articles)
    log(f"pipeline.time: {len(articles)}")

    if not _is_test_mode():
        before_posted = len(articles)
        articles = remove_already_posted(articles)
        log(f"pipeline.posted: {before_posted} -> {len(articles)}")
        before_cooldown = len(articles)
        articles = apply_shared_variant_cooldown_filter(articles)
        metrics["after_posted"] = len(articles)
        metrics["after_shared_variant_cooldown"] = len(articles)
        if not articles:
            source_health["_metrics"] = metrics
            log(f"pipeline.shared_variant_cooldown exhausted candidates: {before_cooldown} -> 0")
            return [], source_health

    before_dup = len(articles)
    articles = remove_duplicates(articles)
    metrics["after_duplicate"] = len(articles)
    log(f"pipeline.duplicate: {before_dup} -> {len(articles)}")

    articles = _detect_trends(articles)
    source_health["_metrics"] = metrics
    return articles, source_health


def run() -> bool:
    set_stage("fetch", "running")
    try:
        articles, source_health = fetch_and_filter_news()
        if not articles:
            set_stage(
                "fetch",
                "error",
                output={
                    "articles": [],
                    "count": 0,
                    "source_health": source_health,
                    "metrics": source_health.get("_metrics", {}),
                },
                error="No article found",
            )
            return False

        set_stage(
            "fetch",
            "done",
            output={
                "articles": articles,
                "count": len(articles),
                "source_health": source_health,
                "metrics": source_health.get("_metrics", {}),
            },
        )
        return True
    except Exception as exc:
        set_stage("fetch", "error", error=str(exc))
        return False


if __name__ == "__main__":
    run()
