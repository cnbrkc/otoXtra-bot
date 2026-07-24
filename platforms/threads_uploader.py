"""
platforms/threads_uploader.py - Threads Görsel URL Çözümleme ve Yükleme (v5.1)
Nitter bağlantılarını çevirme, orijinal URL bulma ve upload fallback zinciri burada.
"""
import os
import re
from urllib.parse import unquote, urlparse
from core.logger import log
from core.image_uploader import get_public_url_fallback

# ═══════════════════════════════════════════════════════════════════════════════
# ORIJINAL URL CIKARMA & NITTER COZUMLEME
# ═══════════════════════════════════════════════════════════════════════════════

_TWITTER_CDN_HOSTS = {"pbs.twimg.com", "ton.twimg.com", "video.twimg.com"}

_NITTER_PIC_PATTERN = re.compile(
    r"/pic/(?:orig/)?(?:media%2F|media/)([A-Za-z0-9_\-]+\.[a-zA-Z]{3,4})",
    re.IGNORECASE,
)


def _resolve_nitter_url(url: str) -> str:
    """
    Nitter /pic/ URL'lerini orijinal Twitter CDN URL'lerine cevirir.
    """
    if not url or not isinstance(url, str):
        return url

    parsed = urlparse(url)

    if parsed.netloc in _TWITTER_CDN_HOSTS:
        return url

    if "/pic/" in url:
        m = _NITTER_PIC_PATTERN.search(url)
        if m:
            filename = unquote(m.group(1))
            name_part, _, ext_part = filename.rpartition(".")
            ext_part = ext_part.lower()
            quality = "orig" if "/orig/" in url else "large"
            return f"https://pbs.twimg.com/media/{filename}?format={ext_part}&name={quality}"

    return url


def _extract_original_urls(article: dict, max_urls: int = 8) -> list[str]:
    """
    Article dict'inden orijinal gorsel URL'lerini cikarir ve cozumler.
    """
    urls: list[str] = []
    seen: set[str] = set()

    def _add(raw_url: str) -> None:
        if not raw_url or not isinstance(raw_url, str):
            return
        raw_url = raw_url.strip()
        if not raw_url:
            return
        resolved = _resolve_nitter_url(raw_url)
        if not resolved or not resolved.startswith("http"):
            return
        if resolved in seen:
            return
        seen.add(resolved)
        urls.append(resolved)

    original_urls = article.get("original_image_urls", [])
    if isinstance(original_urls, list):
        for url in original_urls:
            _add(url)

    _add(article.get("image_url", ""))

    candidates = article.get("image_candidates", [])
    if isinstance(candidates, list):
        for candidate in candidates[:max_urls * 2]:
            if isinstance(candidate, dict):
                _add(candidate.get("url", ""))
            elif isinstance(candidate, str):
                _add(candidate)

    _add(article.get("rss_image_url", ""))

    if urls:
        log(f"Threads: {len(urls)} orijinal URL cikarildi (cozumlenmis)")

    return urls[:max_urls]


def _resolve_public_url(image_path: str, article: dict = None, image_index: int = 0) -> str | None:
    """
    Bir yerel gorsel dosyasini public URL'ye donusturur.
    Orijinal URL'leri once dener, sonra upload servislerini kullanir.
    """
    if article:
        original_urls = _extract_original_urls(article)
        if image_index < len(original_urls):
            url = original_urls[image_index]
            if url:
                log(f"Carousel gorsel {image_index + 1}: Orijinal URL kullanilacak")
                return url

    if image_path and os.path.exists(image_path):
        url = get_public_url_fallback(image_path)
        if url:
            return url

    return None
