"""
agents/image_utils.py - Görsel Doğrulama, URL İşlemleri ve Puanlama
Limit kontrolü, perceptual hash, duplicate tespiti ve URL normalize işlemleri burada.
"""
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from typing import Any, Optional, Tuple, List, Dict
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse, unquote

import requests
from PIL import Image

from core.logger import log

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

_DEFAULT_PLATFORM_MAX_IMAGE_WIDTH = 4096
_DEFAULT_PLATFORM_MAX_IMAGE_HEIGHT = 4096
_DEFAULT_PLATFORM_MAX_IMAGE_AREA = 16_000_000
_DEFAULT_PLATFORM_MAX_IMAGE_BYTES = 20 * 1024 * 1024

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
_DISALLOWED_IMAGE_EXTENSIONS = (".svg", ".ico")
_NOISE_HINTS = (
    "logo", "icon", "avatar", "sprite", "favicon", "ads", "pixel",
    "author", "profile", "yazar", "cookie", "uygulama-indir",
    "dh-oneriyor", "dh-cookie", "instagram-big", "populer-",
)
_NOISE_PATH_PATTERNS = (
    "/banner/", "/banners/", "/ad-banner", "/images/editor/",
    "/images/images/editor/", "/content/img/", "/profile_images/",
)
_IMAGE_HINT_PATHS = ("/wp-content/uploads/", "/uploads/", "/images/", "/image/", "/img/", "/media/")
_TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
_RESIZE_QUERY_KEYS = {"w", "h", "width", "height", "resize", "fit", "crop", "quality", "q"}

_FALLBACK_BG_COLOR = (18, 25, 44)
_FALLBACK_STRIPE_COLOR = (24, 35, 60)

_SOURCE_PRIORITY = {
    "meta_og": 0, "meta_twitter": 0, "nitter_still": 0, "nitter_card": 1,
    "article_script": 1, "article_img": 1, "article_field": 2,
    "rss_field": 2, "article_candidates_field": 2, "unknown": 3,
}
_SOURCE_SCORE_BONUS = {
    "meta_og": 12.0, "meta_twitter": 10.0, "nitter_still": 14.0,
    "nitter_card": 10.0, "article_script": 8.0, "article_img": 7.0,
    "article_field": 4.0, "rss_field": 3.0, "article_candidates_field": 3.0, "unknown": 0.0,
}

_NITTER_PIC_PATTERN = re.compile(
    r"^/pic/(?:orig/)?(?:media%2F|media/)([A-Za-z0-9_\-]+\.[a-zA-Z]{3,4})",
    re.IGNORECASE,
)
_TWITTER_CDN_HOSTS = {"pbs.twimg.com", "ton.twimg.com", "video.twimg.com"}

def _read_int_env(name: str) -> Optional[int]:
    """Environment variable (ENV) değerini int olarak okur."""
    raw = os.environ.get(name)
    if raw is None: return None
    try: return int(raw.strip())
    except Exception: return None

def _read_float_env(name: str) -> Optional[float]:
    """Environment variable (ENV) değerini float olarak okur."""
    raw = os.environ.get(name)
    if raw is None: return None
    try: return float(raw.strip())
    except Exception: return None

def _read_bool_env(name: str) -> Optional[bool]:
    """Environment variable (ENV) değerini boolean olarak okur."""
    raw = os.environ.get(name)
    if raw is None: return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}: return True
    if value in {"0", "false", "no", "off"}: return False
    return None

def _safe_unlink(path: str) -> None:
    """Belirtilen yoldaki dosyayı güvenli şekilde siler (hata vermez)."""
    try: os.unlink(path)
    except OSError: pass

def _is_test_mode() -> bool:
    """Test modunun aktif olup olmadığını kontrol eder (ENV veya CLI argümanı ile)."""
    return _read_bool_env("IMAGE_TEST_MODE") == True

def _is_nitter_url(url: str) -> bool:
    """Verilen URL'nin bir Nitter instance'ına ait olup olmadığını kontrol eder."""
    host = (urlparse(url).netloc or "").lower()
    return "nitter." in host or host.startswith("nitter")

def _is_profile_image_url(url: str) -> bool:
    """URL'nin bir Twitter/Nitter profil fotosu mu yoksa içerik görseli mi olduğunu kontrol eder."""
    lower = url.lower()
    return "/profile_images/" in lower or "/profile_banners/" in lower

def _resolve_nitter_image_url(raw_url: str, nitter_base: str = "") -> str:
    """Nitter /pic/ formatındaki URL'leri orijinal Twitter CDN (pbs.twimg.com) URL'sine çevirir."""
    if not raw_url: return ""
    parsed_raw = urlparse(raw_url)
    if parsed_raw.netloc in _TWITTER_CDN_HOSTS: return raw_url  
    path = parsed_raw.path if parsed_raw.scheme else raw_url
    m = _NITTER_PIC_PATTERN.match(path)
    if m:
        filename = unquote(m.group(1))
        name_part, _, ext_part = filename.rpartition(".")
        ext_part = ext_part.lower()
        quality = "orig" if "/orig/" in path else "large"
        return f"https://pbs.twimg.com/media/{filename}?format={ext_part}&name={quality}"
    if _is_nitter_url(raw_url) and "/pic/" in raw_url: return raw_url  
    return ""

def _get_image_validation_limits() -> Dict[str, Any]:
    """Görsel doğrulama limitlerini (minumum genişlik, yükseklik, alan, oran) ENV veya varsayılan değerlerden üretir."""
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

def _build_relaxed_limits(limits: Dict[str, Any]) -> Dict[str, Any]:
    """Standart limitlerin altında kalan görseller için gevşetilmiş (relaxed) limitleri üretir."""
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

def _get_platform_resize_limits() -> Dict[str, Any]:
    """Platform (Facebook/Threads) için maksimum görsel boyut limitlerini getirir."""
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

def _should_resize_for_platform(image_path: str, limits: Dict[str, Any]) -> Tuple[bool, str]:
    """Görselin platform limitlerini aşıp aşmadığını kontrol eder, resize gerekiyorsa sebep döndürür."""
    try:
        with Image.open(image_path) as img:
            width, height = img.size
        area = width * height
        file_bytes = os.path.getsize(image_path)
        reasons: List[str] = []
        if width > int(limits["max_width"]): reasons.append(f"width>{limits['max_width']}")
        if height > int(limits["max_height"]): reasons.append(f"height>{limits['max_height']}")
        if area > int(limits["max_area"]): reasons.append(f"area>{limits['max_area']}")
        if file_bytes > int(limits["max_bytes"]): reasons.append(f"bytes>{limits['max_bytes']}")
        if reasons: return True, ",".join(reasons)
        return False, f"within_limits:{width}x{height}:{file_bytes // 1024}KB"
    except Exception as exc:
        return True, f"meta_read_error:{exc}"

def _file_sha256(path: str) -> str:
    """Verilen dosyanın SHA256 hash değerini hesaplar (duplikasyon kontrolü için)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _normalize_url(raw_url: str, base_url: str = "") -> str:
    """Görsel URL'sini temizler (tracking parametrelerini atar, Nitter'i çevirir, göreli URL'leri tamamlar)."""
    if not raw_url: return ""
    url = raw_url.strip()
    if not url: return ""
    if "/pic/" in url:
        resolved = _resolve_nitter_image_url(url, base_url)
        if resolved: return resolved
    if url.startswith("//"): url = f"https:{url}"
    if base_url: url = urljoin(base_url, url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc: return ""
    filtered_qs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in _TRACKING_QUERY_KEYS]
    cleaned = parsed._replace(query=urlencode(filtered_qs), fragment="")
    return urlunparse(cleaned)

def _normalize_path_for_candidate_key(path: str) -> str:
    """Dosya yolunu normalize ederek candidate key oluşturulmasına uygun hale getirir."""
    if not path: return path
    dir_part, _, filename = path.rpartition("/")
    name, dot, ext = filename.partition(".")
    lower_name = name.lower()
    lower_name = re.sub(r"^src_\d{2,4}x\d{2,4}x", "", lower_name, flags=re.IGNORECASE)
    lower_name = re.sub(r"^\d{2,4}x\d{2,4}", "", lower_name, flags=re.IGNORECASE)
    normalized_filename = f"{lower_name}{dot}{ext}" if dot else lower_name
    return f"{dir_part}/{normalized_filename}" if dir_part else normalized_filename

def _looks_like_noise(url: str) -> bool:
    """URL'nin bir gürültü (logo, ikon, banner, profil fotosu vb.) olup olmadığını kontrol eder."""
    lower = url.lower()
    parsed = urlparse(lower)
    path = parsed.path or ""
    host = parsed.netloc or ""
    if host in _TWITTER_CDN_HOSTS:
        if "/profile_images/" in path or "/profile_banners/" in path: return True
        return False
    if _is_nitter_url(lower) and "/pic/" in lower:
        if "profile_images" in lower or "profile_banners" in lower: return True
        return False
    if any(hint in lower for hint in _NOISE_HINTS): return True
    if any(pattern in lower for pattern in _NOISE_PATH_PATTERNS): return True
    return False

def _is_probable_image_url(url: str) -> bool:
    """URL'nin gerçek bir görsel dosyasına (.jpg, .png vb.) işaret edip etmediğini kontrol eder."""
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

def _donanimhaber_variants(url: str) -> List[str]:
    """Donanimhaber sitesine özel görsel boyut formatlarını varyant olarak üretir."""
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

def _thumbnail_to_original_variants(url: str) -> List[str]:
    """Thumbnail (küçük resim) URL'sinden orijinal büyük resim URL varyantlarını türetir."""
    parsed_check = urlparse(url)
    if parsed_check.netloc in _TWITTER_CDN_HOSTS: return [url]
    variants = [url]
    parsed = urlparse(url)
    path = parsed.path or ""
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    wp_thumb_pattern = re.compile(r"-(\d{2,4})x(\d{2,4})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", re.IGNORECASE)
    if wp_thumb_pattern.search(path):
        original_path = wp_thumb_pattern.sub(r"\3", path)
        variants.append(urlunparse(parsed._replace(path=original_path)))
    if query_items:
        filtered_qs = [(k, v) for k, v in query_items if k.lower() not in _RESIZE_QUERY_KEYS]
        if len(filtered_qs) != len(query_items):
            variants.append(urlunparse(parsed._replace(query=urlencode(filtered_qs))))
    filename_cleaned_path = re.sub(r"(?i)([-_](small|thumb|thumbnail|medium|preview))(?=\.)", "", path)
    if filename_cleaned_path != path:
        variants.append(urlunparse(parsed._replace(path=filename_cleaned_path)))
    for v in list(variants):
        for dv in _donanimhaber_variants(v):
            variants.append(dv)
    seen = set(); unique = []
    for item in variants:
        if item and item not in seen:
            seen.add(item); unique.append(item)
    return unique

def _candidate_key(url: str) -> str:
    """URL'yi duplikasyon kontrolü için standart bir anahtara (key) dönüştürür."""
    parsed = urlparse(url)
    path = _normalize_path_for_candidate_key(parsed.path or "")
    path = re.sub(r"-(\d{2,4})x(\d{2,4})(\.(?:jpg|jpeg|png|webp|gif|bmp|avif))$", r"\3", path, flags=re.IGNORECASE)
    filtered_qs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in _RESIZE_QUERY_KEYS]
    return urlunparse(parsed._replace(path=path, query=urlencode(filtered_qs), fragment="")).lower()

def _visual_signature(url: str) -> str:
    """Görsel URL'sinden, görselin görsel imzasını (signature) üretir (perceptual hash eşleştirme için)."""
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
    """Verilen görsel dosyasının dhash (difference hash) değerini hesaplar."""
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
    """İki hash arasındaki Hamming mesafesini (fark bit sayısı) hesaplar."""
    return (a ^ b).bit_count()

def _extract_best_src_from_srcset(srcset: str, page_url: str) -> str:
    """HTML srcset özniteliğinden en yüksek çözünürlüklü görsel URL'sini çıkarır."""
    best_url = ""; best_score = -1.0
    for item in srcset.split(","):
        item = item.strip()
        if not item: continue
        parts = item.split()
        candidate = _normalize_url(parts[0], page_url)
        if not candidate: continue
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
            best_score = score; best_url = candidate
    return best_url

def _walk_json_for_image_urls(node: Any, out: List[str]) -> None:
    """JSON ağacını dolaşarak içindeki görsel URL'lerini toplar."""
    if isinstance(node, dict):
        for key, value in node.items():
            lk = str(key).lower()
            if lk in {"image", "imageurl", "thumbnailurl", "contenturl", "url"}:
                if isinstance(value, str):
                    out.append(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str): out.append(item)
                        elif isinstance(item, dict): _walk_json_for_image_urls(item, out)
                elif isinstance(value, dict):
                    _walk_json_for_image_urls(value, out)
            else:
                _walk_json_for_image_urls(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_json_for_image_urls(item, out)

def _collect_jsonld_images(node: Any, page_url: str, collector: List[str]) -> None:
    """Schema.org JSON-LD verisinden görsel URL'lerini çıkarır ve toplar."""
    if isinstance(node, dict):
        image_value = node.get("image")
        if isinstance(image_value, str):
            normalized = _normalize_url(image_value, page_url)
            if normalized: collector.append(normalized)
        elif isinstance(image_value, list):
            for item in image_value:
                if isinstance(item, str):
                    normalized = _normalize_url(item, page_url)
                    if normalized: collector.append(normalized)
                elif isinstance(item, dict):
                    candidate = item.get("url") or item.get("contentUrl") or ""
                    normalized = _normalize_url(candidate, page_url)
                    if normalized: collector.append(normalized)
        elif isinstance(image_value, dict):
            candidate = image_value.get("url") or image_value.get("contentUrl") or ""
            normalized = _normalize_url(candidate, page_url)
            if normalized: collector.append(normalized)
        for value in node.values():
            _collect_jsonld_images(value, page_url, collector)
    elif isinstance(node, list):
        for item in node:
            _collect_jsonld_images(item, page_url, collector)

def _extract_json_image_urls(script_text: str) -> List[str]:
    """Script etiketi içeriğinden regex ile görsel URL'leri çıkarır."""
    if not script_text or not script_text.strip(): return []
    text = script_text.strip()
    urls = []
    try:
        data = json.loads(text)
        _walk_json_for_image_urls(data, urls)
        return urls
    except Exception:
        pass
    for m in re.finditer(r'https?://[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp|gif|bmp|avif)', text, re.IGNORECASE):
        urls.append(m.group(0))
    return urls

def _upsert_candidate(pool: List[Dict[str, Any]], candidate: Dict[str, Any]) -> None:
    """Aday havuzuna yeni görsel ekler, aynı key varsa önceliğe göre günceller."""
    key = candidate.get("key", "")
    if not key: return
    for idx, existing in enumerate(pool):
        if existing.get("key") != key: continue
        old_prio = int(existing.get("priority", 99))
        new_prio = int(candidate.get("priority", 99))
        if new_prio < old_prio: pool[idx] = candidate
        return
    pool.append(candidate)

def _append_field_candidates(pool: List[Dict[str, Any]], value: str, base_url: str, source_type: str) -> None:
    """Article nesnesi içindeki alanlardan görsel adaylarını havuza ekler."""
    if not isinstance(value, str) or not value.strip(): return
    normalized = _normalize_url(value.strip(), base_url)
    if not normalized: return
    for variant in _thumbnail_to_original_variants(normalized):
        _upsert_candidate(pool, {"url": variant, "key": _candidate_key(variant), "source_type": source_type, "priority": _SOURCE_PRIORITY.get(source_type, 99)})

def _add_scrape_candidate(pool: List[Dict[str, Any]], raw_url: str, page_url: str, source_type: str) -> None:
    """Scrape edilen HTML içerikten bulunan görsel URL'sini havuza işler ve ekler."""
    normalized = _normalize_url(raw_url, page_url)
    if not normalized: return
    for variant in _thumbnail_to_original_variants(normalized):
        lower = variant.lower()
        if _looks_like_noise(lower): continue
        if not _is_probable_image_url(lower): continue
        _upsert_candidate(pool, {"url": variant, "key": _candidate_key(variant), "source_type": source_type, "priority": _SOURCE_PRIORITY.get(source_type, 99)})

def _download_image_with_reason(image_url: str, limits: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Verilen URL'den görseli indirir, limitlere göre doğrular ve geçici dosya yolunu döndürür."""
    if not image_url: return None, "empty_url"
    min_width = int(limits.get("min_width", _DEFAULT_MIN_IMAGE_WIDTH))
    min_height = int(limits.get("min_height", _DEFAULT_MIN_IMAGE_HEIGHT))
    min_area = int(limits.get("min_area", _DEFAULT_MIN_IMAGE_AREA))
    min_aspect = float(limits.get("min_aspect", _DEFAULT_MIN_ASPECT_RATIO))
    max_aspect = float(limits.get("max_aspect", _DEFAULT_MAX_ASPECT_RATIO))
    try:
        response = requests.get(image_url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT, stream=True)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if content_type and not content_type.startswith("image/"):
            return None, f"not_image_content_type:{content_type}"
        temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, prefix="otoxtra_img_")
        temp_path = temp_file.name
        downloaded = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk: continue
            downloaded += len(chunk)
            if downloaded > _MAX_DOWNLOAD_BYTES:
                temp_file.close(); _safe_unlink(temp_path); return None, "download_too_large"
            temp_file.write(chunk)
        temp_file.close()
        try:
            with Image.open(temp_path) as img:
                img_width, img_height = img.size
            area = img_width * img_height
            if img_width < min_width or img_height < min_height or area < min_area:
                _safe_unlink(temp_path); return None, f"too_small:{img_width}x{img_height}:area={area}"
            ratio = img_width / img_height if img_height else 0.0
            if ratio < min_aspect or ratio > max_aspect:
                _safe_unlink(temp_path); return None, f"bad_aspect:{img_width}x{img_height}:ratio={ratio:.3f}"
        except Exception:
            _safe_unlink(temp_path); return None, "invalid_image_file"
        return temp_path, f"ok:{img_width}x{img_height}"
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.RequestException as exc:
        return None, f"http_error:{exc}"
    except Exception as exc:
        return None, f"unexpected_error:{exc}"

def _read_image_meta(path: str) -> Tuple[int, int, int]:
    """Görsel dosyasının genişlik, yükseklik ve dosya boyutunu (KB) okur."""
    with Image.open(path) as img:
        width, height = img.size
    size_kb = max(1, os.path.getsize(path) // 1024)
    return width, height, size_kb

def _score_image_quality(width: int, height: int, size_kb: int, source_type: str, target_ratio: float) -> Tuple[float, str]:
    """Görselin kalitesini (çözünürlük, oran, boyut, kaynak tipi) puanlar."""
    area = width * height
    ratio = width / height if height else 0.0
    resolution_score = min(25.0, (area / 1_200_000.0) * 25.0)
    ratio_diff = abs(ratio - target_ratio)
    aspect_score = max(0.0, 20.0 * (1.0 - min(ratio_diff / 1.2, 1.0)))
    if size_kb < 80: size_score = max(0.0, size_kb / 80.0 * 8.0)
    elif size_kb <= 900: size_score = 8.0
    else: size_score = max(0.0, 8.0 - min((size_kb - 900) / 600.0 * 8.0, 8.0))
    source_bonus = _SOURCE_SCORE_BONUS.get(source_type, 0.0)
    total = 45.0 + resolution_score + aspect_score + size_score + source_bonus
    detail = f"res={resolution_score:.1f}, aspect={aspect_score:.1f}, size={size_score:.1f}, src_bonus={source_bonus:.1f}, ratio={ratio:.3f}, size_kb={size_kb}"
    return total, detail

def _adaptive_perceptual_threshold(base_threshold: int, current_signature: str, previous_signature: str) -> int:
    """Görsel imzalarına göre perceptual hash eşik değerini dinamik olarak ayarlar."""
    if current_signature and previous_signature and current_signature == previous_signature:
        return base_threshold + 3
    return base_threshold

def download_image(image_url: str) -> Optional[str]:
    """Dışarıdan çağrılabilen public fonksiyon: Görseli indirir ve geçici dosya yolunu döndürür."""
    limits = _get_image_validation_limits()
    image_path, reason = _download_image_with_reason(image_url, limits)
    if image_path:
        dims = reason.replace("ok:", "")
        log(f"Gorsel indirildi: {dims}")
        return image_path
    log(f"Gorsel indirilemedi: {reason}", "WARNING")
    return None
