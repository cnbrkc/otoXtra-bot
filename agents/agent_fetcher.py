"""
agents/agent_fetcher.py — Haber Çekme Ajanı (v6.0 — Trend Dedektörü)

Değişiklikler v6.0:
  - _detect_trends()   : Aynı konudan kaç kaynak geldiğini sayar (YENİ)
  - Her habere "trend_count" ve "trend_bonus" alanları eklenir
  - remove_duplicates() sonrasına eklendi — trend gruplama DEĞİL, sayma
  - Grup hafızası: is_already_posted() artık konu parmak izi de kontrol eder
"""

import os
import re
import sys
from calendar import timegm
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

from core.logger import log
from core.config_loader import load_config
from core.helpers import (
    clean_html,
    get_turkey_now,
    get_posted_news,
    get_last_check_time,
    is_already_posted,
    is_duplicate_article,
    generate_topic_fingerprint,
    _fingerprint_similarity,
)
from core.state_manager import init_pipeline, set_stage, is_stage_done


# ============================================================
# SABİTLER
# ============================================================

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_PRIORITY_ORDER = {"high": 3, "medium": 2, "low": 1}

# Trend bonus eşikleri
# Aynı konudan N kaynak geldiyse şu kadar bonus puan ekle
_TREND_BONUSES = [
    (5, 15),   # 5+ kaynak → +15 puan
    (3, 10),   # 3-4 kaynak → +10 puan
    (2,  5),   # 2 kaynak   → +5 puan
]

# Parmak izi benzerlik eşiği — trend gruplama için
_TREND_FINGERPRINT_THRESHOLD = 0.70


# ============================================================
# TEST MODU KONTROLÜ
# ============================================================

def _is_test_mode() -> bool:
    """TEST_MODE ortam değişkenini veya --test argümanını kontrol eder."""
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    if "--test" in sys.argv:
        return True
    return False


# ============================================================
# TÜRKÇE KÜÇÜK HARF
# ============================================================

def _turkish_lower(text: str) -> str:
    """Türkçe karakterleri doğru küçük harfe çevirir."""
    text = text.replace("İ", "i").replace("I", "ı")
    return text.lower()


# ============================================================
# 1. RSS GÖRSEL ÇIKARMA
# ============================================================

def _extract_image_from_entry(entry) -> str:
    """RSS entry'sinden görsel URL'sini çıkarır. 6 yöntem dener."""

    # Yöntem 1: media:content
    media_content = entry.get("media_content", [])
    if media_content:
        for media in media_content:
            media_url = media.get("url", "")
            media_type = media.get("type", "")
            if media_url and ("image" in media_type or media_type == ""):
                return media_url

    # Yöntem 2: media:thumbnail
    media_thumbnail = entry.get("media_thumbnail", [])
    if media_thumbnail:
        for thumb in media_thumbnail:
            thumb_url = thumb.get("url", "")
            if thumb_url:
                return thumb_url

    # Yöntem 3: enclosure (type=image)
    enclosures = entry.get("enclosures", [])
    if enclosures:
        for enc in enclosures:
            enc_type = enc.get("type", "")
            enc_url = enc.get("href", "") or enc.get("url", "")
            if enc_url and "image" in enc_type:
                return enc_url

    # Yöntem 3b: enclosure URL uzantısına bak
    if enclosures:
        for enc in enclosures:
            enc_url = enc.get("href", "") or enc.get("url", "")
            if enc_url:
                lower_url = enc_url.lower()
                if any(ext in lower_url for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    return enc_url

    # Yöntem 4: HTML içindeki <img>
    html_content = ""
    html_content += entry.get("summary", "") or ""
    html_content += entry.get("description", "") or ""

    content_list = entry.get("content", [])
    if content_list:
        for content_item in content_list:
            html_content += content_item.get("value", "") or ""

    content_encoded = entry.get("content_encoded", "")
    if content_encoded:
        html_content += content_encoded

    if html_content and ("<img" in html_content or "<figure" in html_content):
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            img_tags = soup.find_all("img")

            skip_patterns = [
                "gravatar.com", "pixel.", "tracking.", "analytics.",
                "facebook.com/tr", "1x1.", "spacer.", "blank.",
                ".gif", "emoji", "icon", "badge", "avatar",
            ]

            for img_tag in img_tags:
                img_src = img_tag.get("src", "")
                if not img_src or not img_src.startswith("http"):
                    img_src = img_tag.get("data-src", "")
                if not img_src or not img_src.startswith("http"):
                    img_src = img_tag.get("data-lazy-src", "")
                if not img_src or not img_src.startswith("http"):
                    img_src = img_tag.get("data-original", "")
                if not img_src or not img_src.startswith("http"):
                    img_src = img_tag.get("data-full-url", "")

                if not img_src or not img_src.startswith("http"):
                    continue

                try:
                    if int(img_tag.get("width", 999)) < 50:
                        continue
                    if int(img_tag.get("height", 999)) < 50:
                        continue
                except (ValueError, TypeError):
                    pass

                lower_src = img_src.lower()
                if any(p in lower_src for p in skip_patterns):
                    continue

                return img_src

        except Exception:
            pass

    # Yöntem 5: image alanı
    image_field = entry.get("image", {})
    if isinstance(image_field, dict):
        img_href = image_field.get("href", "") or image_field.get("url", "")
        if img_href and img_href.startswith("http"):
            return img_href
    elif isinstance(image_field, str) and image_field.startswith("http"):
        return image_field

    # Yöntem 6: links alanında image type
    links = entry.get("links", [])
    for link_item in links:
        link_type = link_item.get("type", "")
        link_href = link_item.get("href", "")
        if "image" in link_type and link_href:
            return link_href

    return ""


# ============================================================
# 2. YAYIM TARİHİ ÇIKARMA
# ============================================================

def _extract_published_date(entry, fallback_iso: str) -> str:
    """Feed entry'sinden yayın tarihini ISO formatında çıkarır."""
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        try:
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
# 3. TÜM RSS FEED'LERİ ÇEK
# ============================================================

def fetch_all_feeds() -> list:
    """sources.json'daki tüm RSS feed'leri çeker."""
    sources_cfg = load_config("sources")
    feeds = sources_cfg.get("feeds", [])

    if not feeds:
        log("sources.json'da hiç feed tanımlı değil!", "ERROR")
        return []

    log(f"Toplam {len(feeds)} RSS kaynağı taranacak")

    all_articles = []
    now_iso = get_turkey_now().isoformat()

    for feed_info in feeds:
        feed_url = feed_info.get("url", "")
        feed_name = feed_info.get("name", "Bilinmeyen")
        feed_priority = feed_info.get("priority", "medium")
        feed_language = feed_info.get("language", "tr")
        feed_can_scrape = feed_info.get("can_scrape_image", False)
        feed_enabled = feed_info.get("enabled", True)

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
            image_count = 0

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
                    image_count += 1

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
                    # Trend alanları — _detect_trends() dolduracak
                    "trend_count": 1,
                    "trend_bonus": 0,
                    "topic_fingerprint": generate_topic_fingerprint(title),
                }
                all_articles.append(article)
                entry_count += 1

            log(f"  ✓  {feed_name} — {entry_count} haber ({image_count} görselli)")

        except Exception as exc:
            log(f"  ✗  {feed_name} — feed çekme hatası: {exc}", "ERROR")
            continue

    log(f"Toplam {len(all_articles)} ham haber çekildi")
    return all_articles


# ============================================================
# 4. KEYWORD FİLTRESİ
# ============================================================

def apply_keyword_filter(articles: list) -> list:
    """Anahtar kelime filtresini uygular."""
    keywords_cfg = load_config("keywords")

    include_keywords = (
        keywords_cfg.get("include_keywords")
        or keywords_cfg.get("must_match_any")
        or []
    )
    exclude_keywords = (
        keywords_cfg.get("exclude_keywords")
        or keywords_cfg.get("must_not_match")
        or []
    )

    if not include_keywords and not exclude_keywords:
        log("[KEYWORD] ⚠️ Keyword listesi boş — filtre atlanıyor", "WARNING")
        return articles

    include_lower = [_turkish_lower(kw) for kw in include_keywords]
    exclude_lower = [_turkish_lower(kw) for kw in exclude_keywords]

    log(f"[KEYWORD] {len(include_lower)} dahil, {len(exclude_lower)} hariç kelime")

    passed = []
    excluded_by_exclude = 0
    excluded_by_include = 0

    for article in articles:
        text = _turkish_lower(
            f"{article.get('title', '')} {article.get('summary', '')}"
        )

        excluded = False
        for kw in exclude_lower:
            if kw in text:
                excluded = True
                excluded_by_exclude += 1
                break

        if excluded:
            continue

        if include_lower:
            found = any(kw in text for kw in include_lower)
            if not found:
                excluded_by_include += 1
                continue

        passed.append(article)

    log(
        f"[KEYWORD] {len(articles)} → {len(passed)} geçti "
        f"(exclude: {excluded_by_exclude}, include yok: {excluded_by_include})"
    )
    return passed


# ============================================================
# 5. ZAMAN FİLTRESİ
# ============================================================

def apply_time_filter(articles: list) -> list:
    """Zaman filtresini uygular."""
    test_mode = _is_test_mode()
    settings = load_config("settings")
    max_age_hours = settings.get("news", {}).get("max_article_age_hours", 12)

    now_utc = datetime.now(timezone.utc)
    max_cutoff_utc = now_utc - timedelta(hours=max_age_hours)

    if test_mode:
        cutoff_utc = max_cutoff_utc
        window_hours = max_age_hours
        log(f"[ZAMAN] 🧪 TEST MODU: son {max_age_hours} saat kullanılıyor")
    else:
        overlap_minutes = 30
        posted_data = get_posted_news()
        last_check = get_last_check_time(posted_data)
        smart_cutoff_utc = (
            last_check - timedelta(minutes=overlap_minutes)
        ).astimezone(timezone.utc)

        cutoff_utc = max(smart_cutoff_utc, max_cutoff_utc)
        window_hours = (now_utc - cutoff_utc).total_seconds() / 3600

        log(
            f"[ZAMAN] Pencere: son {window_hours:.1f} saat "
            f"(maks: {max_age_hours}s, overlap: {overlap_minutes}dk)"
        )

    passed = []
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
        f"[ZAMAN] {len(articles)} → {len(passed)} geçti, "
        f"{old_count} eski haber elendi (>{window_hours:.1f}s)"
    )
    return passed


# ============================================================
# 6. TEKRAR KONTROLÜ
# ============================================================

def remove_already_posted(articles: list) -> list:
    """Daha önce paylaşılmış haberleri listeden çıkarır.

    is_already_posted() artık 3 katmanlı kontrol yapıyor:
      1. URL eşleşme
      2. Başlık benzerliği
      3. Konu parmak izi benzerliği  ← YENİ

    Bu sayede "Tesla öldü" haberini A'dan paylaştıktan sonra
    B ve C'deki aynı konu haberleri de engellenir.
    """
    posted_data = get_posted_news()
    posts_list = posted_data.get("posts", [])

    if not posts_list:
        log("[TEKRAR] Kayıt yok — filtre atlanıyor")
        return articles

    passed = []
    duplicate_count = 0

    for article in articles:
        if is_already_posted(
            article.get("link", ""),
            article.get("title", ""),
            posted_data,
        ):
            duplicate_count += 1
            log(
                f"  [TEKRAR] ✗ '{article.get('title', '')[:55]}' "
                f"— daha önce paylaşıldı (konu/URL/başlık eşleşti)"
            )
        else:
            passed.append(article)

    log(
        f"[TEKRAR] {len(articles)} → {len(passed)} yeni, "
        f"{duplicate_count} daha önce paylaşılmış (konu hafızası dahil)"
    )
    return passed


# ============================================================
# 7. BENZERLİK TEKİLLEŞTİRME
# ============================================================

def remove_duplicates(articles: list) -> list:
    """Aynı/çok benzer haberleri gruplar, her gruptan en iyisini seçer.

    Her gruptan priority'si en yüksek kaynak seçilir.
    Seçilmeyen haberler kaybedilir — trend sayımı için
    bu fonksiyon ÖNCE çalışır, _detect_trends() SONRA.
    """
    if len(articles) <= 1:
        return articles

    used = [False] * len(articles)
    groups = []

    for i in range(len(articles)):
        if used[i]:
            continue
        group = [articles[i]]
        used[i] = True

        for j in range(i + 1, len(articles)):
            if used[j]:
                continue
            if is_duplicate_article(articles[i], articles[j]):
                group.append(articles[j])
                used[j] = True

        groups.append(group)

    unique = []
    for group in groups:
        best = max(
            group,
            key=lambda a: _PRIORITY_ORDER.get(
                a.get("source_priority", "low"), 0
            ),
        )
        unique.append(best)

    removed = len(articles) - len(unique)
    if removed > 0:
        log(f"[BENZERLİK] {len(articles)} → {len(unique)} tekil, {removed} elendi")
    else:
        log(f"[BENZERLİK] {len(articles)} haber — benzer bulunamadı")

    return unique


# ============================================================
# 8. TREND DEDEKTÖRܒ (YENİ)
# ============================================================

def _detect_trends(articles: list) -> list:
    """Haberleri konu parmak izine göre gruplar, trend sayısını hesaplar.

    remove_duplicates() SONRASI çalışır. Bu aşamada her haber
    zaten tekil — yani aynı konudan sadece en iyi kaynak kaldı.
    Ama biz kaç kaynaktan geldiğini BİLMEK istiyoruz.

    Algoritma:
      1. Ham haberler (tekilleştirilmeden önce) sayılır
         → Bu bilgi fetch sırasında "source_count_per_topic" olarak tutulur
         → Ama bu yapıda ham liste yok, bu yüzden farklı yaklaşım:
      2. Tekilleştirilmiş liste içinde benzer parmak izli haberleri say
         (Bu turda kaç haber bu konuyu işliyor)
      3. "trend_count" = bu turda kaç tekil haberden bu konu geçti
      4. Bonus puanı belirle

    NOT: Bu fonksiyon kendi listesi içinde sayar.
    Asıl güç is_already_posted()'ın konu hafızasında —
    30 gün boyunca aynı konuyu engelliyor.

    Args:
        articles: remove_duplicates() sonrası tekilleştirilmiş liste.

    Returns:
        list: "trend_count" ve "trend_bonus" alanları güncellenmiş liste.
    """
    if not articles:
        return articles

    n = len(articles)
    # Her haberin parmak izini al (zaten fetch'te üretildi ama güvenlik için)
    fingerprints = []
    for article in articles:
        fp = article.get("topic_fingerprint", "")
        if not fp:
            fp = generate_topic_fingerprint(article.get("title", ""))
            article["topic_fingerprint"] = fp
        fingerprints.append(fp)

    # Her haber için kaç diğer haberle benzer olduğunu say
    # trend_count = 1 (kendisi) + benzer olanlar
    trend_counts = [1] * n

    for i in range(n):
        for j in range(i + 1, n):
            if not fingerprints[i] or not fingerprints[j]:
                continue
            similarity = _fingerprint_similarity(
                fingerprints[i], fingerprints[j]
            )
            if similarity >= _TREND_FINGERPRINT_THRESHOLD:
                trend_counts[i] += 1
                trend_counts[j] += 1

    # Bonus hesapla ve logla
    trend_detected = 0
    for i, article in enumerate(articles):
        count = trend_counts[i]
        article["trend_count"] = count

        bonus = 0
        for min_count, b in _TREND_BONUSES:
            if count >= min_count:
                bonus = b
                break

        article["trend_bonus"] = bonus

        if count >= 2:
            trend_detected += 1
            log(
                f"  🔥 TREND ({count} kaynak, +{bonus} puan): "
                f"{article.get('title', '')[:55]}"
            )

    if trend_detected > 0:
        log(f"[TREND] {trend_detected} trend konu tespit edildi")
    else:
        log("[TREND] Trend konu bulunamadı — tüm haberler tekil")

    return articles


# ============================================================
# 9. TAM METİN ÇEKİCİ
# ============================================================

def scrape_full_article(url: str) -> str:
    """Haber URL'sine gidip tam metin içeriğini çeker."""
    if not url:
        return ""

    try:
        log(f"📄 Tam metin çekiliyor: {url[:80]}...")

        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        for unwanted in soup.find_all(
            ["script", "style", "nav", "footer", "aside", "iframe"]
        ):
            unwanted.decompose()

        full_text = ""

        # Yöntem 1: Donanımhaber özel selector'ları
        if "donanimhaber.com" in url:
            selectors = [
                {"class_": "article-content"},
                {"class_": "newsContent"},
                {"class_": "content-text"},
                {"class_": "news-detail-text"},
                {"id": "newsDetailText"},
            ]
            for sel in selectors:
                div = soup.find("div", **sel)
                if div:
                    paragraphs = div.find_all("p")
                    full_text = " ".join(
                        p.get_text(strip=True)
                        for p in paragraphs
                        if len(p.get_text(strip=True)) > 10
                    ) if paragraphs else div.get_text(separator=" ", strip=True)
                    if len(full_text) > 50:
                        break

        # Yöntem 2: Genel <article> tag'i
        if not full_text or len(full_text) < 50:
            article_tag = soup.find("article")
            if article_tag:
                paragraphs = article_tag.find_all("p")
                if paragraphs:
                    full_text = " ".join(
                        p.get_text(strip=True)
                        for p in paragraphs
                        if len(p.get_text(strip=True)) > 10
                    )

        # Yöntem 3: Tüm anlamlı <p> tag'leri
        if not full_text or len(full_text) < 50:
            all_p = soup.find_all("p")
            full_text = " ".join(
                p.get_text(strip=True)
                for p in all_p
                if len(p.get_text(strip=True)) >= 30
            )

        full_text = re.sub(r"\s+", " ", full_text).strip()

        if len(full_text) > 5000:
            full_text = full_text[:5000].rsplit(" ", 1)[0] + "..."

        if full_text:
            log(f"  📄 Tam metin: {len(full_text)} karakter")
        else:
            log(f"  ⚠️ Tam metin çekilemedi: {url[:60]}", "WARNING")

        return full_text

    except requests.exceptions.Timeout:
        log(f"⚠️ Zaman aşımı: {url}", "WARNING")
        return ""
    except requests.exceptions.RequestException as exc:
        log(f"⚠️ HTTP hatası ({url}): {exc}", "WARNING")
        return ""
    except Exception as exc:
        log(f"⚠️ Genel hata ({url}): {exc}", "ERROR")
        return ""


# ============================================================
# 10. ANA FONKSİYON
# ============================================================

def fetch_and_filter_news() -> list:
    """Tüm kaynakları tarar, filtreler, tekil listeyi döner."""
    test_mode = _is_test_mode()

    log("=" * 55)
    log("HABER ÇEKME VE FİLTRELEME BAŞLIYOR")
    if test_mode:
        log("🧪 TEST MODU aktif")
    log("=" * 55)

    # ADIM 1: Feed çek
    articles = fetch_all_feeds()
    log(f"[ADIM 1] {len(articles)} ham haber")
    if not articles:
        log("Hiç haber çekilemedi", "WARNING")
        return []

    # ADIM 2: Keyword filtresi
    articles = apply_keyword_filter(articles)
    log(f"[ADIM 2] {len(articles)} haber kaldı")
    if not articles:
        log("Keyword filtresinden geçen haber yok", "WARNING")
        return []

    # ADIM 3: Zaman filtresi
    articles = apply_time_filter(articles)
    log(f"[ADIM 3] {len(articles)} haber kaldı")
    if not articles:
        log("Zaman filtresinden geçen haber yok", "WARNING")
        return []

    # ADIM 4: Tekrar kontrolü
    # ÖNEMLİ: Bu adım artık konu bazlı da kontrol ediyor.
    # "Tesla öldü" haberini daha önce paylaştıysak,
    # farklı URL'den gelen "Tesla öldü" haberi burada elenir.
    if test_mode:
        log("[ADIM 4] 🧪 TEST MODU — tekrar kontrolü atlandı")
    else:
        articles = remove_already_posted(articles)
        log(f"[ADIM 4] {len(articles)} haber kaldı")
        if not articles:
            log("Tüm haberler daha önce paylaşılmış", "WARNING")
            return []

    # ADIM 5: Benzerlik tekilleştirme
    articles = remove_duplicates(articles)
    log(f"[ADIM 5] {len(articles)} tekil haber")

    # ADIM 6: Trend tespiti (YENİ)
    # remove_duplicates() sonrası — kalan haberler arasında
    # kaç tanesinin aynı konuyu işlediğini say
    articles = _detect_trends(articles)
    log(f"[ADIM 6] Trend analizi tamamlandı")

    log("=" * 55)
    log(f"FİLTRELEME TAMAMLANDI — {len(articles)} haber aday")
    log("=" * 55)

    return articles


# ============================================================
# 11. AJAN GİRİŞ NOKTASI
# ============================================================

def run() -> bool:
    """Ajanı çalıştırır. orchestrator.py tarafından çağrılır."""
    log("─" * 55)
    log("agent_fetcher başlıyor")
    log("─" * 55)

    set_stage("fetch", "running")

    try:
        articles = fetch_and_filter_news()

        if not articles:
            log("Paylaşıma aday haber bulunamadı", "WARNING")
            set_stage("fetch", "error", error="Haber bulunamadı")
            return False

        output = {
            "articles": articles,
            "count": len(articles),
        }
        set_stage("fetch", "done", output=output)

        log(f"agent_fetcher tamamlandı → {len(articles)} haber pipeline'a yazıldı")
        return True

    except Exception as exc:
        log(f"agent_fetcher kritik hata: {exc}", "ERROR")
        set_stage("fetch", "error", error=str(exc))
        return False


# ============================================================
# MODÜL TESTİ
# ============================================================

if __name__ == "__main__":
    log("=== agent_fetcher.py modül testi başlıyor ===")

    from core.state_manager import init_pipeline
    init_pipeline("test-fetcher")

    success = run()

    if success:
        from core.state_manager import get_stage
        stage = get_stage("fetch")
        articles = stage.get("output", {}).get("articles", [])

        log(f"\n{'─' * 50}")
        log(f"SONUÇ: {len(articles)} haber bulundu")
        log(f"{'─' * 50}")

        for i, article in enumerate(articles[:5], 1):
            log(f"\n  {i}. {article['title']}")
            log(f"     Kaynak      : {article['source_name']} ({article['source_priority']})")
            log(f"     Tarih       : {article['published']}")
            log(f"     Trend       : {article.get('trend_count', 1)} kaynak "
                f"(+{article.get('trend_bonus', 0)} puan)")
            log(f"     Parmak izi  : {article.get('topic_fingerprint', 'YOK')[:50]}")
            log(f"     URL         : {article['link'][:70]}...")
    else:
        log("Ajan başarısız oldu", "WARNING")

    log("\n=== agent_fetcher.py modül testi tamamlandı ===")
