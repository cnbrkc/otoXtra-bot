"""
agents/fetcher_nitter.py - Nitter ve Twitter Görsel Çekme İşlemleri
FxTwitter API, Nitter HTML parse ve x.com og:image fallback fonksiyonları burada.
v1.1: Spesifik hata yakalama (RequestException, JSONDecodeError) eklendi.
v1.2: Nitter tweet sayfasi da instance failover ile cekiliyor (RSS ile ayni havuz,
      ayni saglik takibi); rate-limit/bot-challenge gorulen instance devre disi.
"""
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from core.logger import log
from agents.fetcher_utils import (
    _USER_AGENT, _is_nitter_url, _nitter_to_twitter_url,
    _is_profile_image_url, _resolve_nitter_image_url, _request_with_retry,
    _nitter_candidate_urls, _normalize_instance_host, _record_nitter_instance_result,
    _detect_nitter_block_reason, _should_block_nitter_instance
)

# Tweet sayfasi icin denenecek maksimum instance sayisi (FxTwitter API zaten yedek).
_NITTER_IMAGE_INSTANCE_LIMIT = 3

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

def _extract_nitter_images_from_instance(tweet_url: str, timeout: int = 20) -> list[str]:
    """Tek bir nitter instance'indan tweet sayfasi gorsellerini cikarir (saglik kaydi ile)."""
    host = _normalize_instance_host(urlparse(tweet_url).netloc)
    parsed = urlparse(tweet_url)
    nitter_base = f"{parsed.scheme}://{parsed.netloc}"
    try:
        response = _request_with_retry(tweet_url, timeout=timeout, attempts=2, base_wait_seconds=1.8)
        response.encoding = response.apparent_encoding or "utf-8"
        block_reason = _detect_nitter_block_reason(response.text or "", getattr(response, "status_code", None))
        if block_reason:
            state = _record_nitter_instance_result(
                host, success=False, reason=block_reason,
                status_code=getattr(response, "status_code", None),
                blocked=_should_block_nitter_instance(block_reason),
            )
            log(f"Nitter tweet sayfasi kullanilamaz: {host} -> {block_reason} (durum={state})", "WARNING")
            return []
        soup = BeautifulSoup(response.text, "html.parser")
    except requests.exceptions.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        state = _record_nitter_instance_result(
            host, success=False, reason=f"http_{status_code or type(exc).__name__.lower()}",
            status_code=status_code, blocked=_should_block_nitter_instance(f"http_{status_code or ''}"),
        )
        log(f"Nitter tweet sayfasi HTTP hatasi: {host} (durum={state}) -> {exc}", "WARNING")
        return []
    except Exception as exc:
        state = _record_nitter_instance_result(host, success=False, reason=type(exc).__name__.lower())
        log(f"Nitter tweet sayfasi beklenmedik hata: {host} (durum={state}) -> {exc}", "WARNING")
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

    if results:
        _record_nitter_instance_result(host, success=True, reason="ok")
        log(f"Nitter tweet sayfasindan {len(results)} gorsel bulundu ({host}): {tweet_url[:80]}")
    else:
        _record_nitter_instance_result(host, success=False, reason="no_images")
        log(f"Nitter tweet sayfasinda gorsel yok ({host}): {tweet_url[:80]}", "WARNING")
    return results

def _extract_nitter_images_from_tweet_page(tweet_url: str, timeout: int = 20) -> list[str]:
    """Tweet sayfasi gorsellerini instance failover ile toplar.

    Link hangi instance'tan geldiyse o host'u tasir; instance rate-limit'e
    girmis olabilir. Ayni yol havuzdaki diger instance'larda da denenir, hepsi
    bos donerse FxTwitter API / x.com og:image fallback'i devreye girer.
    """
    if not tweet_url or not _is_nitter_url(tweet_url): return []
    candidates = _nitter_candidate_urls(tweet_url, max_candidates=_NITTER_IMAGE_INSTANCE_LIMIT)
    results: list[str] = []
    for idx, candidate_url in enumerate(candidates):
        results = _extract_nitter_images_from_instance(candidate_url, timeout=timeout)
        if results:
            if idx > 0:
                log(f"Nitter gorsel failover BASARILI: {candidate_url[:80]} ({len(results)} gorsel)")
            return results
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
