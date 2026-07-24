"""
agents/fetcher_scrape.py - Makale Sayfa Scraping ve Görsel Çekme
HTML parse, JSON-LD, Script tag içeriği ve makale metni çıkarma işlemleri burada.
"""
import json
import re
from bs4 import BeautifulSoup
from typing import Any
from core.logger import log
from agents.fetcher_utils import (
    _normalize_image_url, _thumbnail_to_original_variants, _is_probable_image_url,
    _looks_like_noise_image, _candidate_key, _request_with_retry, _is_nitter_url, 
    _safe_int_min, _extract_best_src_from_srcset
)
from agents.fetcher_nitter import _extract_nitter_images_from_tweet_page

def _collect_jsonld_images(node: Any, page_url: str, collector: list[str]) -> None:
    if isinstance(node, dict):
        image_value = node.get("image")
        if isinstance(image_value, str):
            normalized = _normalize_image_url(image_value, page_url)
            if normalized: collector.append(normalized)
        elif isinstance(image_value, list):
            for item in image_value:
                if isinstance(item, str):
                    normalized = _normalize_image_url(item, page_url)
                    if normalized: collector.append(normalized)
                elif isinstance(item, dict):
                    candidate = item.get("url") or item.get("contentUrl")
                    normalized = _normalize_image_url(candidate or "", page_url)
                    if normalized: collector.append(normalized)
        elif isinstance(image_value, dict):
            candidate = image_value.get("url") or image_value.get("contentUrl")
            normalized = _normalize_image_url(candidate or "", page_url)
            if normalized: collector.append(normalized)
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
                    if normalized: collector.append(normalized)
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
    out = []
    if not script_text or not script_text.strip(): return out
    text = script_text.strip()
    try:
        parsed = json.loads(text)
        _walk_json_for_image_urls(parsed, out, page_url)
    except Exception:
        pass
    for m in re.finditer(r'https?://[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp|gif|bmp|avif)', text, flags=re.IGNORECASE):
        normalized = _normalize_image_url(m.group(0), page_url)
        if normalized: out.append(normalized)
    return out

def extract_images_from_article(url: str, max_candidates: int = 8) -> list[str]:
    if not url: return []
    max_candidates = _safe_int_min(max_candidates, 8, 1)

    if _is_nitter_url(url):
        nitter_imgs = _extract_nitter_images_from_tweet_page(url)
        return nitter_imgs[:max_candidates]

    try:
        response = _request_with_retry(url, timeout=15, attempts=2, base_wait_seconds=1.0)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")

        raw_candidates = []
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
            if not text: continue
            try:
                parsed = json.loads(text)
                tmp = []
                _collect_jsonld_images(parsed, url, tmp)
                for item in tmp:
                    raw_candidates.extend(_thumbnail_to_original_variants(item))
            except Exception:
                continue

        for script in soup.find_all("script"):
            script_text = (script.string or script.get_text() or "").strip()
            if not script_text: continue
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
                img.get("src", ""), img.get("data-src", ""),
                img.get("data-lazy-src", ""), img.get("data-original", ""),
                img.get("data-full-url", ""),
            ]

            srcset = img.get("srcset", "") or img.get("data-srcset", "")
            if srcset:
                best_srcset = _extract_best_src_from_srcset(srcset, url)
                if best_srcset: src_candidates.append(best_srcset)

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
        unique_candidates = []
        seen_keys = set()
        for candidate in raw_candidates:
            lower_candidate = candidate.lower()
            key = _candidate_key(candidate)
            if not candidate or key in seen_keys: continue
            if _looks_like_noise_image(lower_candidate): continue
            if not _is_probable_image_url(lower_candidate): continue
            seen_keys.add(key)
            unique_candidates.append(candidate)
            if len(unique_candidates) >= collect_limit: break

        unique_candidates = unique_candidates[:max_candidates]
        log(f"extract_images_from_article: raw={raw_count}, canonical={len(unique_candidates)}, url={url[:120]}")
        return unique_candidates

    except Exception as exc:
        log(f"extract_images_from_article warning: {exc}", "WARNING")
        return []

def scrape_full_article(url: str) -> str:
    if not url: return ""
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
