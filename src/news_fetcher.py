"""
src/news_fetcher.py — Haber Çekme Modülü (v5.1 — Test Modu Tekrar Filtresi)

otoXtra Facebook Botu için RSS feed'lerden haber çeken,
ön filtreleme (keyword, zaman, tekrar) yapan modül.

v5.1 Değişiklikler:
  - TEST MODU aktifken "daha önce paylaşılanları çıkar" adımı ATLANIR
  - Bu sayede test sırasında haber havuzu boşalmaz
  - Canlı modda her şey eskisi gibi çalışır

v5 Değişiklikler:
  - Zaman filtresi basitleştirildi: TÜM kaynaklar için 12 saat
  - settings.json'daki max_article_age_hours = 12 kullanılır
  - Kaynak bazlı özel zaman filtresi kaldırıldı

Çalışma sırası (fetch_and_filter_news):
  1. Tüm RSS feed'leri çek           → fetch_all_feeds()
  2. Google News URL'lerini çöz       → resolve_google_news_url()
  3. Keyword filtresi uygula          → apply_keyword_filter()
  4. Zaman filtresi uygula            → apply_time_filter()
  5. Daha önce paylaşılanları çıkar   → remove_already_posted()
     ⚠️ TEST MODUNDA BU ADIM ATLANIR
  6. Benzer haberleri tekil yap       → remove_duplicates()

Kullandığı config dosyaları:
  - config/sources.json   → RSS feed listesi
  - config/keywords.json  → include_keywords / exclude_keywords
  - config/settings.json  → max_article_age_hours (12 saat)

Diğer modüller bu dosyayı şöyle import eder:
    from news_fetcher import fetch_and_filter_news
"""

import os
import re
import urllib.parse
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

from utils import (
    load_config,
    clean_html,
    is_already_posted,
    is_similar_title,
    log,
    get_turkey_now,
    get_posted_news,
    get_last_check_time,
)

# ── Sabit Değerler ──────────────────────────────────────────
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_PRIORITY_ORDER = {"high": 3, "medium": 2, "low": 1}


# ── Test Modu Kontrolü ─────────────────────────────────────

def _is_test_mode() -> bool:
    """TEST_MODE ortam değişkenini kontrol eder."""
    return os.environ.get("TEST_MODE", "false").lower() == "true"


# ── Türkçe Küçük Harf Dönüşümü ─────────────────────────────

def _turkish_lower(text: str) -> str:
    """Türkçe karakterleri doğru küçük harfe çevirir."""
    text = text.replace("İ", "i").replace("I", "ı")
    return text.lower()


# ============================================================
# 1. TÜM RSS FEED'LERİ ÇEK
# ============================================================

def _extract_image_from_entry(entry) -> str:
    """RSS entry'sinden görsel URL'sini çıkarır.

    Sırasıyla şu kaynakları dener:
      1. media:content → url attribute
      2. media:thumbnail → url attribute
      3. enclosure (type=image) → href/url
      4. entry içindeki <img> tag'i (summary/content HTML'inde)

    Returns:
        str: Görsel URL'si. Bulunamazsa boş string.
    """
    # ── Yöntem 1: media:content ──
    media_content = entry.get("media_content", [])
    if media_content:
        for media in media_content:
            media_url = media.get("url", "")
            media_type = media.get("type", "")
            if media_url and ("image" in media_type or media_type == ""):
                return media_url

    # ── Yöntem 2: media:thumbnail ──
    media_thumbnail = entry.get("media_thumbnail", [])
    if media_thumbnail:
        for thumb in media_thumbnail:
            thumb_url = thumb.get("url", "")
            if thumb_url:
                return thumb_url

    # ── Yöntem 3: enclosure ──
    enclosures = entry.get("enclosures", [])
    if enclosures:
        for enc in enclosures:
            enc_type = enc.get("type", "")
            enc_url = enc.get("href", "") or enc.get("url", "")
            if enc_url and "image" in enc_type:
                return enc_url

    # ── Yöntem 4: summary/content içindeki <img> tag'i ──
    html_content = (
        entry.get("summary", "")
        or entry.get("description", "")
        or ""
    )
    content_list = entry.get("content", [])
    if content_list:
        for content_item in content_list:
            html_content += content_item.get("value", "")

    if html_content and "<img" in html_content:
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            img_tag = soup.find("img")
            if img_tag:
                img_src = img_tag.get("src", "")
                if img_src and img_src.startswith("http"):
                    return img_src
        except Exception:
            pass

    return ""


def fetch_all_feeds() -> list[dict]:
    """sources.json'daki tüm RSS feed'leri çeker.

    Returns:
        list[dict]: Çekilen haberlerin listesi.
    """
    sources_cfg = load_config("sources")
    feeds = sources_cfg.get("feeds", [])

    if not feeds:
        log("sources.json'da hiç feed tanımlı değil!", "ERROR")
        return []

    log(f"Toplam {len(feeds)} RSS kaynağı taranacak")

    all_articles: list[dict] = []
    now_iso = get_turkey_now().isoformat()

    for feed_info in feeds:
        feed_url: str = feed_info.get("url", "")
        feed_name: str = feed_info.get("name", "Bilinmeyen")
        feed_priority: str = feed_info.get("priority", "medium")
        feed_language: str = feed_info.get("language", "tr")
        feed_can_scrape: bool = feed_info.get("can_scrape_image", False)
        feed_enabled: bool = feed_info.get("enabled", True)

        if not feed_enabled:
            log(f"  ⏭  {feed_name} — devre dışı, atlanıyor")
            continue

        if not feed_url:
            log(f"  ⚠  {feed_name} — URL boş, atlanıyor", "WARNING")
            continue

        try:
            parsed_feed = feedparser.parse(feed_url)

            if parsed_feed.bozo and not parsed_feed.entries:
                log(
                    f"  ✗  {feed_name} — feed parse hatası: "
                    f"{parsed_feed.bozo_exception}",
                    "WARNING",
                )
                continue

            entry_count = 0
            for entry in parsed_feed.entries:
                title_raw = entry.get("title", "")
                if not title_raw:
                    continue

                title = clean_html(title_raw).strip()
                if not title:
                    continue

                link = entry.get("link", "")
                if not link:
                    continue

                published_str = _extract_published_date(entry, now_iso)

                summary_raw = (
                    entry.get("summary", "")
                    or entry.get("description", "")
                    or ""
                )
                summary = clean_html(summary_raw).strip()

                if len(summary) < 10:
                    summary = ""

                image_url = _extract_image_from_entry(entry)
                if image_url:
                    log(f"    🖼️ RSS görsel bulundu: {image_url[:80]}...", "INFO")

                article = {
                    "title": title,
                    "link": link,
                    "published": published_str,
                    "summary": summary,
                    "image_url": image_url,
                    "source_name": feed_name,
                    "source_priority": feed_priority,
                    "language": feed_language,
                    "can_scrape_image": feed_can_scrape,
                }
                all_articles.append(article)
                entry_count += 1

            log(f"  ✓  {feed_name} — {entry_count} haber çekildi")

        except Exception as exc:
            log(f"  ✗  {feed_name} — feed çekme hatası: {exc}", "ERROR")
            continue

    log(f"Toplam {len(all_articles)} ham haber çekildi")
    return all_articles


def _extract_published_date(entry, fallback_iso: str) -> str:
    """Feed entry'sinden yayın tarihini ISO formatında çıkarır."""
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        try:
            from calendar import timegm
            ts = timegm(struct)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass

    date_str = (
        entry.get("published", "")
        or entry.get("updated", "")
        or ""
    )
    if date_str:
        try:
            dt = dateutil_parser.parse(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except (ValueError, OverflowError):
            pass

    return fallback_iso


# ============================================================
# 2. GOOGLE NEWS URL'LERİNİ ÇÖZ
# ============================================================

def resolve_google_news_url(url: str) -> str:
    """Google News redirect URL'sini gerçek haber URL'sine çevirir."""
    if not url:
        return url

    is_google = any(
        domain in url
        for domain in ["news.google.com", "google.com/rss", "google.com/news"]
    )
    if not is_google:
        return url

    try:
        resp = requests.get(
            url,
            allow_redirects=True,
            timeout=10,
            headers={"User-Agent": _USER_AGENT},
        )
        resolved = resp.url

        still_google = any(
            domain in resolved
            for domain in ["news.google.com", "google.com"]
        )
        if not still_google:
            return resolved

    except Exception:
        pass

    try:
        parsed = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed.query)

        for param_name in ("url", "q"):
            values = query_params.get(param_name, [])
            for val in values:
                if val.startswith("http") and "google.com" not in val:
                    return val

        if parsed.fragment and parsed.fragment.startswith("http"):
            return parsed.fragment

    except Exception:
        pass

    return url


# ============================================================
# 3. KEYWORD FİLTRESİ
# ============================================================

def apply_keyword_filter(articles: list[dict]) -> list[dict]:
    """Anahtar kelime filtresini uygular."""
    keywords_cfg = load_config("keywords")

    include_keywords: list[str] = (
        keywords_cfg.get("include_keywords")
        or keywords_cfg.get("must_match_any")
        or []
    )

    exclude_keywords: list[str] = (
        keywords_cfg.get("exclude_keywords")
        or keywords_cfg.get("must_not_match")
        or []
    )

    if not include_keywords and not exclude_keywords:
        log("[KEYWORD] ⚠️ Keyword listesi boş — filtre atlanıyor", "WARNING")
        return articles

    include_lower = [_turkish_lower(kw) for kw in include_keywords]
    exclude_lower = [_turkish_lower(kw) for kw in exclude_keywords]

    log(
        f"[KEYWORD] Filtre başlıyor: "
        f"{len(include_lower)} dahil, {len(exclude_lower)} hariç kelime",
        "INFO",
    )

    passed: list[dict] = []
    excluded_by_exclude: int = 0
    excluded_by_include: int = 0

    for article in articles:
        title: str = article.get("title", "")
        summary: str = article.get("summary", "")
        text = _turkish_lower(f"{title} {summary}")

        excluded: bool = False
        matched_exclude_kw: str = ""

        if exclude_lower:
            for kw in exclude_lower:
                if kw in text:
                    excluded = True
                    matched_exclude_kw = kw
                    break

        if excluded:
            excluded_by_exclude += 1
            if excluded_by_exclude <= 20:
                log(
                    f"  🚫 EXCLUDE elendi [{matched_exclude_kw}]: "
                    f"{title[:70]}",
                    "INFO",
                )
            continue

        if include_lower:
            found_include: bool = False
            for kw in include_lower:
                if kw in text:
                    found_include = True
                    break

            if not found_include:
                excluded_by_include += 1
                if excluded_by_include <= 20:
                    log(
                        f"  ⛔ INCLUDE eşleşmedi: {title[:70]}",
                        "INFO",
                    )
                continue

        passed.append(article)

    total_excluded: int = excluded_by_exclude + excluded_by_include

    log(
        f"[KEYWORD] {len(articles)} haber → {len(passed)} geçti, "
        f"{total_excluded} elendi "
        f"(exclude: {excluded_by_exclude}, include yok: {excluded_by_include})",
        "INFO",
    )

    return passed


# ============================================================
# 4. ZAMAN FİLTRESİ (v5 — Basit 12 Saat)
# ============================================================

def apply_time_filter(articles: list[dict]) -> list[dict]:
    """Zaman filtresini uygular.

    v5 Basitleştirme:
      - TÜM kaynaklar için aynı zaman dilimi: settings.json'daki
        max_article_age_hours (varsayılan 12 saat)
      - Akıllı zaman filtresi (last_check_time) da korunuyor
      - Hangisi DAHA KISA ise o kullanılır

    Args:
        articles: Filtrelenecek haber listesi.

    Returns:
        list[dict]: Zaman filtresinden geçen haberler.
    """
    settings = load_config("settings")
    max_age_hours: int = settings.get("news", {}).get("max_article_age_hours", 12)
    overlap_minutes: int = 30

    now_utc = datetime.now(timezone.utc)

    # ── Maksimum üst sınır: settings'teki max_article_age_hours ──
    max_cutoff_utc = now_utc - timedelta(hours=max_age_hours)

    # ── Akıllı zaman filtresi: son kontrol zamanı ──
    posted_data = get_posted_news()
    last_check = get_last_check_time(posted_data)

    # 30 dakika overlap tamponu çıkar
    smart_cutoff = last_check - timedelta(minutes=overlap_minutes)
    smart_cutoff_utc = smart_cutoff.astimezone(timezone.utc)

    # Akıllı kesme noktası, maksimum sınırdan eski olamaz
    if smart_cutoff_utc < max_cutoff_utc:
        cutoff_utc = max_cutoff_utc
        log(
            f"[ZAMAN] Akıllı filtre çok eski → "
            f"maksimum {max_age_hours} saat kullanılıyor",
            "INFO",
        )
    else:
        cutoff_utc = smart_cutoff_utc

    # ── Zaman penceresini logla ──
    window_seconds = (now_utc - cutoff_utc).total_seconds()
    window_hours = window_seconds / 3600

    log(
        f"[ZAMAN] Zaman penceresi: son {window_hours:.1f} saat "
        f"(maks: {max_age_hours} saat, overlap: {overlap_minutes}dk)",
        "INFO",
    )

    passed: list[dict] = []
    old_count = 0

    for article in articles:
        published_str = article.get("published", "")
        if not published_str:
            passed.append(article)
            continue

        try:
            pub_dt = dateutil_parser.parse(published_str)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)

            if pub_dt < cutoff_utc:
                old_count += 1
                continue

        except (ValueError, OverflowError, TypeError):
            passed.append(article)
            continue

        passed.append(article)

    log(
        f"[ZAMAN] {len(articles)} haber → {len(passed)} geçti, "
        f"{old_count} eski haber elendi (>{window_hours:.1f} saat)"
    )
    return passed


# ============================================================
# 5. DAHA ÖNCE PAYLAŞILANLARI ÇIKAR
# ============================================================

def remove_already_posted(articles: list[dict]) -> list[dict]:
    """Daha önce paylaşılmış haberleri listeden çıkarır.

    ⚠️ TEST MODUNDA bu fonksiyon çağrılsa bile tüm haberleri geçirir.
    Ancak fetch_and_filter_news() zaten test modunda bu adımı ATLAR.
    """
    posted_data = get_posted_news()
    posts_list = posted_data.get("posts", [])

    if not posts_list:
        log("[TEKRAR] Henüz paylaşılmış haber kaydı yok — filtre atlanıyor")
        return articles

    passed: list[dict] = []
    duplicate_count = 0

    for article in articles:
        url = article.get("link", "")
        title = article.get("title", "")

        if is_already_posted(url, title, posted_data):
            duplicate_count += 1
        else:
            passed.append(article)

    log(
        f"[TEKRAR] {len(articles)} haber → {len(passed)} yeni, "
        f"{duplicate_count} daha önce paylaşılmış"
    )
    return passed


# ============================================================
# 6. BENZERLERİ TEKİL YAP
# ============================================================

def remove_duplicates(articles: list[dict]) -> list[dict]:
    """Aynı/çok benzer haberleri gruplar, her gruptan en iyisini seçer."""
    if len(articles) <= 1:
        return articles

    groups: list[list[dict]] = []
    used: list[bool] = [False] * len(articles)

    for i in range(len(articles)):
        if used[i]:
            continue

        group = [articles[i]]
        used[i] = True
        title_i = articles[i].get("title", "")

        for j in range(i + 1, len(articles)):
            if used[j]:
                continue

            title_j = articles[j].get("title", "")
            if is_similar_title(title_i, title_j):
                group.append(articles[j])
                used[j] = True

        groups.append(group)

    unique: list[dict] = []
    for group in groups:
        best = max(
            group,
            key=lambda a: _PRIORITY_ORDER.get(
                a.get("source_priority", "low"), 0
            ),
        )
        unique.append(best)

    duplicate_count = len(articles) - len(unique)
    if duplicate_count > 0:
        log(
            f"[BENZERLİK] {len(articles)} haber → {len(unique)} tekil, "
            f"{duplicate_count} benzer haber elendi"
        )
    else:
        log(f"[BENZERLİK] {len(articles)} haber — benzer bulunamadı")

    return unique


# ============================================================
# 7. HABER TAM METNİ ÇEK
# ============================================================

def scrape_full_article(url: str) -> str:
    """Haber URL'sine gidip tam metin içeriğini çeker.

    Donanımhaber için özel selector'lar dener, bulamazsa
    genel yöntemlerle (article tag, p tag) çeker.

    Args:
        url: Haberin tam URL'si.

    Returns:
        str: Haberin düz metin içeriği (max 5000 karakter).
             Çekilemezse boş string.
    """
    if not url:
        return ""

    try:
        log(f"📄 Tam metin çekiliyor: {url[:80]}...", "INFO")

        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()

        resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        for unwanted in soup.find_all(["script", "style", "nav", "footer", "aside", "iframe"]):
            unwanted.decompose()

        full_text = ""

        # ── Yöntem 1: Donanımhaber özel selector'ları ──
        if "donanimhaber.com" in url:
            content_selectors = [
                {"class_": "article-content"},
                {"class_": "newsContent"},
                {"class_": "content-text"},
                {"class_": "news-detail-text"},
                {"id": "newsDetailText"},
            ]

            for selector in content_selectors:
                content_div = soup.find("div", **selector)
                if content_div:
                    paragraphs = content_div.find_all("p")
                    if paragraphs:
                        full_text = " ".join(
                            p.get_text(strip=True) for p in paragraphs
                            if len(p.get_text(strip=True)) > 10
                        )
                    else:
                        full_text = content_div.get_text(separator=" ", strip=True)

                    if full_text and len(full_text) > 50:
                        log(f"  ✅ Donanımhaber özel selector ile metin çekildi ({len(full_text)} karakter)", "INFO")
                        break

        # ── Yöntem 2: Genel <article> tag'i ──
        if not full_text or len(full_text) < 50:
            article_tag = soup.find("article")
            if article_tag:
                paragraphs = article_tag.find_all("p")
                if paragraphs:
                    full_text = " ".join(
                        p.get_text(strip=True) for p in paragraphs
                        if len(p.get_text(strip=True)) > 10
                    )

        # ── Yöntem 3: Tüm anlamlı <p> tag'leri ──
        if not full_text or len(full_text) < 50:
            all_paragraphs = soup.find_all("p")
            meaningful = [
                p.get_text(strip=True)
                for p in all_paragraphs
                if len(p.get_text(strip=True)) >= 30
            ]
            full_text = " ".join(meaningful)

        full_text = re.sub(r"\s+", " ", full_text).strip()

        if len(full_text) > 5000:
            full_text = full_text[:5000].rsplit(" ", 1)[0] + "..."

        if full_text:
            log(f"  📄 Tam metin çekildi: {len(full_text)} karakter", "INFO")
        else:
            log(f"  ⚠️ Tam metin çekilemedi: {url[:60]}", "WARNING")

        return full_text

    except requests.exceptions.Timeout:
        log(f"⚠️ Tam metin çekme zaman aşımı: {url}", "WARNING")
        return ""
    except requests.exceptions.RequestException as exc:
        log(f"⚠️ Tam metin çekme HTTP hatası ({url}): {exc}", "WARNING")
        return ""
    except Exception as exc:
        log(f"⚠️ Tam metin çekme genel hata ({url}): {exc}", "ERROR")
        return ""


def get_article_full_text(url: str) -> str:
    """Eski fonksiyon adı ile uyumluluk."""
    return scrape_full_article(url)


# ============================================================
# 8. ANA FONKSİYON — HABER ÇEK + FİLTRELE
# ============================================================

def fetch_and_filter_news() -> list[dict]:
    """Ana fonksiyon: Tüm kaynakları tarar, filtreler, tekil listeyi döner.

    TEST MODU aktifken:
      - ADIM 5 (tekrar kontrolü) ATLANIR
      - Daha önce paylaşılmış haberler de havuzda kalır
      - Test sırasında "uygun haber yok" sorunu yaşanmaz

    Returns:
        list[dict]: Filtrelenmiş, tekil, paylaşıma aday haber listesi.
    """
    test_mode: bool = _is_test_mode()

    log("=" * 55)
    log("HABER ÇEKME VE FİLTRELEME BAŞLIYOR")
    if test_mode:
        log("🧪 TEST MODU: Tekrar paylaşım filtresi DEVRE DIŞI")
    log("=" * 55)

    # ── ADIM 1: Tüm feed'leri çek ──
    articles = fetch_all_feeds()
    log(f"[ADIM 1] Feed çekme tamamlandı → {len(articles)} haber")

    if not articles:
        log("Hiç haber çekilemedi — işlem durduruluyor", "WARNING")
        return []

    # ── ADIM 2: Google News URL'lerini çöz ──
    resolved_count = 0
    for article in articles:
        original_link = article.get("link", "")
        resolved_link = resolve_google_news_url(original_link)
        if resolved_link != original_link:
            article["link"] = resolved_link
            resolved_count += 1

    log(
        f"[ADIM 2] Google News URL çözme → "
        f"{resolved_count} URL çözüldü, {len(articles)} haber"
    )

    # ── ADIM 3: Keyword filtresi ──
    articles = apply_keyword_filter(articles)
    log(f"[ADIM 3] Keyword filtresi sonrası → {len(articles)} haber")

    if not articles:
        log("Keyword filtresinden geçen haber yok", "WARNING")
        return []

    # ── ADIM 4: Zaman filtresi ──
    articles = apply_time_filter(articles)
    log(f"[ADIM 4] Zaman filtresi sonrası → {len(articles)} haber")

    if not articles:
        log("Zaman filtresinden geçen haber yok", "WARNING")
        return []

    # ── ADIM 5: Tekrar kontrolü ──
    if test_mode:
        log(
            f"[ADIM 5] 🧪 TEST MODU: Tekrar kontrolü ATLANDI "
            f"— {len(articles)} haber korundu",
            "INFO",
        )
    else:
        articles = remove_already_posted(articles)
        log(f"[ADIM 5] Tekrar kontrolü sonrası → {len(articles)} haber")

        if not articles:
            log("Tüm haberler daha önce paylaşılmış", "WARNING")
            return []

    # ── ADIM 6: Benzerlik tekilleştirme ──
    articles = remove_duplicates(articles)
    log(f"[ADIM 6] Tekilleştirme sonrası → {len(articles)} haber")

    log("=" * 55)
    log(f"FİLTRELEME TAMAMLANDI — {len(articles)} haber paylaşıma aday")
    log("=" * 55)

    return articles


# ============================================================
# MODÜL TESTİ
# ============================================================

if __name__ == "__main__":
    log("=== news_fetcher.py modül testi başlıyor ===")

    result = fetch_and_filter_news()

    if result:
        log(f"\n{'─' * 50}")
        log(f"İLK 5 ADAY HABER:")
        log(f"{'─' * 50}")
        for i, article in enumerate(result[:5], 1):
            log(f"\n  {i}. {article['title']}")
            log(f"     Kaynak   : {article['source_name']} ({article['source_priority']})")
            log(f"     Tarih    : {article['published']}")
            log(f"     URL      : {article['link'][:80]}...")
            log(f"     Görsel   : {article.get('image_url', 'YOK')[:80]}")
            summary_preview = article.get("summary", "")[:100]
            if summary_preview:
                log(f"     Özet     : {summary_preview}...")
    else:
        log("Paylaşıma aday haber bulunamadı", "WARNING")

    log("\n=== news_fetcher.py modül testi tamamlandı ===")
