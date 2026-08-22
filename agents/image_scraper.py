"""
agents/image_scraper.py - Makale Sayfa Scraping ve Aday Toplama
HTML parse, JSON-LD, Script tag içeriği ve candidate pool oluşturma işlemleri burada.

v2.1 UPDATE (Haberden görsel çekme sağlamlaştırma):
  - HTTP: tek deneme yerine _request_with_retry (2 deneme) + zengin browser
    header'ları (Accept, Accept-Language, Referer) + encoding düzeltmesi.
  - Seçiciler: og:image:secure_url, article:image, meta[itemprop=image],
    link[rel=image_src] eklendi; lazy attribute seti genişletildi
    (data-echo, data-url, data-image, data-large_image, data-large-image...).
  - <a href> içinde doğrudan resim uzantılı bağlantılar da aday sayılır
    (hero image link'e sarılmış olabilir).
  - style="background-image:url(...)" ve <style> bloklarından görsel URL
    çıkarılır (noise filtresinden geçirilerek).
  - Küçük <img> etiketleri (width/height attr < 200) erken elenir (perf).
  - HTTP hata/status kodları loglanır: 'neden görsel bulunamadı' teşhisi.
"""
import json
import re

import requests
from bs4 import BeautifulSoup

from core.logger import log
from agents.image_utils import (
    _USER_AGENT, _REQUEST_TIMEOUT, _is_nitter_url, _is_profile_image_url,
    _add_scrape_candidate, _upsert_candidate, _append_field_candidates,
    _extract_best_src_from_srcset, _collect_jsonld_images, _extract_json_image_urls,
    _SOURCE_PRIORITY, _candidate_key, _looks_like_noise, _is_probable_image_url,
)
from agents.image_nitter import _extract_nitter_images_from_page, _nitter_to_twitter_url, _extract_tweet_images_via_fxtwitter

# v2.1: Meta etiketlerinde aranan seçiciler (önem sırasına göre)
_META_SELECTORS = [
    ('meta[property="og:image"]', "content", "meta_og"),
    ('meta[property="og:image:url"]', "content", "meta_og"),
    ('meta[property="og:image:secure_url"]', "content", "meta_og"),
    ('meta[name="twitter:image"]', "content", "meta_twitter"),
    ('meta[name="twitter:image:src"]', "content", "meta_twitter"),
    ('meta[property="article:image"]', "content", "meta_og"),
    ('meta[itemprop="image"]', "content", "meta_og"),
    ('link[rel="image_src"]', "href", "meta_og"),
]

# v2.1: Yaygın lazy-load attribute'ları
_IMG_ATTRS = (
    "src", "data-src", "data-lazy-src", "data-original", "data-full-url",
    "data-echo", "data-url", "data-image", "data-large_image",
    "data-large-image", "data-original-src", "data-lazy",
)

_BG_URL_RE = re.compile(
    r"url\(\s*(?:['\"]?)(https?://[^'\")\s]+?\.(?:jpg|jpeg|png|webp|gif|bmp|avif))"
    r"(?:['\"]?)\s*\)",
    re.IGNORECASE,
)


def _extract_style_bg_urls(soup: BeautifulSoup, page_url: str, limit: int = 6) -> list:
    """style attr ve <style> bloklarından background-image URL'leri toplar.

    Modern siteler hero görselini div arka planı olarak basabilir; bu
    yöntem o görselleri de aday havuzuna katar. Noise filtresi uygulanır.
    """
    urls = []
    candidates = []
    for tag in soup.find_all(attrs={"style": True})[:60]:
        style = tag.get("style", "") or ""
        candidates.extend(_BG_URL_RE.findall(style))
    for style_tag in soup.find_all("style")[:5]:
        text = style_tag.get_text() or ""
        candidates.extend(_BG_URL_RE.findall(text))
    seen = set()
    for raw in candidates:
        if raw in seen:
            continue
        seen.add(raw)
        lower = raw.lower()
        if _looks_like_noise(lower) or not _is_probable_image_url(lower):
            continue
        urls.append(raw)
        if len(urls) >= limit:
            break
    return urls


def scrape_article_image_urls(url: str, max_candidates: int = 8) -> list[dict]:
    if not url:
        return []
    if _is_nitter_url(url):
        nitter_results = _extract_nitter_images_from_page(url)
        pool = []
        for item in nitter_results[:max_candidates]:
            candidate_url = item.get("url", "")
            stype = item.get("source_type", "nitter_still")
            if candidate_url:
                _upsert_candidate(pool, {"url": candidate_url, "key": _candidate_key(candidate_url), "source_type": stype, "priority": _SOURCE_PRIORITY.get(stype, 0)})
        if not pool:
            twitter_url = _nitter_to_twitter_url(url)
            if twitter_url:
                log(f"Nitter scrape bosa dustu, FxTwitter API deneniyor: {twitter_url[:80]}")
                fxtwitter_images = _extract_tweet_images_via_fxtwitter(twitter_url)
                if fxtwitter_images:
                    for item in fxtwitter_images[:max_candidates]:
                        candidate_url = item.get("url", "")
                        stype = item.get("source_type", "nitter_still")
                        if candidate_url:
                            _upsert_candidate(pool, {"url": candidate_url, "key": _candidate_key(candidate_url), "source_type": stype, "priority": _SOURCE_PRIORITY.get(stype, 0)})
                    log(f"FxTwitter API'den {len(pool)} gorsel adayi bulundu")
                if not pool:
                    log(f"FxTwitter bosa dustu, x.com HTML scrape deneniyor: {twitter_url[:80]}")
                    try:
                        response = requests.get(twitter_url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT)
                        response.raise_for_status()
                        soup = BeautifulSoup(response.text, "html.parser")
                        for selector, attr, source_type in _META_SELECTORS:
                            for tag in soup.select(selector):
                                raw_url = tag.get(attr, "")
                                if raw_url and not _is_profile_image_url(raw_url):
                                    _add_scrape_candidate(pool, raw_url, twitter_url, source_type)
                        log(f"x.com scrape'tan {len(pool)} gorsel adayi bulundu")
                    except Exception as exc:
                        log(f"x.com article scrape hatasi: {exc}", "WARNING")
        return pool

    try:
        # v2.1: Tek deneme yerine 2 denemeli retry + browser header'ları
        from agents.fetcher_utils import _request_with_retry
        response = _request_with_retry(url, timeout=_REQUEST_TIMEOUT, attempts=2, base_wait_seconds=1.0)
    except requests.exceptions.RequestException as exc:
        log(f"Sayfa cekilemedi (HTTP) {url[:100]} -> {exc}", "WARNING")
        return []
    except Exception as exc:
        log(f"Sayfa cekilemedi (beklenmedik) {url[:100]} -> {exc}", "WARNING")
        return []

    try:
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        pool_normal = []

        # 1) Meta etiketleri (og:, twitter:, article:, itemprop, link[rel])
        for selector, attr, source_type in _META_SELECTORS:
            for tag in soup.select(selector):
                _add_scrape_candidate(pool_normal, tag.get(attr, ""), url, source_type)

        # 2) <img> etiketleri (src + tüm lazy varyantları + srcset)
        for img in soup.find_all("img"):
            width_attr = img.get("width")
            height_attr = img.get("height")
            try:
                if width_attr and height_attr and int(width_attr) < 200 and int(height_attr) < 200:
                    continue  # küçük ikon/boşluk görsellerini erken ele
            except (ValueError, TypeError):
                pass

            src_list = [img.get(attr_name, "") for attr_name in _IMG_ATTRS]
            srcset = img.get("srcset", "") or img.get("data-srcset", "")
            if srcset:
                best_srcset = _extract_best_src_from_srcset(srcset, url)
                if best_srcset:
                    src_list.append(best_srcset)
            for src in src_list:
                _add_scrape_candidate(pool_normal, src, url, "article_img")

        # 3) <source> etiketleri (picture elementleri)
        for source in soup.find_all("source"):
            srcset = source.get("srcset", "") or source.get("data-srcset", "")
            if srcset:
                best_srcset = _extract_best_src_from_srcset(srcset, url)
                if best_srcset:
                    _add_scrape_candidate(pool_normal, best_srcset, url, "article_img")

        # 4) JSON-LD + script içi görsel URL'leri
        for script in soup.select('script[type="application/ld+json"]'):
            script_text = (script.string or script.get_text() or "").strip()
            if not script_text:
                continue
            try:
                parsed_ld = json.loads(script_text)
                _jsonld_images = []
                _collect_jsonld_images(parsed_ld, url, _jsonld_images)
                for ld_url in _jsonld_images:
                    if ld_url and not _is_profile_image_url(ld_url):
                        _add_scrape_candidate(pool_normal, ld_url, url, "article_script")
            except (json.JSONDecodeError, TypeError):
                pass

        for script in soup.find_all("script"):
            script_text = script.string or script.get_text() or ""
            if not script_text.strip():
                continue
            if script.get("type") == "application/ld+json":
                continue
            for script_url in _extract_json_image_urls(script_text):
                _add_scrape_candidate(pool_normal, script_url, url, "article_script")

        # 5) v2.1: <a href> doğrudan resim bağlantıları (hero image linki)
        for anchor in soup.find_all("a", href=True)[:120]:
            href = anchor.get("href", "")
            if _is_probable_image_url(href.lower()):
                _add_scrape_candidate(pool_normal, href, url, "article_img")

        # 6) v2.1: style background-image URL'leri
        for bg_url in _extract_style_bg_urls(soup, url):
            _add_scrape_candidate(pool_normal, bg_url, url, "article_img")

        ordered = sorted(pool_normal, key=lambda x: (int(x.get("priority", 99)), x.get("url", "")))
        cleaned = ordered[:max_candidates]
        log(f"Sayfadan {len(cleaned)} gorsel adayi bulundu (http={response.status_code}, "
            f"ham_aday={len(pool_normal)}) {url[:100]}")
        return cleaned
    except Exception as exc:
        log(f"Sayfa gorsel toplama hatasi: {exc}", "WARNING")
        return []


def _collect_article_candidates(article: dict, max_candidates: int) -> list[dict]:
    pool = []
    base_url = article.get("link", "")
    list_candidates = article.get("image_candidates", [])
    if isinstance(list_candidates, list):
        for item in list_candidates:
            _append_field_candidates(pool, item, base_url, "article_candidates_field")
    _append_field_candidates(pool, article.get("image_url", ""), base_url, "article_field")
    _append_field_candidates(pool, article.get("rss_image_url", ""), base_url, "rss_field")
    ordered = sorted(pool, key=lambda x: (int(x.get("priority", 99)), x.get("url", "")))
    return ordered[:max_candidates]
