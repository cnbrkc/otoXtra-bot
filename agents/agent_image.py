"""
agents/agent_image.py - Gorsel Isleme Ajani (v4.6)

Degisiklikler:
- MAX_IMAGES_PER_NEWS env override.
- Runtime limit logu.
- Ayni gorselin farkli URL'lerden tekrar secilmesini engellemek icin
  dosya icerik hash (sha256) tekillemesi.
- Near-duplicate gorselleri azaltmak icin perceptual hash (dHash) kontrolu.
- Prefix boyut varyantlarini canonical key'de normalize etme.
- editor/author/profile gibi alakasiz gorselleri daha sert eleme.
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
_MIN_IMAGE_WIDTH = 400
_MIN_IMAGE_HEIGHT = 220
_MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024

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

    # src_340x1912xslug..., 1400x788slug... gibi prefix boyutlari temizle
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


def _download_image_with_reason(image_url: str) -> tuple[Optional[str], str]:
    if not image_url:
        return None, "empty_url"

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

            if img_width < _MIN_IMAGE_WIDTH or img_height < _MIN_IMAGE_HEIGHT:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                return None, f"too_small:{img_width}x{img_height}"

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


def download_image(image_url: str) -> Optional[str]:
    image_path, reason = _download_image_with_reason(image_url)
    if image_path:
        dims = reason.replace("ok:", "")
        log(f"Gorsel indirildi: {dims}")
        return image_path
    log(f"Gorsel indirilemedi: {reason}", "WARNING")
    return None


def scrape_article_image_urls(url: str, max_candidates: int = 8) -> list[str]:
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
        candidates: list[str] = []

        meta_selectors = [
            ('meta[property="og:image"]', "content"),
            ('meta[property="og:image:url"]', "content"),
            ('meta[name="twitter:image"]', "content"),
            ('meta[name="twitter:image:src"]', "content"),
        ]
        for selector, attr in meta_selectors:
            for tag in soup.select(selector):
                normalized = _normalize_url(tag.get(attr, ""), url)
                if normalized:
                    candidates.extend(_thumbnail_to_original_variants(normalized))

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
                if normalized:
                    candidates.extend(_thumbnail_to_original_variants(normalized))

        cleaned: list[str] = []
        for c in _unique_keep_order(candidates):
            lower = c.lower()
            if _looks_like_noise(lower):
                continue
            if not _is_probable_image_url(lower):
                continue
            cleaned.append(c)
            if len(cleaned) >= max_candidates:
                break

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


def _collect_article_candidates(article: dict, max_candidates: int) -> list[str]:
    candidates: list[str] = []

    list_candidates = article.get("image_candidates", [])
    if isinstance(list_candidates, list):
        for item in list_candidates:
            if isinstance(item, str):
                normalized = _normalize_url(item, article.get("link", ""))
                if normalized:
                    candidates.extend(_thumbnail_to_original_variants(normalized))

    for key in ("rss_image_url", "image_url"):
        value = article.get(key, "")
        if isinstance(value, str) and value.strip():
            normalized = _normalize_url(value.strip(), article.get("link", ""))
            if normalized:
                candidates.extend(_thumbnail_to_original_variants(normalized))

    candidates = _unique_keep_order(candidates)
    return candidates[:max_candidates]


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

    prepared_paths: list[str] = []
    used_sources: list[str] = []

    candidate_urls = _collect_article_candidates(article, max_candidates_to_try)

    if article.get("can_scrape_image", True) and article.get("link", ""):
        needs_more = len(candidate_urls) < max_images_per_news
        if needs_more:
            scraped_urls = scrape_article_image_urls(
                article.get("link", ""),
                max_candidates=max_candidates_to_try,
            )
            candidate_urls.extend(scraped_urls)
            candidate_urls = _unique_keep_order(candidate_urls)[:max_candidates_to_try]

    log(f"Toplam aday URL (canonical): {len(candidate_urls)}")

    tried_keys: set[str] = set()
    seen_content_hashes: set[str] = set()
    seen_perceptual_hashes: list[int] = []
    fail_reasons: Counter[str] = Counter()
    tried_count = 0

    for idx, candidate_url in enumerate(candidate_urls, start=1):
        if len(prepared_paths) >= max_images_per_news:
            break

        key = _candidate_key(candidate_url)
        if key in tried_keys:
            fail_reasons["duplicate_candidate_key"] += 1
            continue
        tried_keys.add(key)
        tried_count += 1

        log(f"Aday deneniyor ({idx}/{len(candidate_urls)}): {candidate_url[:140]}")
        downloaded, reason = _download_image_with_reason(candidate_url)
        if not downloaded:
            fail_reasons[reason] += 1
            log(f"Aday elendi: {reason}", "WARNING")
            continue

        try:
            processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
            if should_add_logo:
                processed = add_logo(processed)

            content_hash = _file_sha256(processed)
            if content_hash in seen_content_hashes:
                fail_reasons["duplicate_image_content"] += 1
                log("Aday elendi: duplicate_image_content", "WARNING")
                try:
                    os.unlink(processed)
                except OSError:
                    pass
                continue

            seen_content_hashes.add(content_hash)

            try:
                perceptual_hash = _dhash(processed)
                if any(_hamming(perceptual_hash, prev) <= perceptual_threshold for prev in seen_perceptual_hashes):
                    fail_reasons["near_duplicate_perceptual"] += 1
                    log("Aday elendi: near_duplicate_perceptual", "WARNING")
                    try:
                        os.unlink(processed)
                    except OSError:
                        pass
                    continue
                seen_perceptual_hashes.append(perceptual_hash)
            except Exception as ph_exc:
                fail_reasons["perceptual_hash_error"] += 1
                log(f"Perceptual hash atlandi: {ph_exc}", "WARNING")

            prepared_paths.append(processed)
            used_sources.append("article_or_rss")
            log(f"Aday basarili: {reason} -> kaydedildi ({len(prepared_paths)}/{max_images_per_news})")
        except Exception as exc:
            fail_reasons[f"processing_error:{exc}"] += 1
            log(f"Aday islenemedi: {exc}", "WARNING")
            try:
                os.unlink(downloaded)
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
