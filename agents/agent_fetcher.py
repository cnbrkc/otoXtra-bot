"""
News fetch and filter agent. (v5.0 - DRY Refactoring)
1673 satırlık dosya 4 modüle bölündü: 
  agent_fetcher (RSS/Filtre), fetcher_utils (URL/İstek), fetcher_nitter (Nitter API), fetcher_scrape (HTML Parse)
"""
import os
import time
import random
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from calendar import timegm
from urllib.parse import urlparse
from dateutil import parser as dateutil_parser
from bs4 import BeautifulSoup

from core.config_loader import load_config
from core.helpers import (
    _fingerprint_similarity, clean_html, generate_topic_fingerprint,
    get_last_check_time, get_last_post_time, get_posted_news, get_turkey_now,
    is_already_posted, is_duplicate_article, is_shared_variant_in_cooldown,
)
from core.logger import log
from core.state_manager import set_stage

# Diğer modülleri içe aktar
from agents.fetcher_utils import (
    _USER_AGENT, _PRIORITY_ORDER, _TREND_BONUSES, _TREND_FINGERPRINT_THRESHOLD,
    _is_test_mode, _safe_int, _safe_float, _safe_int_min, _safe_float_min,
    _coerce_bool, _read_bool_env, _read_int_env, _read_float_env, _turkish_lower,
    _is_nitter_feed, _is_nitter_url, _resolve_nitter_image_url, _normalize_image_url,
    _thumbnail_to_original_variants, _candidate_key, _request_with_retry,
    _nitter_candidate_urls, _normalize_instance_host, _nitter_rotation_mode,
    _nitter_instance_hosts, _nitter_health_summary, _record_nitter_instance_result,
    _detect_nitter_block_reason, _should_block_nitter_instance, _canonicalize_nitter_link,
    _rotation_state
)
from agents.fetcher_scrape import extract_images_from_article, scrape_full_article

# ── RSS ENTRY İŞLEMLERİ ───────────────────────────────────────────────────────

def _extract_image_from_entry(entry) -> str:
    media_content = entry.get("media_content", [])
    for media in media_content:
        media_url = media.get("url", "")
        media_type = media.get("type", "")
        if media_url and ("image" in media_type or media_type == ""): return media_url

    media_thumbnail = entry.get("media_thumbnail", [])
    for thumb in media_thumbnail:
        thumb_url = thumb.get("url", "")
        if thumb_url: return thumb_url

    enclosures = entry.get("enclosures", [])
    for enc in enclosures:
        enc_type = enc.get("type", "")
        enc_url = enc.get("href", "") or enc.get("url", "")
        if enc_url and "image" in enc_type: return enc_url

    for enc in enclosures:
        enc_url = enc.get("href", "") or enc.get("url", "")
        lower_url = enc_url.lower()
        if any(ext in lower_url for ext in [".jpg", ".jpeg", ".png", ".webp"]): return enc_url

    html_content = (entry.get("summary", "") or "") + (entry.get("description", "") or "")
    for content_item in entry.get("content", []) or []:
        html_content += content_item.get("value", "") or ""
    html_content += entry.get("content_encoded", "") or ""

    if html_content and ("<img" in html_content or "<figure" in html_content):
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            for img_tag in soup.find_all("img"):
                img_src = (
                    img_tag.get("src", "") or img_tag.get("data-src", "") or
                    img_tag.get("data-lazy-src", "") or img_tag.get("data-original", "") or
                    img_tag.get("data-full-url", "")
                )
                if not img_src: continue
                if "/pic/" in img_src:
                    resolved = _resolve_nitter_image_url(img_src, "")
                    if resolved: return resolved
                if img_src.startswith("http"): return img_src

            for a_tag in soup.find_all("a", href=True):
                href = a_tag.get("href", "")
                if "/pic/" in href:
                    resolved = _resolve_nitter_image_url(href, "")
                    if resolved: return resolved
        except Exception as exc:
            log(f"Image parse warning: {exc}", "WARNING")

    image_field = entry.get("image", {})
    if isinstance(image_field, dict):
        img_href = image_field.get("href", "") or image_field.get("url", "")
        if img_href.startswith("http"): return img_href
    if isinstance(image_field, str) and image_field.startswith("http"): return image_field

    for link_item in entry.get("links", []) or []:
        link_type = link_item.get("type", "")
        link_href = link_item.get("href", "")
        if "image" in link_type and link_href: return link_href

    return ""

def _extract_published_date(entry, fallback_iso: str) -> str:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        try:
            ts = timegm(struct)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    date_str = entry.get("published", "") or entry.get("updated", "") or ""
    if date_str:
        try:
            dt = dateutil_parser.parse(date_str)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except (ValueError, OverflowError):
            pass
    return fallback_iso

# ── FEED AYARLARI & BEKLEMELER ────────────────────────────────────────────────

def _feed_delay_config() -> tuple[float, float]:
    settings_cfg = load_config("settings")
    posting_cfg = settings_cfg.get("posting", {}) if isinstance(settings_cfg, dict) else {}
    base_delay = _safe_float_min(_read_float_env("FEED_FETCH_DELAY_SECONDS", _safe_float(posting_cfg.get("feed_fetch_delay_seconds", 0.35), 0.35)), 0.0)
    jitter = _safe_float_min(_read_float_env("FEED_FETCH_DELAY_JITTER_SECONDS", _safe_float(posting_cfg.get("feed_fetch_delay_jitter_seconds", 0.4), 0.4)), 0.0)
    return base_delay, jitter

def _nitter_delay_config() -> tuple[float, float, float, float]:
    """Nitter'a ozel bekleme ayarlari (feed oncesi + instance'lar arasi).

    Public nitter instance'lari yuksek frekansli istekte ban/rate-limit/bot
    challenge donduruyor. Bu yuzden nitter kaynaklari normal RSS kaynaklarindan
    belirgin sekilde daha yavas ve jitter'li cekilir.

    Donus: (feed_delay, feed_jitter, instance_delay, instance_jitter)
    """
    settings_cfg = load_config("settings")
    posting_cfg = settings_cfg.get("posting", {}) if isinstance(settings_cfg, dict) else {}
    feed_delay = _safe_float_min(_read_float_env("NITTER_FEED_DELAY_SECONDS", _safe_float(posting_cfg.get("nitter_feed_delay_seconds", 2.0), 2.0)), 0.0)
    feed_jitter = _safe_float_min(_read_float_env("NITTER_FEED_DELAY_JITTER_SECONDS", _safe_float(posting_cfg.get("nitter_feed_delay_jitter_seconds", 2.5), 2.5)), 0.0)
    instance_delay = _safe_float_min(_read_float_env("NITTER_INSTANCE_DELAY_SECONDS", _safe_float(posting_cfg.get("nitter_instance_delay_seconds", 1.5), 1.5)), 0.0)
    instance_jitter = _safe_float_min(_read_float_env("NITTER_INSTANCE_DELAY_JITTER_SECONDS", _safe_float(posting_cfg.get("nitter_instance_delay_jitter_seconds", 2.0), 2.0)), 0.0)
    return feed_delay, feed_jitter, instance_delay, instance_jitter

def _feed_attempt_config(feed_url: str) -> tuple[int, int, float, int]:
    settings_cfg = load_config("settings")
    posting_cfg = settings_cfg.get("posting", {}) if isinstance(settings_cfg, dict) else {}
    is_nitter = _is_nitter_feed(feed_url)
    if is_nitter:
        fetch_attempts = _safe_int_min(_read_int_env("NITTER_FEED_FETCH_ATTEMPTS", _safe_int(posting_cfg.get("nitter_feed_fetch_attempts", 3), 3)), 1, 1)
        http_attempts = _safe_int_min(_read_int_env("NITTER_HTTP_ATTEMPTS", _safe_int(posting_cfg.get("nitter_http_attempts", 3), 3)), 1, 1)
        base_wait = _safe_float_min(_read_float_env("NITTER_HTTP_BASE_WAIT_SECONDS", _safe_float(posting_cfg.get("nitter_http_base_wait_seconds", 2.5), 2.5)), 0.1)
        timeout = _safe_int_min(_read_int_env("NITTER_HTTP_TIMEOUT_SECONDS", _safe_int(posting_cfg.get("nitter_http_timeout_seconds", 25), 25)), 5, 1)
        return fetch_attempts, http_attempts, base_wait, timeout
    fetch_attempts = _safe_int_min(_read_int_env("FEED_FETCH_ATTEMPTS", _safe_int(posting_cfg.get("feed_fetch_attempts", 1), 1)), 1, 1)
    http_attempts = _safe_int_min(_read_int_env("FEED_HTTP_ATTEMPTS", _safe_int(posting_cfg.get("feed_http_attempts", 3), 3)), 1, 1)
    base_wait = _safe_float_min(_read_float_env("FEED_HTTP_BASE_WAIT_SECONDS", _safe_float(posting_cfg.get("feed_http_base_wait_seconds", 1.5), 1.5)), 0.1)
    timeout = _safe_int_min(_read_int_env("FEED_HTTP_TIMEOUT_SECONDS", _safe_int(posting_cfg.get("feed_http_timeout_seconds", 20), 20)), 5, 1)
    return fetch_attempts, http_attempts, base_wait, timeout

def _sleep_between_feeds(feed_name: str, base_delay: float, jitter: float, is_nitter: bool = False) -> None:
    if _is_test_mode(): return
    total_sleep = base_delay + (random.uniform(0, jitter) if jitter > 0 else 0.0)
    if is_nitter:
        nitter_delay, nitter_jitter, _, _ = _nitter_delay_config()
        total_sleep += nitter_delay + (random.uniform(0, nitter_jitter) if nitter_jitter > 0 else 0.0)
    if total_sleep <= 0: return
    log(f"Feed delay: {feed_name} icin {total_sleep:.2f}s bekleniyor{' (nitter ek bekleme dahil)' if is_nitter else ''}")
    time.sleep(total_sleep)

def _sleep_between_instances(feed_name: str, host: str) -> None:
    """Ayni feed icin bir sonraki instance'a gecmeden once bekle (ban korumasi)."""
    if _is_test_mode(): return
    _, _, base_delay, jitter = _nitter_delay_config()
    total_sleep = base_delay + (random.uniform(0, jitter) if jitter > 0 else 0.0)
    if total_sleep <= 0: return
    log(f"Nitter instance delay: {feed_name} -> {host} istegi oncesi {total_sleep:.2f}s bekleniyor")
    time.sleep(total_sleep)

# ── NITTER INSTANCE FAILOVER ─────────────────────────────────────────────────

_NITTER_RSS_ACCEPT = "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.7"

def _log_nitter_pool_once(candidate_count: int) -> None:
    """Ilk nitter feed'inde havuz/rotasyon bilgisini bir kez loglar."""
    mode = _nitter_rotation_mode()
    if _rotation_state.get("logged_mode"): return
    _rotation_state["logged_mode"] = mode
    pool = _nitter_instance_hosts()
    log(f"Nitter instance havuzu: {len(pool)} instance, rotasyon={mode}, feed basina max aday={candidate_count}")
    summary = _nitter_health_summary()
    if summary:
        log(f"Nitter instance saglik durumu: {summary}")

def _fetch_nitter_feed_response(feed_url: str, feed_name: str, timeout: int, http_attempts: int, http_base_wait: float):
    """Nitter feed'ini instance failover + rotasyon ile cekmeye calisir.

    Public instance'larin cogu 2026 itibariyle kapali, rate-limit'li ya da bot
    challenge (Anubis/Cloudflare) arkasinda. Bu yuzden:
      * adaylar havuzdan ROTASYONLA secilir (sequential round-robin / random),
        boylece yuk tek instance'a yigilmaz,
      * emekli instance'lar (nitter.cf, nitter.net, xcancel.com...) hic denenmez,
      * instance'lar arasina jitter'li bekleme konur,
      * 403/429/bot-challenge/RSS-kapali gorulen instance gecici olarak
        devre disi birakilir (circuit breaker, data/nitter_health.json),
      * ENTRY DONDUREN ilk instance kabul edilir.

    Donus: requests.Response (entry'li ya da bos). Tum adaylar HTTP hatasi
    verirse son hata raise edilir (mevcut hata akisi yakalar).
    """
    candidates = _nitter_candidate_urls(feed_url)
    _log_nitter_pool_once(len(candidates))
    failover_timeout = _safe_int_min(_read_int_env("NITTER_FAILOVER_TIMEOUT_SECONDS", min(timeout, 15)), 5, 5)
    rotation_mode = _nitter_rotation_mode()
    last_empty_response = None
    last_exc: Exception | None = None
    attempted = 0

    for idx, candidate_url in enumerate(candidates):
        host = _normalize_instance_host(urlparse(candidate_url).netloc)
        if attempted > 0:
            # Ayni feed icin instance degistirirken bekleme: ardisik istekler
            # tek instance'a ya da arka arkaya baska instance'lara yigilmasin.
            _sleep_between_instances(feed_name, host)
        attempted += 1
        # Ilk aday icin kisitli tekrar denemesi yeter; digerleri icin tek deneme
        # (tekrar denemeleri zaten instance failover ustleniyor).
        attempts = max(1, min(http_attempts, 2)) if idx == 0 else 1
        response = None
        try:
            response = _request_with_retry(
                candidate_url, timeout=failover_timeout, attempts=attempts,
                base_wait_seconds=http_base_wait, extra_headers={"Accept": _NITTER_RSS_ACCEPT}
            )
        except requests.exceptions.HTTPError as exc:
            failed_response = getattr(exc, "response", None)
            status_code = getattr(failed_response, "status_code", None)
            body = ""
            try:
                body = (getattr(failed_response, "text", "") or "")[:2000]
            except Exception:
                body = ""
            reason = _detect_nitter_block_reason(body, status_code) or f"http_{status_code or 'error'}"
            blocked = _should_block_nitter_instance(reason)
            state = _record_nitter_instance_result(host, success=False, reason=reason, status_code=status_code, blocked=blocked)
            last_exc = exc
            log(
                f"Nitter failover ({idx + 1}/{len(candidates)}) {feed_name}: {host} HTTP {status_code} -> {reason}"
                f" (durum={state}), siradaki instance deneniyor", "WARNING"
            )
            continue
        except Exception as exc:
            last_exc = exc
            state = _record_nitter_instance_result(host, success=False, reason=type(exc).__name__.lower())
            if idx < len(candidates) - 1:
                log(
                    f"Nitter failover ({idx + 1}/{len(candidates)}) {feed_name}: {host} erisilemedi ({type(exc).__name__}, durum={state}),"
                    f" siradaki instance deneniyor", "WARNING"
                )
            continue

        status_code = getattr(response, "status_code", None)
        body_preview = ""
        try:
            body_preview = (getattr(response, "content", b"") or b"")[:4096].decode("utf-8", errors="ignore")
        except Exception:
            body_preview = ""

        # HTTP 200 ama govde challenge/rate-limit sayfasi olabilir (Anubis, Cloudflare,
        # "Instance has no auth tokens", "XCancel service is suspended" ...).
        block_reason = _detect_nitter_block_reason(body_preview, status_code)
        if block_reason:
            blocked = _should_block_nitter_instance(block_reason)
            state = _record_nitter_instance_result(host, success=False, reason=block_reason, status_code=status_code, blocked=blocked)
            last_exc = None
            log(
                f"Nitter failover ({idx + 1}/{len(candidates)}) {feed_name}: {host} yanit verdi ama kullanilamaz"
                f" ({block_reason}, durum={state}), siradaki instance deneniyor", "WARNING"
            )
            continue

        probe = None
        try:
            probe = feedparser.parse(response.content)
        except Exception:
            probe = None
        if probe is not None and probe.entries:
            state = _record_nitter_instance_result(host, success=True, reason="ok")
            log(
                f"Nitter failover BASARILI: {feed_name} -> {host} uzerinden {len(probe.entries)} entry"
                f" (aday {idx + 1}/{len(candidates)}, rotasyon={rotation_mode}, durum={state})"
            )
            return response

        # Instance ayakta ama bu kullanici icin entry yok (RSS kapali / hesap bulunamadi).
        _record_nitter_instance_result(host, success=False, reason="empty_feed")
        last_empty_response = response
        last_exc = None
        log(
            f"Nitter failover ({idx + 1}/{len(candidates)}) {feed_name}: {host} bos feed/RSS kapali,"
            f" siradaki instance deneniyor", "WARNING"
        )

    if last_empty_response is not None:
        # En az bir instance yanit verdi ama entry yok -> ana akis 'no_entries' raporlar
        log(f"Nitter: {feed_name} icin hicbir instance entry dondurmedi (denenen={attempted})", "WARNING")
        return last_empty_response
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Nitter failover: {feed_name} icin tum instance'lar basarisiz")

# ── ANA FEED ÇEKME ────────────────────────────────────────────────────────────

def fetch_all_feeds() -> tuple[list[dict], dict]:
    sources_cfg = load_config("sources")
    feeds = sources_cfg.get("feeds", []) if isinstance(sources_cfg, dict) else []
    if not feeds:
        log("No feeds found in sources.json", "ERROR")
        return [], {}

    all_articles = []
    source_health = {}
    now_iso = get_turkey_now().isoformat()

    settings_cfg = load_config("settings")
    images_cfg = settings_cfg.get("images", {}) if isinstance(settings_cfg, dict) else {}
    news_cfg = settings_cfg.get("news", {}) if isinstance(settings_cfg, dict) else {}

    max_articles_per_source = _safe_int_min(news_cfg.get("max_articles_per_source", 25), 25, 1)
    enable_fetch_article_image_scrape = _coerce_bool(images_cfg.get("enable_fetch_article_image_scrape", False), False)
    enable_fetch_article_image_scrape = _read_bool_env("ENABLE_FETCH_ARTICLE_IMAGE_SCRAPE", enable_fetch_article_image_scrape)
    max_candidates_per_article = _safe_int_min(images_cfg.get("max_candidates_per_article", 8), 8, 1)
    max_article_scrapes_per_feed = _safe_int_min(images_cfg.get("max_article_scrapes_per_feed", 6), 6, 0)

    delay_base, delay_jitter = _feed_delay_config()

    for feed_idx, feed_info in enumerate(feeds):
        feed_url = str(feed_info.get("url", "") or "").strip()
        feed_name = str(feed_info.get("name", "Unknown") or "Unknown").strip()
        feed_priority = str(feed_info.get("priority", "medium") or "medium").strip().lower()
        feed_language = str(feed_info.get("language", "tr") or "tr").strip().lower()
        feed_can_scrape = _coerce_bool(feed_info.get("can_scrape_image", True), True)
        feed_enabled = _coerce_bool(feed_info.get("enabled", True), True)

        if not feed_enabled:
            source_health[feed_name] = {"status": "disabled", "count": 0, "detail": "disabled", "attempts": 0}
            continue
        if not feed_url:
            source_health[feed_name] = {"status": "error", "count": 0, "detail": "empty_url", "attempts": 0}
            continue

        fetch_attempts, http_attempts, http_base_wait, timeout = _feed_attempt_config(feed_url)
        is_nitter_feed = _is_nitter_feed(feed_url)
        if is_nitter_feed:
            # Tekrar denemeleri instance failover ustlendigi icin ayni
            # instance'a defalarca istek atmak yerine tek gecis yapilir.
            fetch_attempts = 1

        if feed_idx > 0:
            _sleep_between_feeds(feed_name, delay_base, delay_jitter, is_nitter=is_nitter_feed)

        last_error_detail = ""
        final_entry_count = 0
        success = False
        attempt_used = 0
        feed_instance = ""

        for feed_attempt in range(1, fetch_attempts + 1):
            attempt_used = feed_attempt
            try:
                if is_nitter_feed:
                    response = _fetch_nitter_feed_response(feed_url, feed_name, timeout, http_attempts, http_base_wait)
                else:
                    response = _request_with_retry(feed_url, timeout=timeout, attempts=http_attempts, base_wait_seconds=http_base_wait)
                parsed_feed = feedparser.parse(response.content)
                
                if parsed_feed.bozo and not parsed_feed.entries:
                    bozo_msg = str(getattr(parsed_feed, "bozo_exception", "parse error"))[:120]
                    last_error_detail = f"parse_error: {bozo_msg}"
                    log(f"{feed_name}: parse bozuk ({feed_attempt}/{fetch_attempts}) -> {bozo_msg}", "WARNING")
                    if feed_attempt < fetch_attempts:
                        time.sleep(min(2.5, 0.8 * feed_attempt))
                        continue
                    break

                if not parsed_feed.entries:
                    last_error_detail = "no_entries"
                    log(f"{feed_name}: no_entries ({feed_attempt}/{fetch_attempts})", "WARNING" if feed_attempt < fetch_attempts else "INFO")
                    if feed_attempt < fetch_attempts:
                        time.sleep(min(2.0, 0.6 * feed_attempt))
                        continue
                    break

                entry_count = 0
                for entry in parsed_feed.entries:
                    if entry_count >= max_articles_per_source: break

                    title = clean_html(entry.get("title", "")).strip()
                    link = entry.get("link", "")
                    if not title or not link: continue

                    # Nitter instance rotasyonu yapildigi icin ayni tweet her calismada
                    # farkli instance host'undan (bazen http:// olarak) gelir. Link
                    # kanoniklestirilir (x.com) ki duplike tespiti ve posted_news
                    # kayitlari instance'a bagimli olmasin; nitter sayfasi ayrica
                    # saklanir ki gorsel/metin scrape'i hala nitter uzerinden yuruyebilsin.
                    canonical_link, nitter_page_url = _canonicalize_nitter_link(link)
                    canonical_link = canonical_link or link
                    served_instance = _normalize_instance_host(urlparse(link).netloc) if _is_nitter_url(link) else ""

                    summary_raw = entry.get("summary", "") or entry.get("description", "") or ""
                    summary = clean_html(summary_raw).strip()
                    if len(summary) < 10: summary = ""

                    rss_image_url = _extract_image_from_entry(entry)
                    normalized_rss_image = _normalize_image_url(rss_image_url, nitter_page_url or link) if rss_image_url else ""

                    article_image_candidates = []
                    
                    # v9.0 HIZLANDIRMA: Fetch asamasinda makale scrape tamamen devre disi birakildi.
                    # if feed_can_scrape and effective_scrape and scraped_in_feed < max_article_scrapes_per_feed:
                    #     article_image_candidates = extract_images_from_article(link, max_candidates=max_candidates_per_article)
                    #     scraped_in_feed += 1

                    if normalized_rss_image:
                        rss_variants = _thumbnail_to_original_variants(normalized_rss_image)
                        for rss_item in reversed(rss_variants):
                            if rss_item in article_image_candidates:
                                article_image_candidates.remove(rss_item)
                        article_image_candidates = rss_variants + article_image_candidates

                    raw_candidate_count = len(article_image_candidates)
                    deduped_candidates = []
                    seen_candidate_keys = set()
                    for c in article_image_candidates:
                        if not c: continue
                        c_key = _candidate_key(c)
                        if c_key in seen_candidate_keys: continue
                        seen_candidate_keys.add(c_key)
                        deduped_candidates.append(c)
                        if len(deduped_candidates) >= max_candidates_per_article: break
                    article_image_candidates = deduped_candidates

                    primary_image = article_image_candidates[0] if article_image_candidates else (normalized_rss_image or rss_image_url)

                    article = {
                        "title": title,
                        "link": canonical_link,
                        "nitter_url": nitter_page_url or "",
                        "source_instance": served_instance,
                        "published": _extract_published_date(entry, now_iso),
                        "summary": summary,
                        "image_url": primary_image or "",
                        "rss_image_url": normalized_rss_image or rss_image_url or "",
                        "image_candidates": article_image_candidates,
                        "image_source": ("article" if article_image_candidates and (not normalized_rss_image or article_image_candidates[0] != normalized_rss_image) else ("rss" if primary_image else "none")),
                        "source_name": feed_name,
                        "source_priority": feed_priority,
                        "language": feed_language,
                        "can_scrape_image": feed_can_scrape,
                        "trend_count": 1,
                        "trend_bonus": 0,
                        "topic_fingerprint": generate_topic_fingerprint(title),
                    }
                    all_articles.append(article)
                    entry_count += 1
                    if served_instance and not feed_instance:
                        feed_instance = served_instance

                final_entry_count = entry_count
                success = True
                break

            except Exception as exc:
                last_error_detail = f"unknown_error: {str(exc)[:120]}"
                log(f"{feed_name}: unknown_error ({feed_attempt}/{fetch_attempts}) -> {exc}", "WARNING")

            if feed_attempt < fetch_attempts:
                pause = min(3.5, 0.9 * feed_attempt)
                time.sleep(pause)

        if success:
            source_health[feed_name] = {"status": "ok", "count": final_entry_count, "detail": "", "attempts": attempt_used, "instance": feed_instance}
        else:
            detail = "Feed has no entries" if last_error_detail == "no_entries" else (last_error_detail or "fetch_failed")
            status = "no_entries" if last_error_detail == "no_entries" else "error"
            source_health[feed_name] = {"status": status, "count": 0, "detail": detail, "attempts": attempt_used, "instance": feed_instance}

    return all_articles, source_health

# ── FİLTRELEME & TREND ────────────────────────────────────────────────────────

def apply_keyword_filter(articles: list[dict]) -> list[dict]:
    keywords_cfg = load_config("keywords")
    if not isinstance(keywords_cfg, dict): return articles
    include_keywords = keywords_cfg.get("include_keywords") or keywords_cfg.get("must_match_any") or []
    exclude_keywords = keywords_cfg.get("exclude_keywords") or keywords_cfg.get("must_not_match") or []
    if not include_keywords and not exclude_keywords: return articles

    include_lower = [_turkish_lower(str(kw)) for kw in include_keywords if str(kw).strip()]
    exclude_lower = [_turkish_lower(str(kw)) for kw in exclude_keywords if str(kw).strip()]

    passed = []
    for article in articles:
        text = _turkish_lower(f"{article.get('title', '')} {article.get('summary', '')}")
        if any(kw in text for kw in exclude_lower): continue
        if include_lower and not any(kw in text for kw in include_lower): continue
        passed.append(article)
    return passed

def _apply_time_filter_with_hours(articles: list[dict], max_age_hours: int, use_smart_cutoff: bool) -> tuple[list[dict], datetime]:
    max_age_hours = max(1, _safe_int(max_age_hours, 12))
    now_utc = datetime.now(timezone.utc)
    max_cutoff_utc = now_utc - timedelta(hours=max_age_hours)

    if _is_test_mode() or not use_smart_cutoff:
        cutoff_utc = max_cutoff_utc
    else:
        posted_data = get_posted_news()
        # AKILLI CUTOFF DUZELTMESI: Kesme noktasi eskiden last_check_time'a
        # (her calismada guncellenir) bagliydi. Bot calisma basina sadece
        # 1 haber paylastigi icin, ayni saat diliminde cikan diger taze
        # haberler 'zaten goruldu' sayilip bir sonraki calismada pencere
        # disinda kaliyor ve bir daha ASLA paylasilamiyordu (kayip aday).
        # Simdi kesme noktasi son PAYLASIM zamanina bagli: paylasim yapilmayan
        # saatlerde pencere otomatik genisler ve kacan haberler tekrar
        # degerlendirmeye girer. Zaten paylasilmis olanlari is_already_posted
        # ve duplike filtreleri engeller, tekrar paylasimi olmaz.
        anchor = get_last_post_time(posted_data)
        anchor_name = "last_post"
        if anchor is None:
            anchor = get_last_check_time(posted_data)
            anchor_name = "last_check"
        # Grace (dakika): paylasilan haberden GRACE kadar once yayinlanmis
        # haberler tekrar degerlendirmeye girebilsin. Bot calisma basina
        # tek haber paylastigi icin ayni saat diliminde cikan diger haberler
        # yarista kaybediyordu; 90dk'lik grace, birlikte gelen haber
        # dalgasinin sonraki calismalarda paylasilmasini saglar.
        settings_cfg = load_config("settings")
        news_cfg = settings_cfg.get("news", {}) if isinstance(settings_cfg, dict) else {}
        grace_minutes = _safe_int_min(
            _read_int_env("NEWS_SMART_CUTOFF_GRACE_MINUTES", _safe_int(news_cfg.get("smart_cutoff_grace_minutes", 90), 90)),
            90, 0,
        )
        smart_cutoff_utc = (anchor - timedelta(minutes=grace_minutes)).astimezone(timezone.utc)
        cutoff_utc = max(smart_cutoff_utc, max_cutoff_utc)
        log(f"time_filter anchor: {anchor_name}={anchor.isoformat()} grace={grace_minutes}dk")

    passed = []
    fallback_passed = []
    for article in articles:
        published_str = article.get("published", "")
        if not published_str:
            # Tarih olmayan haberler için fallback: direkt geçir ama logla
            passed.append(article)
            fallback_passed.append(article)
            continue
        try:
            pub_dt = dateutil_parser.parse(published_str)
            if pub_dt.tzinfo is None: pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt < cutoff_utc:
                # Fallback için daha geniş bir pencere kontrolü
                relaxed_cutoff = now_utc - timedelta(hours=max(48, max_age_hours + 12))
                if pub_dt >= relaxed_cutoff:
                    fallback_passed.append(article)
                continue
        except Exception:
            # Parse edilemeyen tarihler için fallback: direkt geçir
            passed.append(article)
            fallback_passed.append(article)
            continue
        passed.append(article)
    
    # Eğer hiç haber geçmediyse ve fallback adayları varsa, onları kullan
    if not passed and fallback_passed:
        log(f"time_filter fallback: {len(fallback_passed)} haber kurtarıldı (tarih yok veya relaxed cutoff)", "WARNING")
        return fallback_passed, max_cutoff_utc
    
    return passed, cutoff_utc

def apply_time_filter(articles: list[dict]) -> list[dict]:
    settings = load_config("settings")
    news_cfg = settings.get("news", {}) if isinstance(settings, dict) else {}
    max_age_hours = _read_int_env("NEWS_MAX_AGE_HOURS", _safe_int(news_cfg.get("max_article_age_hours", 24), 24))
    passed, cutoff_utc = _apply_time_filter_with_hours(articles=articles, max_age_hours=max_age_hours, use_smart_cutoff=True)
    log(f"time_filter: in={len(articles)} out={len(passed)} cutoff={cutoff_utc.isoformat()}")
    return passed

def remove_already_posted(articles: list[dict]) -> list[dict]:
    posted_data = get_posted_news()
    if not posted_data.get("posts", []): return articles
    passed = []
    for article in articles:
        if not is_already_posted(article.get("link", ""), article.get("title", ""), posted_data):
            passed.append(article)
    return passed

def apply_shared_variant_cooldown_filter(articles: list[dict]) -> list[dict]:
    settings = load_config("settings")
    news_cfg = settings.get("news", {}) if isinstance(settings, dict) else {}
    cooldown_hours = _read_int_env("SHARED_VARIANT_COOLDOWN_HOURS", _safe_int(news_cfg.get("shared_variant_cooldown_hours", 48), 48))
    if cooldown_hours <= 0: return articles
    posted_data = get_posted_news()
    passed = [article for article in articles if not is_shared_variant_in_cooldown(article.get("link", ""), article.get("title", ""), posted_data, cooldown_hours)]
    log(f"pipeline.shared_variant_cooldown: {len(articles)} -> {len(passed)} (hours={cooldown_hours})")
    
    # Fallback: Eğer tüm haberler cooldown yüzünden elendiyse, en eski paylaşımı baz alarak gevşetme yap
    if not passed and articles:
        log(f"shared_variant_cooldown fallback: tum haberler elendi ({len(articles)}), daha eski cooldown'lar kontrol ediliyor", "WARNING")
        relaxed_hours = max(24, cooldown_hours // 2)
        passed_relaxed = [article for article in articles if not is_shared_variant_in_cooldown(article.get("link", ""), article.get("title", ""), posted_data, relaxed_hours)]
        if passed_relaxed:
            log(f"shared_variant_cooldown fallback: {len(passed_relaxed)} haber kurtarıldı (relaxed_hours={relaxed_hours})", "WARNING")
            return passed_relaxed
    
    return passed

def remove_duplicates(articles: list[dict]) -> list[dict]:
    if len(articles) <= 1: return articles
    used = [False] * len(articles)
    groups = []
    for i in range(len(articles)):
        if used[i]: continue
        group = [articles[i]]
        used[i] = True
        for j in range(i + 1, len(articles)):
            if used[j]: continue
            if is_duplicate_article(articles[i], articles[j]):
                group.append(articles[j])
                used[j] = True
        groups.append(group)
    unique = []
    for group in groups:
        best = max(group, key=lambda a: _PRIORITY_ORDER.get(a.get("source_priority", "low"), 0))
        unique.append(best)
    return unique

def _detect_trends(articles: list[dict]) -> list[dict]:
    if not articles: return articles
    n = len(articles)
    fps = []
    for article in articles:
        fp = article.get("topic_fingerprint") or generate_topic_fingerprint(article.get("title", ""))
        article["topic_fingerprint"] = fp
        fps.append(fp)
    trend_counts = [1] * n
    for i in range(n):
        for j in range(i + 1, n):
            if not fps[i] or not fps[j]: continue
            if _fingerprint_similarity(fps[i], fps[j]) >= _TREND_FINGERPRINT_THRESHOLD:
                trend_counts[i] += 1
                trend_counts[j] += 1
    for i, article in enumerate(articles):
        count = trend_counts[i]
        article["trend_count"] = count
        bonus = 0
        for min_count, b in _TREND_BONUSES:
            if count >= min_count:
                bonus = b
                break
        article["trend_bonus"] = bonus
    return articles

def fetch_and_filter_news() -> tuple[list[dict], dict]:
    articles, source_health = fetch_all_feeds()
    metrics = {"found": len(articles), "after_keyword": 0, "after_time": 0, "after_posted": 0, "after_duplicate": 0}
    if not articles:
        source_health["_metrics"] = metrics
        return [], source_health
    log(f"pipeline.fetch: {len(articles)}")

    articles = apply_keyword_filter(articles)
    metrics["after_keyword"] = len(articles)
    if not articles:
        log("pipeline.keyword: 0")
        source_health["_metrics"] = metrics
        return [], source_health
    log(f"pipeline.keyword: {len(articles)}")

    original_after_keyword = list(articles)
    articles = apply_time_filter(articles)
    if not articles:
        settings = load_config("settings")
        news_cfg = settings.get("news", {}) if isinstance(settings, dict) else {}
        base_hours = _read_int_env("NEWS_MAX_AGE_HOURS", _safe_int(news_cfg.get("max_article_age_hours", 36), 36))
        relaxed_hours = max(base_hours, 48)
        relaxed, relaxed_cutoff = _apply_time_filter_with_hours(articles=original_after_keyword, max_age_hours=relaxed_hours, use_smart_cutoff=False)
        if relaxed:
            log(f"time_filter fallback aktif: {len(original_after_keyword)} -> {len(relaxed)} (hours={relaxed_hours}, cutoff={relaxed_cutoff.isoformat()})", "WARNING")
            articles = relaxed
        else:
            log("pipeline.time: 0 (fallback da bos)")
            source_health["_metrics"] = metrics
            return [], source_health
    metrics["after_time"] = len(articles)
    log(f"pipeline.time: {len(articles)}")

    if not _is_test_mode():
        before_posted = len(articles)
        articles = remove_already_posted(articles)
        log(f"pipeline.posted: {before_posted} -> {len(articles)}")
        before_cooldown = len(articles)
        articles = apply_shared_variant_cooldown_filter(articles)
        metrics["after_posted"] = len(articles)
        metrics["after_shared_variant_cooldown"] = len(articles)
        # Fallback: Eğer cooldown tüm haberleri elerse, relaxed modda tekrar dene
        if not articles and before_cooldown > 0:
            log(f"pipeline.shared_variant_cooldown exhausted candidates: {before_cooldown} -> 0, relaxed fallback deneniyor", "WARNING")
            relaxed_cooldown_hours = max(24, _read_int_env("SHARED_VARIANT_COOLDOWN_HOURS", 48) // 2)
            posted_data = get_posted_news()
            passed_relaxed = [article for article in original_after_keyword if not is_shared_variant_in_cooldown(article.get("link", ""), article.get("title", ""), posted_data, relaxed_cooldown_hours)]
            if passed_relaxed:
                log(f"shared_variant_cooldown relaxed fallback: {len(passed_relaxed)} haber kurtarıldı (hours={relaxed_cooldown_hours})", "WARNING")
                articles = passed_relaxed
            else:
                source_health["_metrics"] = metrics
                return [], source_health

    before_dup = len(articles)
    articles = remove_duplicates(articles)
    metrics["after_duplicate"] = len(articles)
    log(f"pipeline.duplicate: {before_dup} -> {len(articles)}")

    articles = _detect_trends(articles)
    source_health["_metrics"] = metrics
    return articles, source_health

def run() -> bool:
    set_stage("fetch", "running")
    try:
        articles, source_health = fetch_and_filter_news()
        if not articles:
            set_stage("fetch", "error", output={"articles": [], "count": 0, "source_health": source_health, "metrics": source_health.get("_metrics", {})}, error="No article found")
            return False
        set_stage("fetch", "done", output={"articles": articles, "count": len(articles), "source_health": source_health, "metrics": source_health.get("_metrics", {})})
        return True
    except Exception as exc:
        set_stage("fetch", "error", error=str(exc))
        return False

if __name__ == "__main__":
    run()
