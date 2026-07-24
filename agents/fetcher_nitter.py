"""
agents/fetcher_nitter.py - Nitter ve Twitter Görsel Çekme İşlemleri
FxTwitter API, Nitter HTML parse ve x.com og:image fallback fonksiyonları burada.
v1.1: Spesifik hata yakalama (RequestException, JSONDecodeError) eklendi.
"""
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from core.logger import log
from agents.fetcher_utils import (
    _USER_AGENT, _is_nitter_url, _nitter_to_twitter_url,
    _is_profile_image_url, _resolve_nitter_image_url, _request_with_retry
)

def _extract_tweet_images_via_fxtwitter(tweet_url: str, timeout: int = 20) -> list[str]:
    if not tweet_url: return []
    parsed = urlparse(tweet_url)
    path = parsed.path or ""
    if "/status/" not in path: return []
    api_url = f"https://api.fxtwitter.com{path}"
    results = []
    try:
        response = requests.get(api_url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 200: return []
        tweet = data.get("tweet", {})
        media = tweet.get("media", {})
        for photo in media.get("photos", []):
            url = photo.get("url", "")
            if url and not _is_profile_image_url(url):
                if "pbs.twimg.com" in url and "name=" not in urlparse(url).query:
                    url = f"{url}?name=orig" if "?" in url else f"{url}?name=orig"
                if url not in results: results.append(url)
        for video in media.get("videos", []):
            thumb = video.get("thumbnail_url", "")
            if thumb and not _is_profile_image_url(thumb) and thumb not in results:
                if "pbs.twimg.com" in thumb and "name=" not in urlparse(thumb).query:
                    thumb = f"{thumb}?name=orig" if "?" in thumb else f"{thumb}?name=orig"
                results.append(thumb)
        if results:
            log(f"FxTwitter API: {len(results)} gorsel bulundu: {tweet_url[:80]}")
    except requests.exceptions.Timeout:
        log(f"FxTwitter API zaman asimi: {tweet_url[:80]}", "WARNING")
    except requests.exceptions.ConnectionError:
        log(f"FxTwitter API baglanti hatasi: {tweet_url[:80]}", "WARNING")
    except requests.exceptions.RequestException as exc:
        log(f"FxTwitter API HTTP hatasi: {tweet_url[:80]} -> {exc}", "WARNING")
    except (json.JSONDecodeError, ValueError) as exc:
        log(f"FxTwitter API JSON decode hatasi: {tweet_url[:80]} -> {exc}", "WARNING")
    except Exception as exc:
        log(f"FxTwitter API beklenmedik hata: {tweet_url[:80]} -> {exc}", "WARNING")
    return results

def _extract_twitter_og_image(tweet_url: str, timeout: int = 20) -> list[str]:
    if not tweet_url: return []
    results = []
    try:
        response = _request_with_retry(tweet_url, timeout=timeout, attempts=2, base_wait_seconds=1.5)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.select('meta[property="og:image"]'):
            img_url = tag.get("content", "")
            if not img_url or _is_profile_image_url(img_url): continue
            if "pbs.twimg.com" in img_url:
                parsed = urlparse(img_url)
                if "name=" not in parsed.query:
                    img_url = f"{img_url}&name=orig" if "?" in img_url else f"{img_url}?name=orig"
                results.append(img_url)
        for tag in soup.select('meta[name="twitter:image"]'):
            img_url = tag.get("content", "")
            if not img_url or _is_profile_image_url(img_url): continue
            if "pbs.twimg.com" in img_url and img_url not in results:
                results.append(img_url)
    except requests.exceptions.RequestException as exc:
        log(f"Twitter og:image HTTP hatasi: {tweet_url[:80]} -> {exc}", "WARNING")
    except Exception as exc:
        log(f"Twitter og:image beklenmedik hata: {tweet_url[:80]} -> {exc}", "WARNING")
    return results

def _extract_nitter_images_from_tweet_page(tweet_url: str, timeout: int = 20) -> list[str]:
    if not tweet_url or not _is_nitter_url(tweet_url): return []
    parsed = urlparse(tweet_url)
    nitter_base = f"{parsed.scheme}://{parsed.netloc}"
    try:
        response = _request_with_retry(tweet_url, timeout=timeout, attempts=3, base_wait_seconds=1.8)
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
    except requests.exceptions.RequestException as exc:
        log(f"Nitter tweet sayfasi HTTP hatasi: {tweet_url[:80]} -> {exc}", "WARNING")
        return []
    except Exception as exc:
        log(f"Nitter tweet sayfasi beklenmedik hata: {tweet_url[:80]} -> {exc}", "WARNING")
        return []

    results = []
    seen = set()

    def _add(url: str):
        if url and url not in seen:
            seen.add(url)
            results.append(url)

    for a_tag in soup.find_all("a", class_="still-image"):
        href = a_tag.get("href", "")
        resolved = _resolve_nitter_image_url(href, nitter_base)
        if resolved: _add(resolved)
        img = a_tag.find("img")
        if img:
            src = img.get("src", "")
            resolved_src = _resolve_nitter_image_url(src, nitter_base)
            if resolved_src: _add(resolved_src)

    for div in soup.find_all("div", class_=lambda c: c and ("attachment" in c or "card-image" in c)):
        for img in div.find_all("img"):
            src = img.get("src", "")
            resolved = _resolve_nitter_image_url(src, nitter_base)
            if resolved: _add(resolved)
        for a_tag in div.find_all("a"):
            href = a_tag.get("href", "")
            resolved = _resolve_nitter_image_url(href, nitter_base)
            if resolved: _add(resolved)

    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "/pic/" in src:
            resolved = _resolve_nitter_image_url(src, nitter_base)
            if resolved: _add(resolved)

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        if "/pic/" in href:
            resolved = _resolve_nitter_image_url(href, nitter_base)
            if resolved: _add(resolved)

    log(f"Nitter tweet sayfasindan {len(results)} gorsel bulundu: {tweet_url[:80]}")

    if not results:
        twitter_url = _nitter_to_twitter_url(tweet_url)
        if twitter_url:
            log(f"Nitter bos, FxTwitter API deneniyor: {twitter_url[:80]}")
            fxtwitter_images = _extract_tweet_images_via_fxtwitter(twitter_url, timeout=timeout)
            if fxtwitter_images:
                log(f"FxTwitter API'den {len(fxtwitter_images)} gorsel bulundu")
                results.extend(fxtwitter_images)
            else:
                log(f"FxTwitter bosa dustu, x.com HTML scrape deneniyor: {twitter_url[:80]}")
                twitter_images = _extract_twitter_og_image(twitter_url, timeout=timeout)
                if twitter_images:
                    log(f"x.com scrape'tan {len(twitter_images)} gorsel bulundu (profil fotosu filtrelendi)")
                    results.extend(twitter_images)
    return results
