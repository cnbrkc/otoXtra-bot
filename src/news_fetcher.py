"""
src/news_fetcher.py — Haber Çekme Modülü (v3 — Akıllı Zaman Filtresi)

otoXtra Facebook Botu için RSS feed'lerden haber çeken,
ön filtreleme (keyword, zaman, tekrar) yapan modül.

Çalışma sırası (fetch_and_filter_news):
  1. Tüm RSS feed'leri çek           → fetch_all_feeds()
  2. Google News URL'lerini çöz       → resolve_google_news_url()
  3. Keyword filtresi uygula          → apply_keyword_filter()
  4. Zaman filtresi uygula            → apply_time_filter()
  5. Daha önce paylaşılanları çıkar   → remove_already_posted()
  6. Benzer haberleri tekil yap       → remove_duplicates()

v3 Değişiklikler:
  - apply_time_filter() akıllı zaman filtresi kullanıyor:
    posted_news.json'daki last_check_time okunur,
    30 dakika overlap tamponu eklenir, sadece yeni haberler alınır.
  - İlk çalışmada (kayıt yoksa) varsayılan 6 saat geriye bakar.
  - settings.json'daki news_max_age_hours üst sınır olarak korunur.

v2 Değişiklikler:
  - Keyword key isimleri düzeltildi: include_keywords / exclude_keywords
  - Keyword eşleştirme case-insensitive Türkçe desteği eklendi
  - Keyword log mesajları detaylandırıldı (hangi kelime eledi)

Kullandığı config dosyaları:
  - config/sources.json   → RSS feed listesi
  - config/keywords.json  → include_keywords / exclude_keywords
  - config/settings.json  → news_max_age_hours (üst sınır)

Diğer modüller bu dosyayı şöyle import eder:
    from news_fetcher import fetch_and_filter_news

YANLIŞ kullanım (YAPMA):
    from src.news_fetcher import fetch_and_filter_news
"""

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


# ── Türkçe Küçük Harf Dönüşümü ─────────────────────────────

def _turkish_lower(text: str) -> str:
    """Türkçe karakterleri doğru küçük harfe çevirir.

    Python'un str.lower() metodu Türkçe İ→i dönüşümünü
    doğru yapmaz (İ→i̇ yapar). Bu fonksiyon düzeltir.

    Args:
        text: Küçük harfe çevrilecek metin.

    Returns:
        str: Türkçe kurallarına uygun küçük harf metin.
    """
    text = text.replace("İ", "i").replace("I", "ı")
    return text.lower()


# ============================================================
# 1. TÜM RSS FEED'LERİ ÇEK
# ============================================================

def fetch_all_feeds() -> list[dict]:
    """sources.json'daki tüm RSS feed'leri çeker.

    Her feed ayrı ayrı indirilir. Hata olan feed atlanır,
    diğerlerine devam edilir. Her haberden standart bilgiler
    çıkarılarak düz bir listeye eklenir.

    Returns:
        list[dict]: Çekilen haberlerin listesi. Her eleman:
            {
                "title": str,
                "link": str,
                "published": str,        # ISO 8601 format
                "summary": str,
                "source_name": str,
                "source_priority": str,   # "high" / "medium" / "low"
                "language": str,          # "tr" / "en"
                "can_scrape_image": bool
            }
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

        # Devre dışı feed'i atla
        if not feed_enabled:
            log(f"  ⏭  {feed_name} — devre dışı, atlanıyor")
            continue

        if not feed_url:
            log(f"  ⚠  {feed_name} — URL boş, atlanıyor", "WARNING")
            continue

        try:
            parsed_feed = feedparser.parse(feed_url)

            # feedparser hata kontrolü
            if parsed_feed.bozo and not parsed_feed.entries:
                log(
                    f"  ✗  {feed_name} — feed parse hatası: "
                    f"{parsed_feed.bozo_exception}",
                    "WARNING",
                )
                continue

            entry_count = 0
            for entry in parsed_feed.entries:
                # Başlık zorunlu
                title_raw = entry.get("title", "")
                if not title_raw:
                    continue

                title = clean_html(title_raw).strip()
                if not title:
                    continue

                # Link
                link = entry.get("link", "")
                if not link:
                    continue

                # Yayın tarihi
                published_str = _extract_published_date(entry, now_iso)

                # Özet
                summary_raw = (
                    entry.get("summary", "")
                    or entry.get("description", "")
                    or ""
                )
                summary = clean_html(summary_raw).strip()

                # Çok kısa özetleri boş kabul et
                if len(summary) < 10:
                    summary = ""

                article = {
                    "title": title,
                    "link": link,
                    "published": published_str,
                    "summary": summary,
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
    """Feed entry'sinden yayın tarihini ISO formatında çıkarır.

    feedparser birden fazla tarih alanı sağlayabilir. Sırasıyla
    published_parsed, updated_parsed ve string versiyonları denenir.

    Args:
        entry: feedparser entry nesnesi.
        fallback_iso: Tarih bulunamazsa kullanılacak ISO string.

    Returns:
        str: ISO 8601 formatında tarih stringi.
    """
    # Yöntem 1: feedparser struct_time
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        try:
            from calendar import timegm
            ts = timegm(struct)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass

    # Yöntem 2: Ham tarih stringi → dateutil parse
    date_str = (
        entry.get("published", "")
        or entry.get("updated", "")
        or ""
    )
    if date_str:
        try:
            dt = dateutil_parser.parse(date_str)
            # timezone yoksa UTC varsay
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except (ValueError, OverflowError):
            pass

    # Hiçbiri çalışmadıysa şu anki zaman
    return fallback_iso


# ============================================================
# 2. GOOGLE NEWS URL'LERİNİ ÇÖZ
# ============================================================

def resolve_google_news_url(url: str) -> str:
    """Google News redirect URL'sini gerçek haber URL'sine çevirir.

    Yöntem 1: HTTP redirect takibi (requests).
    Yöntem 2: URL parametresinden gerçek URL çıkarma.

    Google News URL'si değilse orijinal URL'yi aynen döner.

    Args:
        url: Kontrol edilecek / çözülecek URL.

    Returns:
        str: Gerçek haber URL'si veya orijinal URL.
    """
    if not url:
        return url

    # Google News URL'si mi kontrol et
    is_google = any(
        domain in url
        for domain in ["news.google.com", "google.com/rss", "google.com/news"]
    )
    if not is_google:
        return url

    # ── Yöntem 1: HTTP redirect takibi ──
    try:
        resp = requests.get(
            url,
            allow_redirects=True,
            timeout=10,
            headers={"User-Agent": _USER_AGENT},
        )
        resolved = resp.url

        # Hâlâ Google'da mıyız?
        still_google = any(
            domain in resolved
            for domain in ["news.google.com", "google.com"]
        )
        if not still_google:
            return resolved

    except Exception:
        pass  # Yöntem 2'ye geç

    # ── Yöntem 2: URL parametresinden çıkar ──
    try:
        parsed = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed.query)

        # "url" veya "q" parametresi gerçek URL'yi içerebilir
        for param_name in ("url", "q"):
            values = query_params.get(param_name, [])
            for val in values:
                if val.startswith("http") and "google.com" not in val:
                    return val

        # Fragment içinde olabilir (#)
        if parsed.fragment and parsed.fragment.startswith("http"):
            return parsed.fragment

    except Exception:
        pass

    # Hiçbir yöntem çalışmadıysa orijinal URL
    return url


# ============================================================
# 3. KEYWORD FİLTRESİ (v2 — Düzeltilmiş)
# ============================================================

def apply_keyword_filter(articles: list[dict]) -> list[dict]:
    """Anahtar kelime filtresini uygular.

    keywords.json'dan dahil (include_keywords) ve hariç
    (exclude_keywords) listelerini okur.

    Geçiş kuralı:
      1. ÖNCE exclude kontrolü: Başlık+özette exclude kelimesi varsa → RED
      2. SONRA include kontrolü: include listesi doluysa,
         başlık+özette en az 1 include kelimesi olmalı → yoksa RED
      3. include listesi boşsa → exclude'dan geçen herkes kabul

    Karşılaştırmalar Türkçe-uyumlu case-insensitive yapılır.

    Args:
        articles: Filtrelenecek haber listesi.

    Returns:
        list[dict]: Filtreden geçen haberler.
    """
    keywords_cfg = load_config("keywords")

    # ── v2 DÜZELTMESİ: Doğru key isimleri ──
    # Önce yeni (doğru) key isimlerini dene
    # Sonra eski key isimlerini dene (geriye uyumluluk)
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

    # Kelime listeleri boşsa tüm haberleri geçir
    if not include_keywords and not exclude_keywords:
        log("[KEYWORD] ⚠️ Keyword listesi boş — filtre atlanıyor", "WARNING")
        return articles

    # Türkçe-uyumlu küçük harfe çevir (bir kez)
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

        # ── ADIM 1: Hariç kelime kontrolü (önce) ──
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
            # Sadece ilk 20 elemeyi logla (spam önleme)
            if excluded_by_exclude <= 20:
                log(
                    f"  🚫 EXCLUDE elendi [{matched_exclude_kw}]: "
                    f"{title[:70]}",
                    "INFO",
                )
            continue

        # ── ADIM 2: Dahil kelime kontrolü ──
        if include_lower:
            found_include: bool = False
            for kw in include_lower:
                if kw in text:
                    found_include = True
                    break

            if not found_include:
                excluded_by_include += 1
                # Sadece ilk 20 elemeyi logla
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

    if excluded_by_exclude > 20:
        log(
            f"  ℹ️ ... ve {excluded_by_exclude - 20} exclude elenme daha (loglanmadı)",
            "INFO",
        )

    if excluded_by_include > 20:
        log(
            f"  ℹ️ ... ve {excluded_by_include - 20} include elenme daha (loglanmadı)",
            "INFO",
        )

    return passed


# ============================================================
# 4. ZAMAN FİLTRESİ (v3 — Akıllı Zaman Filtresi)
# ============================================================

def apply_time_filter(articles: list[dict]) -> list[dict]:
    """Son kontrolden bu yana gelen haberleri filtreler (Akıllı Zaman Filtresi).

    posted_news.json'daki last_check_time'ı okur ve 30 dakikalık
    overlap tamponu ekleyerek kesme noktası belirler.

    Akıllı filtre sayesinde:
      - Bot 2 saatte bir çalışıyorsa → ~2.5 saatlik pencere (48 saat yerine)
      - İlk çalışmada (kayıt yoksa) → 6 saatlik pencere
      - %70 daha az haber YZ'ye gider → API tasarrufu

    Güvenlik: settings.json'daki news_max_age_hours değeri
    maksimum üst sınır olarak korunur (varsayılan 48 saat).

    Tarih parse edilemezse haber güvenli tarafta kalır (geçer).

    Args:
        articles: Filtrelenecek haber listesi.

    Returns:
        list[dict]: Zaman filtresinden geçen haberler.
    """
    settings = load_config("settings")
    max_age_hours: int = settings.get("news_max_age_hours", 48)
    overlap_minutes: int = 30  # Haber kaçırma tamponu

    now_utc = datetime.now(timezone.utc)

    # ── Maksimum üst sınır (settings'ten) ──
    max_cutoff_utc = now_utc - timedelta(hours=max_age_hours)

    # ── Akıllı zaman filtresi: son kontrol zamanını oku ──
    posted_data = get_posted_news()
    last_check = get_last_check_time(posted_data)

    # 30 dakika overlap tamponu çıkar (haber kaçırmasın)
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
    window_minutes = int(window_seconds / 60)

    log(
        f"[ZAMAN] Akıllı zaman penceresi: son {window_hours:.1f} saat "
        f"({window_minutes} dakika) — overlap tamponu: {overlap_minutes}dk",
        "INFO",
    )

    passed: list[dict] = []
    old_count = 0

    for article in articles:
        published_str = article.get("published", "")
        if not published_str:
            # Tarih yoksa geçir (güvenli taraf)
            passed.append(article)
            continue

        try:
            pub_dt = dateutil_parser.parse(published_str)
            # timezone yoksa UTC varsay
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)

            if pub_dt < cutoff_utc:
                old_count += 1
                continue

        except (ValueError, OverflowError, TypeError):
            # Tarih parse edilemezse haberi geçir
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

    data/posted_news.json kaydını okur ve her haberin
    URL'si veya başlığı ile karşılaştırır.

    Args:
        articles: Kontrol edilecek haber listesi.

    Returns:
        list[dict]: Daha önce paylaşılmamış haberler.
    """
    posted_data = get_posted_news()
    posts_list = posted_data.get("posts", [])

    # Hiç kayıt yoksa hepsini geçir
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
    """Aynı/çok benzer haberleri gruplar, her gruptan en iyisini seçer.

    Başlık benzerliği %80+ olan haberler aynı grup sayılır.
    Her gruptan source_priority'si en yüksek olan seçilir
    (high > medium > low). Eşit priority ise ilk gelen seçilir.

    Args:
        articles: Tekilleştirilecek haber listesi.

    Returns:
        list[dict]: Tekil haber listesi.
    """
    if len(articles) <= 1:
        return articles

    # Grup oluştur: her grup benzer haberleri içerir
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

    # Her gruptan en yüksek priority olanı seç
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

def get_article_full_text(url: str) -> str:
    """Haber URL'sine gidip tam metin içeriğini çeker.

    Önce <article> tag'ini arar, yoksa sayfadaki <p>
    tag'lerini birleştirir. Sonuç en fazla 5000 karakter.

    BeautifulSoup parser olarak 'html.parser' kullanılır.

    Args:
        url: Haberin tam URL'si.

    Returns:
        str: Haberin düz metin içeriği. Çekilemezse boş string.
    """
    if not url:
        return ""

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()

        # Encoding düzeltme (Türkçe karakterler için)
        resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        full_text = ""

        # Yöntem 1: <article> tag'i
        article_tag = soup.find("article")
        if article_tag:
            paragraphs = article_tag.find_all("p")
            if paragraphs:
                full_text = " ".join(p.get_text(strip=True) for p in paragraphs)

        # Yöntem 2: Sayfadaki tüm <p> tag'leri
        if not full_text:
            all_paragraphs = soup.find_all("p")
            # En az 30 karakterlik paragrafları al (menü/footer değil)
            meaningful = [
                p.get_text(strip=True)
                for p in all_paragraphs
                if len(p.get_text(strip=True)) >= 30
            ]
            full_text = " ".join(meaningful)

        # Boşlukları temizle
        full_text = re.sub(r"\s+", " ", full_text).strip()

        # Maksimum 5000 karakter
        if len(full_text) > 5000:
            full_text = full_text[:5000].rsplit(" ", 1)[0] + "..."

        return full_text

    except requests.exceptions.Timeout:
        log(f"Tam metin çekme zaman aşımı: {url}", "WARNING")
        return ""
    except requests.exceptions.RequestException as exc:
        log(f"Tam metin çekme HTTP hatası ({url}): {exc}", "WARNING")
        return ""
    except Exception as exc:
        log(f"Tam metin çekme genel hata ({url}): {exc}", "ERROR")
        return ""


# ============================================================
# 8. ANA FONKSİYON — HABER ÇEK + FİLTRELE
# ============================================================

def fetch_and_filter_news() -> list[dict]:
    """Ana fonksiyon: Tüm kaynakları tarar, filtreler, tekil listeyi döner.

    Çalışma sırası:
      1. Tüm RSS feed'leri çek
      2. Google News URL'lerini gerçek URL'ye çevir
      3. Keyword filtresi uygula
      4. Zaman filtresi uygula (akıllı — son kontrolden itibaren)
      5. Daha önce paylaşılanları çıkar
      6. Benzerleri tekilleştir

    Her adımda kalan haber sayısı loglanır.

    Returns:
        list[dict]: Filtrelenmiş, tekil, paylaşıma aday haber listesi.
    """
    log("=" * 55)
    log("HABER ÇEKME VE FİLTRELEME BAŞLIYOR")
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

    # ── ADIM 4: Zaman filtresi (akıllı) ──
    articles = apply_time_filter(articles)
    log(f"[ADIM 4] Zaman filtresi sonrası → {len(articles)} haber")

    if not articles:
        log("Zaman filtresinden geçen haber yok", "WARNING")
        return []

    # ── ADIM 5: Tekrar kontrolü ──
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
# MODÜL TESTİ (doğrudan çalıştırılırsa)
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
            summary_preview = article.get("summary", "")[:100]
            if summary_preview:
                log(f"     Özet     : {summary_preview}...")
    else:
        log("Paylaşıma aday haber bulunamadı", "WARNING")

    log("\n=== news_fetcher.py modül testi tamamlandı ===")
