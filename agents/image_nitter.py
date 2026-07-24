"""
agents/image_nitter.py - Nitter ve Twitter Görsel Çekme İşlemleri
FxTwitter API, Nitter HTML parse ve x.com og:image fallback fonksiyonları burada.
"""
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from core.logger import log
from agents.image_utils import (
    _USER_AGENT, _REQUEST_TIMEOUT, _is_nitter_url, _is_profile_image_url,
    _resolve_nitter_image_url
)

def _nitter_to_twitter_url(nitter_url: str) -> str:
    if not nitter_url: return ""
    parsed = urlparse(nitter_url)
    path = parsed.path or ""
    m = re.search(r"(/[^/]+/status/\d+)", path)
    if m: return f"https://x.com{m.group(1)}"
    return ""

def _extract_tweet_images_via_fxtwitter(tweet_url: str) -> list[dict]:
    if not tweet_url: return []
    parsed = urlparse(tweet_url)
    path = parsed.path or ""
    if "/status/" not in path: return []
    api_url = f"https://api.fxtwitter.com{path}"
    try:
        response = requests.get(api_url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 200:
            log(f"FxTwitter API kod={data.get('code')}: {tweet_url[:80]}", "WARNING")
            return []
        results = []
        seen = set()
        tweet = data.get("tweet", {})
        media = tweet.get("media", {})
        for photo in media.get("photos", []):
            url = photo.get("url", "")
            if not url or _is_profile_image_url(url): continue
            if "pbs.twimg.com" in url and "name=" not in urlparse(url).query:
                url = f"{url}?name=orig" if "?" in url else f"{url}?name=orig"
            if url not in seen:
                seen.add(url)
                results.append({"url": url, "source_type": "nitter_still"})
        for video in media.get("videos", []):
            thumb = video.get("thumbnail_url", "")
            if not thumb or _is_profile_image_url(thumb): continue
            if "pbs.twimg.com" in thumb and "name=" not in urlparse(thumb).query:
                thumb = f"{thumb}?name=orig" if "?" in thumb else f"{thumb}?name=orig"
            if thumb not in seen:
                seen.add(thumb)
                results.append({"url": thumb, "source_type": "nitter_card"})
        for item in media.get("all", []):
            url = item.get("url", "")
            if not url or _is_profile_image_url(url) or url in seen: continue
            if "pbs.twimg.com" in url and "name=" not in urlparse(url).query:
                url = f"{url}?name=orig" if "?" in url else f"{url}?name=orig"
            seen.add(url)
            results.append({"url": url, "source_type": "nitter_card"})
        if results:
            log(f"FxTwitter API: {len(results)} gorsel bulundu: {tweet_url[:80]}")
        else:
            log(f"FxTwitter API: Tweet'te gorsel yok: {tweet_url[:80]}", "INFO")
        return results
    except Exception as exc:
        log(f"FxTwitter API hatasi: {tweet_url[:80]} -> {exc}", "WARNING")
        return []

def _extract_twitter_og_image(tweet_url: str) -> list[dict]:
    if not tweet_url: return []
    results = []
    seen = set()
    try:
        response = requests.get(tweet_url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.select('meta[property="og:image"]'):
            img_url = tag.get("content", "")
            if not img_url or _is_profile_image_url(img_url): continue
            if "pbs.twimg.com" in img_url:
                parsed = urlparse(img_url)
                if "name=" not in parsed.query:
                    img_url = f"{img_url}&name=orig" if "?" in img_url else f"{img_url}?name=orig"
                if img_url not in seen:
                    seen.add(img_url)
                    results.append({"url": img_url, "source_type": "nitter_still"})
        for tag in soup.select('meta[name="twitter:image"]'):
            img_url = tag.get("content", "")
            if not img_url or _is_profile_image_url(img_url): continue
            if "pbs.twimg.com" in img_url:
                parsed = urlparse(img_url)
                if "name=" not in parsed.query:
                    img_url = f"{img_url}&name=orig" if "?" in img_url else f"{img_url}?name=orig"
                if img_url not in seen:
                    seen.add(img_url)
                    results.append({"url": img_url, "source_type": "nitter_card"})
    except Exception as exc:
        log(f"Twitter og:image cekme hatasi: {tweet_url[:80]} -> {exc}", "WARNING")
    return results

def _extract_nitter_images_from_page(tweet_url: str) -> list[dict]:
    if not tweet_url or not _is_nitter_url(tweet_url): return []
    parsed = urlparse(tweet_url)
    nitter_base = f"{parsed.scheme}://{parsed.netloc}"
    try:
        response = requests.get(tweet_url, headers={"User-Agent": _USER_AGENT}, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as exc:
        log(f"Nitter tweet sayfasi alinamadi: {tweet_url[:80]} -> {exc}", "WARNING")
        return []

    results = []
    seen = set()

    def _add(url: str, stype: str) -> None:
        if url and url not in seen:
            seen.add(url)
            results.append({"url": url, "source_type": stype})

    for a_tag in soup.find_all("a", class_="still-image"):
        href = a_tag.get("href", "")
        resolved = _resolve_nitter_image_url(href, nitter_base)
        if resolved: _add(resolved, "nitter_still")
        img = a_tag.find("img")
        if img:
            src = img.get("src", "")
            resolved_src = _resolve_nitter_image_url(src, nitter_base)
            if resolved_src: _add(resolved_src, "nitter_still")

    for div in soup.find_all("div", class_=lambda c: c and ("card-image" in c or "attachment" in c)):
        for img in div.find_all("img"):
            src = img.get("src", "")
            resolved = _resolve_nitter_image_url(src, nitter_base)
            if resolved: _add(resolved, "nitter_card")
        for a_tag in div.find_all("a"):
            href = a_tag.get("href", "")
            resolved = _resolve_nitter_image_url(href, nitter_base)
            if resolved: _add(resolved, "nitter_card")

    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "/pic/" in src:
            resolved = _resolve_nitter_image_url(src, nitter_base)
            if resolved: _add(resolved, "nitter_card")

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        if "/pic/" in href:
            resolved = _resolve_nitter_image_url(href, nitter_base)
            if resolved: _add(resolved, "nitter_card")

    log(f"Nitter sayfasindan {len(results)} gorsel bulundu: {tweet_url[:80]}")

    if not results:
        twitter_url = _nitter_to_twitter_url(tweet_url)
        if twitter_url:
            log(f"Nitter sayfa bos, FxTwitter API deneniyor: {twitter_url[:80]}")
            fxtwitter_images = _extract_tweet_images_via_fxtwitter(twitter_url)
            if fxtwitter_images:
                log(f"FxTwitter API'den {len(fxtwitter_images)} gorsel bulundu")
                results.extend(fxtwitter_images)
            else:
                log(f"FxTwitter bosa dustu, x.com HTML scrape deneniyor: {twitter_url[:80]}")
                twitter_images = _extract_twitter_og_image(twitter_url)
                if twitter_images:
                    log(f"x.com scrape'tan {len(twitter_images)} gorsel bulundu (profil fotosu filtrelendi)")
                    results.extend(twitter_images)
    return results
