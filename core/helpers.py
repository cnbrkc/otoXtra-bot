"""
core/helpers.py — Genel Yardımcı Fonksiyonlar

otoXtra Facebook Botu için tüm modüller tarafından kullanılan
yardımcı fonksiyonları içerir.

İçerdiği fonksiyonlar:
  - get_turkey_now()        : Türkiye saatini döner
  - get_today_str()         : Bugünün tarihini string olarak döner
  - clean_html()            : HTML tag'lerini temizler
  - is_similar_title()      : Başlık benzerliği kontrol eder (geliştirildi)
  - is_duplicate_article()  : Çok boyutlu duplicate tespiti (YENİ)
  - get_posted_news()       : Paylaşılmış haberler kaydını okur
  - save_posted_news()      : Paylaşılmış haberler kaydını yazar
  - is_already_posted()     : Haberin daha önce paylaşılıp paylaşılmadığını kontrol eder
  - get_today_post_count()  : Bugün kaç post yapıldığını döner
  - get_last_check_time()   : Son kontrol zamanını okur
  - save_last_check_time()  : Son kontrol zamanını yazar
  - random_delay()          : Rastgele süre bekler

Kullanım:
    from core.helpers import get_turkey_now, clean_html, get_posted_news

YANLIŞ kullanım (YAPMA):
    from src.utils import get_turkey_now
"""

import os
import re
import time
import random
import difflib
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from core.logger import log
from core.config_loader import get_project_root, load_json, save_json, load_config


# ============================================================
# 1. TARİH / SAAT
# ============================================================

def get_turkey_now() -> datetime:
    """Türkiye saatinde (UTC+3) şu anki zamanı döner."""
    turkey_tz = timezone(timedelta(hours=3))
    return datetime.now(turkey_tz)


def get_today_str() -> str:
    """Bugünün tarihini YYYY-MM-DD formatında döner (Türkiye saati)."""
    return get_turkey_now().strftime("%Y-%m-%d")


# ============================================================
# 2. HTML TEMİZLEME
# ============================================================

def clean_html(html_text: str) -> str:
    """HTML tag'lerini temizleyip düz metin döner."""
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    return soup.get_text(separator=" ", strip=True)


# ============================================================
# 3. BAŞLIK BENZERLİĞİ (GELİŞTİRİLDİ)
# ============================================================

def is_similar_title(title1: str, title2: str, threshold: float = None) -> bool:
    """İki haber başlığının benzerliğini kontrol eder.

    Eşik değeri settings.json'dan okunur.
    Verilmezse settings'e bakar, o da yoksa 0.80 kullanır.

    Args:
        title1:    Birinci başlık.
        title2:    İkinci başlık.
        threshold: Benzerlik eşiği (0.0-1.0). None ise settings'ten okur.

    Returns:
        bool: Benzerlik oranı >= threshold ise True.
    """
    if not title1 or not title2:
        return False

    if threshold is None:
        try:
            settings = load_config("settings")
            threshold = settings.get("duplicate_detection", {}).get(
                "title_similarity_threshold", 0.80
            )
        except Exception:
            threshold = 0.80

    clean1 = title1.lower().strip()
    clean2 = title2.lower().strip()

    ratio = difflib.SequenceMatcher(None, clean1, clean2).ratio()
    return ratio >= threshold


# ============================================================
# 4. ÇOK BOYUTLU DUPLICATE TESPİTİ (YENİ)
# ============================================================

def _extract_domain(url: str) -> str:
    """URL'den domain adını çıkarır.

    Args:
        url: Tam URL.

    Returns:
        str: Domain adı. Örnek: "donanimhaber.com"
             Hata durumunda boş string.
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # www. önekini kaldır
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _extract_keywords_from_title(title: str, min_len: int = 4) -> set:
    """Başlıktan anlamlı anahtar kelimeleri çıkarır.

    Türkçe stop word'leri ve kısa kelimeleri filtreler.

    Args:
        title:   Haber başlığı.
        min_len: Minimum kelime uzunluğu.

    Returns:
        set: Anlamlı kelimeler kümesi.
    """
    # Türkçe stop word'ler (anlamsız bağlaçlar, edatlar)
    stop_words = {
        "bir", "bu", "şu", "ve", "ile", "için", "ama", "fakat",
        "ancak", "veya", "ya", "de", "da", "ki", "mı", "mi",
        "mu", "mü", "den", "dan", "ten", "tan", "nin", "nın",
        "nun", "nün", "ın", "in", "un", "ün", "deki", "daki",
        "teki", "taki", "gibi", "kadar", "daha", "çok", "çok",
        "olan", "olan", "oldu", "olarak", "üzere", "sonra",
        "önce", "göre", "karşı", "arasında", "içinde",
    }

    # Sadece harf ve rakam bırak, küçük harfe çevir
    title_lower = title.replace("İ", "i").replace("I", "ı").lower()
    words = re.findall(r"[a-züğışçöa-z0-9]+", title_lower)

    return {
        w for w in words
        if len(w) >= min_len and w not in stop_words
    }


def is_duplicate_article(article1: dict, article2: dict) -> bool:
    """İki haberin duplicate olup olmadığını çok boyutlu kontrol eder.

    3 yöntem sırayla uygulanır — biri True dönerse duplicate sayılır:

    Yöntem 1 — URL Tam Eşleşme:
        Aynı URL → kesin duplicate

    Yöntem 2 — Domain + Başlık Benzerliği:
        Aynı domain + %title_similarity_threshold benzer başlık
        → muhtemelen aynı haber farklı bölümde

    Yöntem 3 — Anahtar Kelime Örtüşmesi:
        İki başlıktaki anlamlı kelimelerin %keyword_overlap_threshold'u
        örtüşüyorsa → aynı konuyu işliyor, duplicate say

    Eşikler settings.json'dan okunur:
        settings.duplicate_detection.title_similarity_threshold  (varsayılan: 0.80)
        settings.duplicate_detection.keyword_overlap_threshold   (varsayılan: 0.70)

    Args:
        article1: Birinci haber dict'i.
        article2: İkinci haber dict'i.

    Returns:
        bool: Duplicate ise True.
    """
    # Ayarları oku
    try:
        settings = load_config("settings")
        dup_settings = settings.get("duplicate_detection", {})
        title_threshold = dup_settings.get("title_similarity_threshold", 0.80)
        keyword_threshold = dup_settings.get("keyword_overlap_threshold", 0.70)
    except Exception:
        title_threshold = 0.80
        keyword_threshold = 0.70

    url1 = article1.get("link", "")
    url2 = article2.get("link", "")
    title1 = article1.get("title", "")
    title2 = article2.get("title", "")

    # ── Yöntem 1: URL tam eşleşme ──
    if url1 and url2 and url1 == url2:
        return True

    # ── Yöntem 2: Başlık benzerliği ──
    if is_similar_title(title1, title2, threshold=title_threshold):
        return True

    # ── Yöntem 3: Anahtar kelime örtüşmesi ──
    if title1 and title2:
        keywords1 = _extract_keywords_from_title(title1)
        keywords2 = _extract_keywords_from_title(title2)

        if keywords1 and keywords2:
            # Jaccard benzerliği: kesişim / birleşim
            intersection = keywords1 & keywords2
            union = keywords1 | keywords2

            if union:
                overlap_ratio = len(intersection) / len(union)
                if overlap_ratio >= keyword_threshold:
                    return True

    return False


# ============================================================
# 5. PAYLAŞILMIŞ HABERLER KAYDI (OKUMA)
# ============================================================

def get_posted_news() -> dict:
    """data/posted_news.json dosyasını okur."""
    filepath = os.path.join(get_project_root(), "data", "posted_news.json")
    data = load_json(filepath)

    if not data or not isinstance(data, dict):
        return {"posts": [], "daily_counts": {}}

    if "posts" not in data or not isinstance(data.get("posts"), list):
        data["posts"] = []

    if "daily_counts" not in data or not isinstance(data.get("daily_counts"), dict):
        data["daily_counts"] = {}

    return data


# ============================================================
# 6. PAYLAŞILMIŞ HABERLER KAYDI (YAZMA)
# ============================================================

def save_posted_news(data: dict) -> bool:
    """data/posted_news.json dosyasına yazar. Otomatik temizlik yapar."""
    if "posts" in data and isinstance(data["posts"], list):
        post_count = len(data["posts"])
        if post_count > 500:
            data["posts"] = data["posts"][-300:]
            log(f"Eski kayıtlar temizlendi: {post_count} → 300")

    filepath = os.path.join(get_project_root(), "data", "posted_news.json")
    return save_json(filepath, data)


# ============================================================
# 7. TEKRAR KONTROL
# ============================================================

def is_already_posted(url: str, title: str, posted_data: dict) -> bool:
    """Haberin daha önce paylaşılıp paylaşılmadığını kontrol eder.

    URL eşleşmesi veya başlık benzerliği (%80+) ile kontrol eder.
    """
    posts = posted_data.get("posts", [])

    for post in posts:
        posted_url = post.get("url", "")
        if posted_url and url and posted_url == url:
            return True

        posted_title = post.get("title", "")
        if is_similar_title(title, posted_title):
            return True

    return False


# ============================================================
# 8. GÜNLÜK POST SAYISI
# ============================================================

def get_today_post_count(posted_data: dict) -> int:
    """Bugün kaç post yapıldığını döner."""
    today = get_today_str()
    daily_counts = posted_data.get("daily_counts", {})
    return daily_counts.get(today, 0)


# ============================================================
# 9. AKILLI ZAMAN FİLTRESİ
# ============================================================

def get_last_check_time(posted_data: dict) -> datetime:
    """Son kontrol zamanını okur."""
    turkey_tz = timezone(timedelta(hours=3))
    default_fallback = get_turkey_now() - timedelta(hours=6)

    raw_value = posted_data.get("last_check_time")

    if not raw_value or not isinstance(raw_value, str):
        log("last_check_time bulunamadı → varsayılan 6 saat önce")
        return default_fallback

    try:
        parsed = datetime.fromisoformat(raw_value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=turkey_tz)

        now = get_turkey_now()
        if parsed > now:
            log("last_check_time gelecekte → düzeltildi", "WARNING")
            return now - timedelta(hours=1)

        max_age = now - timedelta(hours=48)
        if parsed < max_age:
            log("last_check_time 48s+ eski → sıfırlandı", "WARNING")
            return default_fallback

        return parsed

    except (ValueError, TypeError) as exc:
        log(f"last_check_time parse hatası: {exc} → varsayılan", "WARNING")
        return default_fallback


def save_last_check_time(posted_data: dict) -> None:
    """Şu anki zamanı last_check_time olarak posted_data'ya yazar.

    Dosyaya KAYDETMEZ. Ardından save_posted_news() çağrılmalıdır.
    """
    now = get_turkey_now()
    posted_data["last_check_time"] = now.isoformat()
    log(f"last_check_time güncellendi: {now.strftime('%Y-%m-%d %H:%M:%S')}")


# ============================================================
# 10. RASTGELE GECİKME
# ============================================================

def random_delay(max_minutes: int) -> None:
    """Rastgele bir süre bekler."""
    if max_minutes <= 0:
        return

    total_seconds = random.randint(0, max_minutes * 60)
    minutes = total_seconds // 60
    seconds = total_seconds % 60

    log(f"Rastgele gecikme: {minutes} dakika {seconds} saniye")
    time.sleep(total_seconds)


# ============================================================
# MODÜL TESTİ
# ============================================================

if __name__ == "__main__":
    log("=== core/helpers.py modül testi başlıyor ===")

    now = get_turkey_now()
    log(f"Türkiye saati: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Bugün: {get_today_str()}")

    # Başlık benzerliği testleri
    t1 = "Yeni Toyota Corolla Türkiye'de Satışa Sunuldu"
    t2 = "Yeni Toyota Corolla Türkiye'de satışa sunuldu!"
    t3 = "BMW 3 Serisi Makyajlandı"
    log(f"Benzerlik (t1 vs t2): {is_similar_title(t1, t2)}  → True olmalı")
    log(f"Benzerlik (t1 vs t3): {is_similar_title(t1, t3)}  → False olmalı")

    # Duplicate tespiti testleri
    a1 = {
        "title": "Tesla Model 3 Türkiye fiyatı açıklandı",
        "link": "https://donanimhaber.com/tesla-model-3-fiyat",
    }
    a2 = {
        "title": "Tesla Model 3'ün Türkiye satış fiyatı belli oldu",
        "link": "https://motor1.com/tesla-model-3-turkiye",
    }
    a3 = {
        "title": "BMW 5 Serisi yeni model tanıtıldı",
        "link": "https://motor1.com/bmw-5-serisi",
    }

    log(f"\nDuplicate testi (Tesla vs Tesla): {is_duplicate_article(a1, a2)}  → True olmalı")
    log(f"Duplicate testi (Tesla vs BMW):   {is_duplicate_article(a1, a3)}  → False olmalı")

    # Keyword çıkarma testi
    keywords = _extract_keywords_from_title("Tesla Model 3 Türkiye fiyatı açıklandı")
    log(f"\nKeyword çıkarma: {keywords}")

    # HTML temizleme
    html_sample = "<p>Bu bir <b>test</b> metnidir.</p>"
    log(f"\nHTML temizleme: '{clean_html(html_sample)}'")

    posted = get_posted_news()
    log(f"\nPaylaşılmış haber: {len(posted.get('posts', []))}")
    log(f"Bugünkü post: {get_today_post_count(posted)}")

    log("=== core/helpers.py modül testi tamamlandı ===")
