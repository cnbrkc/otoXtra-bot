"""
core/helpers.py — Genel Yardımcı Fonksiyonlar (v2.0 — Trend + Grup Hafızası)

Değişiklikler v2.0:
  - generate_topic_fingerprint()  : Başlıktan normalize parmak izi üretir (YENİ)
  - is_topic_already_posted()     : Konu bazlı tekrar kontrolü (YENİ)
  - is_already_posted()           : URL + başlık + KONU kontrolü (güncellendi)
  - save_posted_news()            : 30 günlük temizlik (güncellendi)
  - Alan adı tutarsızlığı düzeltildi: hep "url" kullanılıyor
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
# 3. BAŞLIK BENZERLİĞİ
# ============================================================

def is_similar_title(title1: str, title2: str, threshold: float = None) -> bool:
    """İki haber başlığının benzerliğini kontrol eder.

    Eşik değeri settings.json'dan okunur.
    Verilmezse settings'e bakar, o da yoksa 0.80 kullanır.
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
# 4. KONU PARMAK İZİ (YENİ)
# ============================================================

# Türkçe stop word'ler — parmak izinden çıkarılır
_STOP_WORDS = {
    "bir", "bu", "şu", "ve", "ile", "için", "ama", "fakat",
    "ancak", "veya", "ya", "de", "da", "ki", "mı", "mi",
    "mu", "mü", "den", "dan", "ten", "tan", "nin", "nın",
    "nun", "nün", "ın", "in", "un", "ün", "deki", "daki",
    "teki", "taki", "gibi", "kadar", "daha", "çok",
    "olan", "oldu", "olarak", "üzere", "sonra",
    "önce", "göre", "karşı", "arasında", "içinde",
    "yeni", "büyük", "küçük", "ilk", "son", "en",
    "artık", "sadece", "bile", "her", "hiç", "tüm",
    "çıktı", "geldi", "oldu", "edildi", "yapıldı",
    "açıklandı", "duyuruldu", "tanıtıldı", "başladı",
}


def generate_topic_fingerprint(title: str) -> str:
    """Haber başlığından normalize edilmiş konu parmak izi üretir.

    Algoritma:
      1. Türkçe karakterleri ASCII'ye çevir
      2. Küçük harfe çevir
      3. Sayıları ve harf-dışı karakterleri temizle
      4. Stop word'leri çıkar
      5. Min 3 karakter olan kelimeleri al
      6. Sırala (sıra bağımsız karşılaştırma için)
      7. "-" ile birleştir

    Örnek:
      "Tesla Model S ve Model X Türkiye'de sonlandı!"
      → "model-model-sonlandi-tesla-turkiyede"

    Args:
        title: Haber başlığı.

    Returns:
        str: Normalize parmak izi. Başlık boşsa boş string.
    """
    if not title:
        return ""

    # Türkçe → ASCII dönüşüm tablosu
    tr_map = str.maketrans(
        "çğıöşüÇĞİÖŞÜ",
        "cgiosucgiosu"
    )

    normalized = title.lower()
    normalized = normalized.translate(tr_map)

    # Sadece harf ve rakam bırak (boşlukları koru)
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)

    # Kelimelere böl, filtrele
    words = normalized.split()
    keywords = sorted([
        w for w in words
        if len(w) >= 3 and w not in _STOP_WORDS
    ])

    return "-".join(keywords)


def _fingerprint_similarity(fp1: str, fp2: str) -> float:
    """İki parmak izinin benzerlik oranını döner (0.0-1.0)."""
    if not fp1 or not fp2:
        return 0.0
    return difflib.SequenceMatcher(None, fp1, fp2).ratio()


# ============================================================
# 5. ÇOK BOYUTLU DUPLICATE TESPİTİ
# ============================================================

def _extract_domain(url: str) -> str:
    """URL'den domain adını çıkarır."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def _extract_keywords_from_title(title: str, min_len: int = 4) -> set:
    """Başlıktan anlamlı anahtar kelimeleri çıkarır."""
    title_lower = title.replace("İ", "i").replace("I", "ı").lower()
    words = re.findall(r"[a-züğışçöa-z0-9]+", title_lower)
    return {
        w for w in words
        if len(w) >= min_len and w not in _STOP_WORDS
    }


def is_duplicate_article(article1: dict, article2: dict) -> bool:
    """İki haberin duplicate olup olmadığını çok boyutlu kontrol eder.

    3 yöntem sırayla uygulanır — biri True dönerse duplicate sayılır:
      1. URL tam eşleşme
      2. Başlık benzerliği (settings'ten eşik)
      3. Anahtar kelime örtüşmesi (settings'ten eşik)
    """
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

    # Yöntem 1: URL tam eşleşme
    if url1 and url2 and url1 == url2:
        return True

    # Yöntem 2: Başlık benzerliği
    if is_similar_title(title1, title2, threshold=title_threshold):
        return True

    # Yöntem 3: Anahtar kelime örtüşmesi
    if title1 and title2:
        keywords1 = _extract_keywords_from_title(title1)
        keywords2 = _extract_keywords_from_title(title2)
        if keywords1 and keywords2:
            intersection = keywords1 & keywords2
            union = keywords1 | keywords2
            if union:
                overlap_ratio = len(intersection) / len(union)
                if overlap_ratio >= keyword_threshold:
                    return True

    return False


# ============================================================
# 6. PAYLAŞILMIŞ HABERLER KAYDI (OKUMA)
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
# 7. PAYLAŞILMIŞ HABERLER KAYDI (YAZMA) — 30 GÜNLÜK TEMİZLİK
# ============================================================

# Kaç günlük geçmiş tutulacak
_HISTORY_DAYS = 30


def save_posted_news(data: dict) -> bool:
    """data/posted_news.json dosyasına yazar.

    Temizlik kuralları (her kayıtta çalışır):
      1. 30 günden eski kayıtlar silinir (tarih bazlı)
      2. Hâlâ 500'den fazla kayıt varsa en eski 200'ü sil (güvenlik)
      3. daily_counts'tan 30 günden eski tarihler temizlenir

    Bu sayede:
      - Dosya hiç şişmez
      - 30 gün içindeki haberler asla yeniden paylaşılmaz
      - Eski trend kayıtları bellekte yer tutmaz
    """
    posts = data.get("posts", [])
    daily_counts = data.get("daily_counts", {})

    now = get_turkey_now()
    cutoff = now - timedelta(days=_HISTORY_DAYS)
    cutoff_str = cutoff.isoformat()

    original_count = len(posts)
    cleaned_posts = []
    old_count = 0

    for post in posts:
        posted_at = post.get("posted_at", "")
        if not posted_at:
            # Tarih yoksa tut (güvenli taraf)
            cleaned_posts.append(post)
            continue

        try:
            from dateutil import parser as dateutil_parser
            post_dt = dateutil_parser.parse(posted_at)
            if post_dt.tzinfo is None:
                post_dt = post_dt.replace(tzinfo=timezone(timedelta(hours=3)))
            if post_dt < cutoff:
                old_count += 1
            else:
                cleaned_posts.append(post)
        except Exception:
            # Parse hatası → tut
            cleaned_posts.append(post)

    # Güvenlik: hâlâ 500+ varsa en eski 200'ü at
    if len(cleaned_posts) > 500:
        removed_extra = len(cleaned_posts) - 300
        cleaned_posts = cleaned_posts[-300:]
        log(f"⚠️ Güvenlik temizliği: {removed_extra} ek kayıt silindi")

    # daily_counts temizliği: 30 günden eski tarihleri sil
    cutoff_date_str = cutoff.strftime("%Y-%m-%d")
    cleaned_daily = {
        date: count
        for date, count in daily_counts.items()
        if date >= cutoff_date_str
    }

    data["posts"] = cleaned_posts
    data["daily_counts"] = cleaned_daily

    if old_count > 0:
        log(
            f"🧹 Temizlik: {original_count} → {len(cleaned_posts)} kayıt "
            f"({old_count} adet 30+ günlük kayıt silindi)"
        )

    filepath = os.path.join(get_project_root(), "data", "posted_news.json")
    return save_json(filepath, data)


# ============================================================
# 8. TEKRAR KONTROL — URL + BAŞLIK + KONU
# ============================================================

def is_topic_already_posted(
    fingerprint: str,
    posted_data: dict,
    similarity_threshold: float = 0.75,
) -> bool:
    """Konu parmak izine göre daha önce paylaşılıp paylaşılmadığını kontrol eder.

    Parmak izi benzerliği >= similarity_threshold ise "bu konu paylaşıldı" sayar.
    Bu sayede farklı URL ve başlıkla gelen ama aynı konuyu işleyen haberler
    engellenir.

    Args:
        fingerprint:          generate_topic_fingerprint() ile üretilen iz.
        posted_data:          get_posted_news() çıktısı.
        similarity_threshold: Benzerlik eşiği (varsayılan 0.75).

    Returns:
        bool: Konu daha önce paylaşıldıysa True.
    """
    if not fingerprint:
        return False

    posts = posted_data.get("posts", [])

    for post in posts:
        posted_fp = post.get("topic_fingerprint", "")
        if not posted_fp:
            continue

        similarity = _fingerprint_similarity(fingerprint, posted_fp)
        if similarity >= similarity_threshold:
            return True

    return False


def is_already_posted(url: str, title: str, posted_data: dict) -> bool:
    """Haberin daha önce paylaşılıp paylaşılmadığını kontrol eder.

    3 katmanlı kontrol:
      1. URL tam eşleşme (url veya original_url alanları)
      2. Başlık benzerliği (%80+)
      3. Konu parmak izi benzerliği (%75+)  ← YENİ

    Args:
        url:         Haberin URL'si.
        title:       Haberin başlığı.
        posted_data: get_posted_news() çıktısı.

    Returns:
        bool: Daha önce paylaşıldıysa True.
    """
    posts = posted_data.get("posts", [])

    # Yeni haberin parmak izi
    fingerprint = generate_topic_fingerprint(title)

    for post in posts:
        # Katman 1: URL eşleşme
        # "url" ve "original_url" her ikisini de kontrol et (eski/yeni format)
        posted_url = post.get("url", "") or post.get("original_url", "")
        if posted_url and url and posted_url == url:
            return True

        # Katman 2: Başlık benzerliği
        posted_title = post.get("title", "")
        if is_similar_title(title, posted_title):
            return True

        # Katman 3: Konu parmak izi
        posted_fp = post.get("topic_fingerprint", "")
        if posted_fp and fingerprint:
            similarity = _fingerprint_similarity(fingerprint, posted_fp)
            if similarity >= 0.75:
                return True

    return False


# ============================================================
# 9. GÜNLÜK POST SAYISI
# ============================================================

def get_today_post_count(posted_data: dict) -> int:
    """Bugün kaç post yapıldığını döner."""
    today = get_today_str()
    daily_counts = posted_data.get("daily_counts", {})
    return daily_counts.get(today, 0)


# ============================================================
# 10. AKILLI ZAMAN FİLTRESİ
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
# 11. RASTGELE GECİKME
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

    # Parmak izi testleri
    log("\n--- Parmak İzi Testleri ---")
    titles = [
        "Tesla Model S ve Model X Türkiye'de sonlandı!",
        "Tesla'nın Model S ve Model X üretimi resmen durdu",
        "BMW 5 Serisi yeni model tanıtıldı",
    ]
    fps = []
    for t in titles:
        fp = generate_topic_fingerprint(t)
        log(f"  '{t[:50]}'")
        log(f"  → '{fp}'")
        fps.append(fp)

    log(f"\nBenzerlik (Tesla1 vs Tesla2): {_fingerprint_similarity(fps[0], fps[1]):.2f}  → ~0.75+ olmalı")
    log(f"Benzerlik (Tesla1 vs BMW):   {_fingerprint_similarity(fps[0], fps[2]):.2f}  → düşük olmalı")

    # Başlık benzerliği testleri
    log("\n--- Başlık Benzerliği Testleri ---")
    t1 = "Yeni Toyota Corolla Türkiye'de Satışa Sunuldu"
    t2 = "Yeni Toyota Corolla Türkiye'de satışa sunuldu!"
    t3 = "BMW 3 Serisi Makyajlandı"
    log(f"Benzerlik (t1 vs t2): {is_similar_title(t1, t2)}  → True olmalı")
    log(f"Benzerlik (t1 vs t3): {is_similar_title(t1, t3)}  → False olmalı")

    # Duplicate tespiti testleri
    log("\n--- Duplicate Tespiti Testleri ---")
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
    log(f"Duplicate (Tesla vs Tesla): {is_duplicate_article(a1, a2)}  → True olmalı")
    log(f"Duplicate (Tesla vs BMW):   {is_duplicate_article(a1, a3)}  → False olmalı")

    # Konu bazlı tekrar kontrol
    log("\n--- Konu Bazlı Tekrar Kontrol ---")
    fake_posted = {
        "posts": [
            {
                "title": "Tesla Model S ve X üretimi durdu",
                "url": "https://site-a.com/tesla-s-x",
                "topic_fingerprint": generate_topic_fingerprint(
                    "Tesla Model S ve X üretimi durdu"
                ),
                "posted_at": now.isoformat(),
            }
        ],
        "daily_counts": {},
    }
    new_title = "Bir dönemin sonu: Tesla Model S ve Model X sonlandı"
    new_fp = generate_topic_fingerprint(new_title)
    log(f"Yeni haber FP: '{new_fp}'")
    result = is_already_posted(
        "https://site-b.com/tesla-farkli-link",
        new_title,
        fake_posted,
    )
    log(f"Konu daha önce paylaşıldı mı: {result}  → True olmalı")

    posted = get_posted_news()
    log(f"\nPaylaşılmış haber: {len(posted.get('posts', []))}")
    log(f"Bugünkü post: {get_today_post_count(posted)}")

    log("\n=== core/helpers.py modül testi tamamlandı ===")
