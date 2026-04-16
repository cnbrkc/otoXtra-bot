"""
agents/agent_image.py - Gorsel Isleme Ajani (v4.8)

Degisiklikler:
- Boyut filtresi sertlestirildi (default: min 900x500 + min area 450000).
- En-boy oran filtresi eklendi (default: 0.8 <= ratio <= 2.1).
- Kaynak onceliklendirme eklendi:
  og/twitter meta > article img > article/rss field.
- Gorsel kalite puanlama eklendi:
  cozumurluk + oran uygunlugu + dosya boyutu + kaynak bonusu.
- Dinamik duplicate esigi eklendi:
  ayni seri varyantlarinda perceptual duplicate daha agresif.
- Env override:
  IMAGE_MIN_WIDTH, IMAGE_MIN_HEIGHT, IMAGE_MIN_AREA,
  IMAGE_MIN_ASPECT_RATIO, IMAGE_MAX_ASPECT_RATIO
"""

import hashlib
import os
import re
import sys
import tempfile
from collections import Counter
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from PIL import Image, ImageDraw
from bs4 import BeautifulSoup

from core.logger import log
from core.config_loader import load_config, get_project_root
from core.state_manager import get_stage, set_stage, init_pipeline


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_REQUEST_TIMEOUT = 15
_MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024

# Default image validation rules
_DEFAULT_MIN_IMAGE_WIDTH = 900
_DEFAULT_MIN_IMAGE_HEIGHT = 500
_DEFAULT_MIN_IMAGE_AREA = 450000
_DEFAULT_MIN_ASPECT_RATIO = 0.8
_DEFAULT_MAX_ASPECT_RATIO = 2.1

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
)
_IMAGE_HINT_PATHS = ("/wp-content/uploads/", "/uploads/", "/images/", "/image/", "/img/", "/media/")
_TRACKING_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
_RESIZE_QUERY_KEYS = {"w", "h", "width", "height", "resize", "fit", "crop", "quality", "q"}

_DEFAULT_PERCEPTUAL_HASH_THRESHOLD = 6

# Source priority: smaller is better
_SOURCE_PRIORITY = {
    "meta_og": 0,
    "meta_twitter": 0,
    "article_img": 1,
    "article_field": 2,
    "rss_field": 2,
    "article_candidates_field": 2,
    "unknown": 3,
}

_SOURCE_SCORE_BONUS = {
    "meta_og": 12.0,
    "meta_twitter": 10.0,
    "article_img": 7.0,
    "article_field": 4.0,
    "rss_field": 3.0,
    "article_candidates_field": 3.0,
    "unknown": 0.0,
}


def _is_test_mode() -> bool:
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    if "--test" in sys.argv:
        return True
    return False


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
    lower = url.lower()
    return any(hint in lower for hint in _NOISE_HINTS)


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

    # s1/s2/s3 gibi varyantlari normalle
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
        row = pixels[y * 9:(y + 1) * 9]
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


def _unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if not item:
            continue
        key = _candidate_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


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
            suffix=".jpg", delete=False, prefix="otoxtra_img_"
        )
        temp_path = temp_file.name

        downloaded = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            downloaded += len(chunk)
            if downloaded > _MAX_DOWNLOAD_BYTES:
                temp_file.close()
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                return None, "download_too_large"
            temp_file.write(chunk)
        temp_file.close()

        try:
            img = Image.open(temp_path)
            img_width, img_height = img.size
            img.close()

            area = img_width * img_height
            if img_width < min_width or img_height < min_height or area < min_area:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                return None, f"too_small:{img_width}x{img_height}:area={area}"

            ratio = img_width / img_height if img_height else 0.0
            if ratio < min_aspect or ratio > max_aspect:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                return None, f"bad_aspect:{img_width}x{img_height}:ratio={ratio:.3f}"

        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
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

    # Resolution score (0..25)
    resolution_score = min(25.0, (area / 1_200_000.0) * 25.0)

    # Aspect score (0..20) -> target ratioya ne kadar yakin
    ratio_diff = abs(ratio - target_ratio)
    aspect_score = max(0.0, 20.0 * (1.0 - min(ratio_diff / 1.2, 1.0)))

    # Size score (0..8): cok kucuk/cok buyuk olmayan dosyalari odullendir
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
    if not url:
        return []

    try:
        log(f"Sayfadan gorsel adaylari araniyor: {url[:100]}")

        response = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        pool: list[dict] = []

        # 1) Meta kaynaklari (en yuksek oncelik)
        meta_selectors = [
            ('meta[property="og:image"]', "content", "meta_og"),
            ('meta[property="og:image:url"]', "content", "meta_og"),
            ('meta[name="twitter:image"]', "content", "meta_twitter"),
            ('meta[name="twitter:image:src"]', "content", "meta_twitter"),
        ]
        for selector, attr, source_type in meta_selectors:
            for tag in soup.select(selector):
                normalized = _normalize_url(tag.get(attr, ""), url)
                if not normalized:
                    continue
                for variant in _thumbnail_to_original_variants(normalized):
                    if not variant:
                        continue
                    lower = variant.lower()
                    if _looks_like_noise(lower):
                        continue
                    if not _is_probable_image_url(lower):
                        continue
                    candidate = {
                        "url": variant,
                        "key": _candidate_key(variant),
                        "source_type": source_type,
                        "priority": _SOURCE_PRIORITY.get(source_type, 99),
                    }
                    _upsert_candidate(pool, candidate)

        # 2) Sayfa img taglari
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
                normalized = _normalize_url(src, url)
                if not normalized:
                    continue
                for variant in _thumbnail_to_original_variants(normalized):
                    if not variant:
                        continue
                    lower = variant.lower()
                    if _looks_like_noise(lower):
                        continue
                    if not _is_probable_image_url(lower):
                        continue
                    candidate = {
                        "url": variant,
                        "key": _candidate_key(variant),
                        "source_type": "article_img",
                        "priority": _SOURCE_PRIORITY.get("article_img", 99),
                    }
                    _upsert_candidate(pool, candidate)

        ordered = sorted(pool, key=lambda x: (int(x.get("priority", 99)), x.get("url", "")))
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


def _create_fallback_image(width: int, height: int) -> str:
    project_root = get_project_root()
    logo_candidates = [
        os.path.join(project_root, "assets", "logo_solid.png"),
        os.path.join(project_root, "assets", "logo_solid.jpg"),
        os.path.join(project_root, "assets", "logo.png"),
    ]
    logo_path = None
    for candidate in logo_candidates:
        if os.path.exists(candidate):
            logo_path = candidate
            break

    try:
        temp_file = tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False, prefix="otoxtra_fallback_"
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
            [stripe_padding_x, height - stripe_margin - stripe_thickness, width - stripe_padding_x, height - stripe_margin],
            fill=_FALLBACK_STRIPE_COLOR,
        )

        if logo_path:
            try:
                logo_img = Image.open(logo_path)
                if logo_img.mode != "RGBA":
                    logo_img = logo_img.convert("RGBA")

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
            suffix=".jpg", delete=False, prefix="otoxtra_emergency_"
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
    logo_size_percent = images_settings.get("logo_size_percent", 15)
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


def _collect_article_candidates(article: dict, max_candidates: int) -> list[dict]:
    pool: list[dict] = []
    base_url = article.get("link", "")

    list_candidates = article.get("image_candidates", [])
    if isinstance(list_candidates, list):
        for item in list_candidates:
            if not isinstance(item, str):
                continue
            normalized = _normalize_url(item, base_url)
            if not normalized:
                continue
            for variant in _thumbnail_to_original_variants(normalized):
                candidate = {
                    "url": variant,
                    "key": _candidate_key(variant),
                    "source_type": "article_candidates_field",
                    "priority": _SOURCE_PRIORITY.get("article_candidates_field", 99),
                }
                _upsert_candidate(pool, candidate)

    value = article.get("image_url", "")
    if isinstance(value, str) and value.strip():
        normalized = _normalize_url(value.strip(), base_url)
        if normalized:
            for variant in _thumbnail_to_original_variants(normalized):
                candidate = {
                    "url": variant,
                    "key": _candidate_key(variant),
                    "source_type": "article_field",
                    "priority": _SOURCE_PRIORITY.get("article_field", 99),
                }
                _upsert_candidate(pool, candidate)

    value = article.get("rss_image_url", "")
    if isinstance(value, str) and value.strip():
        normalized = _normalize_url(value.strip(), base_url)
        if normalized:
            for variant in _thumbnail_to_original_variants(normalized):
                candidate = {
                    "url": variant,
                    "key": _candidate_key(variant),
                    "source_type": "rss_field",
                    "priority": _SOURCE_PRIORITY.get("rss_field", 99),
                }
                _upsert_candidate(pool, candidate)

    ordered = sorted(pool, key=lambda x: (int(x.get("priority", 99)), x.get("url", "")))
    return ordered[:max_candidates]


def _adaptive_perceptual_threshold(
    base_threshold: int,
    current_signature: str,
    previous_signature: str,
) -> int:
    # Ayni seri varyantlarinda daha agresif duplicate eleme
    if current_signature and previous_signature and current_signature == previous_signature:
        return base_threshold + 3
    return base_threshold


def prepare_images(article: dict) -> list[str]:
    settings_config = load_config("settings")
    images_settings = settings_config.get("images", {})

    should_add_logo = bool(images_settings.get("add_logo", True))
    feed_image_width = int(images_settings.get("feed_image_width", 1200))
    feed_image_height = int(images_settings.get("feed_image_height", 630))
    max_candidates_to_try = int(images_settings.get("max_candidates_per_article", 10))
    perceptual_threshold = int(
        images_settings.get("perceptual_hash_threshold", _DEFAULT_PERCEPTUAL_HASH_THRESHOLD)
    )
    limits = _get_image_validation_limits()
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

    article_title = article.get("title", "")[:120]
    log("-" * 40)
    log(f"Gorsel hazirlama basladi: {article_title}")
    log(
        f"Image limits: max_images_per_news={max_images_per_news} ({source}), "
        f"max_candidates_to_try={max_candidates_to_try}, perceptual_threshold={perceptual_threshold}"
    )
    log(
        "Validation limits: "
        f"min_width={limits['min_width']}, min_height={limits['min_height']}, "
        f"min_area={limits['min_area']}, ratio={limits['min_aspect']:.2f}-{limits['max_aspect']:.2f}"
    )

    prepared_paths: list[str] = []
    used_sources: list[str] = []

    # Candidate pool with source priority
    candidate_pool = _collect_article_candidates(article, max_candidates_to_try)

    if article.get("can_scrape_image", True) and article.get("link", ""):
        scraped_candidates = scrape_article_image_urls(
            article.get("link", ""),
            max_candidates=max_candidates_to_try,
        )
        for c in scraped_candidates:
            _upsert_candidate(candidate_pool, c)

    candidate_pool = sorted(candidate_pool, key=lambda x: (int(x.get("priority", 99)), x.get("url", "")))
    candidate_pool = candidate_pool[:max_candidates_to_try]

    log(f"Toplam aday URL (canonical): {len(candidate_pool)}")

    tried_keys: set[str] = set()
    seen_content_hashes: set[str] = set()
    seen_perceptual_records: list[tuple[int, str]] = []
    fail_reasons: Counter[str] = Counter()
    tried_count = 0

    accepted: list[dict] = []

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

        log(
            f"Aday deneniyor ({idx}/{len(candidate_pool)}): "
            f"{candidate_url[:120]} | source={source_type}"
        )

        downloaded, reason = _download_image_with_reason(candidate_url, limits)
        if not downloaded:
            fail_reasons[reason] += 1
            log(f"Aday elendi: {reason}", "WARNING")
            continue

        try:
            width, height, size_kb = _read_image_meta(downloaded)

            content_hash = _file_sha256(downloaded)
            if content_hash in seen_content_hashes:
                fail_reasons["duplicate_image_content"] += 1
                log("Aday elendi: duplicate_image_content", "WARNING")
                try:
                    os.unlink(downloaded)
                except OSError:
                    pass
                continue

            current_signature = _visual_signature(candidate_url)

            try:
                current_phash = _dhash(downloaded)
                is_near_dup = False
                for prev_phash, prev_signature in seen_perceptual_records:
                    dynamic_threshold = _adaptive_perceptual_threshold(
                        perceptual_threshold,
                        current_signature,
                        prev_signature,
                    )
                    if _hamming(current_phash, prev_phash) <= dynamic_threshold:
                        is_near_dup = True
                        break

                if is_near_dup:
                    fail_reasons["near_duplicate_perceptual"] += 1
                    log("Aday elendi: near_duplicate_perceptual", "WARNING")
                    try:
                        os.unlink(downloaded)
                    except OSError:
                        pass
                    continue
            except Exception as ph_exc:
                fail_reasons["perceptual_hash_error"] += 1
                log(f"Perceptual hash atlandi: {ph_exc}", "WARNING")
                current_phash = None

            processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
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

            log(
                f"Aday basarili: {reason} -> quality={score:.1f} ({score_detail})"
            )

        except Exception as exc:
            fail_reasons[f"processing_error:{exc}"] += 1
            log(f"Aday islenemedi: {exc}", "WARNING")
            try:
                os.unlink(downloaded)
            except OSError:
                pass

    # Kaliteye gore secim
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

        # Seçilmeyenleri diskten temizle
        for item in discarded:
            path = item.get("path", "")
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass

    if not prepared_paths:
        fallback = _create_fallback_image(feed_image_width, feed_image_height)
        prepared_paths = [fallback]
        used_sources = ["fallback"]

    article["image_source"] = used_sources[0] if used_sources else "unknown"
    article["image_sources"] = used_sources
    article["prepared_image_count"] = len(prepared_paths)

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
        "link": "https://www.ntv.com.tr/",
        "summary": "Test ozet metni.",
        "image_url": "",
        "rss_image_url": "",
        "image_candidates": [],
        "source_name": "Test Kaynak",
        "source_priority": "high",
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

        image_paths = output.get("image_paths", [])
        if image_paths:
            for idx, path in enumerate(image_paths, start=1):
                if path and os.path.exists(path):
                    size_kb = os.path.getsize(path) // 1024
                    log(f"Gorsel {idx}: {path} ({size_kb} KB)")
                else:
                    log(f"Gorsel {idx}: dosya bulunamadi -> {path}", "WARNING")
        else:
            log("Hazirlanan gorsel yok", "WARNING")

        log("-" * 50)
    else:
        log("Ajan basarisiz oldu", "WARNING")

    log("=== agent_image.py modul testi tamamlandi ===")
