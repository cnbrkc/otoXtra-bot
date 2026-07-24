"""
agents/image_scraper.py - Makale Sayfa Scraping ve Aday Toplama
HTML parse, JSON-LD, Script tag içeriği ve candidate pool oluşturma işlemleri burada.
"""
import json
import requests
from bs4 import BeautifulSoup
from core.logger import log
from agents.image_utils import (
    _USER_AGENT, _REQUEST_TIMEOUT, _is_nitter_url, _is_profile_image_url,
    _add_scrape_candidate, _upsert_candidate, _append_field_candidates,
    _extract_best_src_from_srcset, _collect_jsonld_images, _extract_json_image_urls,
    _SOURCE_PRIORITY
)
from agents.image_nitter import _extract_nitter_images_from_page, _nitter_to_twitter_url, _extract_tweet_images_via_fxtwitter

def scrape_article_image_urls(url: str, max_candidates: int = 8) -> list[dict]:
    if not url: return []
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
                        meta_selectors = [
                            ('meta[property="og:image"]', "content", "meta_og"),
                            ('meta[property="og:image:url"]', "content", "meta_og"),
                            ('meta[name="twitter:image"]', "content", "meta_twitter"),
                            ('meta[name="twitter:image:src"]', "content", "meta_twitter"),
                        ]
                        for selector, attr, source_type in meta_selectors:
                            for tag in soup.select(selector):
                                raw_url = tag.get(attr, "")
                                if raw_url and not _is_profile_image_url(raw_url):
                                    _add_scrape_candidate(pool, raw_url, twitter_url, source_type)
                        log(f"x.com scrape'tan {len(pool)} gorsel adayi bulundu")
                    except Exception as exc:
                        log(f"x.com article scrape hatasi: {exc}", "WARNING")
        return pool

    try:
        log(f"Sayfadan gorsel adaylari araniyor: {url[:100]}")
        response = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        pool_normal = []
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
            src_list = [img.get("src", ""), img.get("data-src", ""), img.get("data-lazy-src", ""), img.get("data-original", ""), img.get("data-full-url", "")]
            srcset = img.get("srcset", "") or img.get("data-srcset", "")
            if srcset:
                best_srcset = _extract_best_src_from_srcset(srcset, url)
                if best_srcset: src_list.append(best_srcset)
            for src in src_list:
                _add_scrape_candidate(pool_normal, src, url, "article_img")

        for source in soup.find_all("source"):
            srcset = source.get("srcset", "") or source.get("data-srcset", "")
            if srcset:
                best_srcset = _extract_best_src_from_srcset(srcset, url)
                if best_srcset:
                    _add_scrape_candidate(pool_normal, best_srcset, url, "article_img")

        for script in soup.select('script[type="application/ld+json"]'):
            script_text = (script.string or script.get_text() or "").strip()
            if not script_text: continue
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
            if not script_text.strip(): continue
            if script.get("type") == "application/ld+json": continue
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
