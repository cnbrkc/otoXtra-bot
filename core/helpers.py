"""
core/helpers.py — Genel Yardımcı Fonksiyonlar

otoXtra Facebook Botu için tüm modüller tarafından kullanılan
yardımcı fonksiyonları içerir.

İçerdiği fonksiyonlar:
  - get_turkey_now()      : Türkiye saatini döner
  - get_today_str()       : Bugünün tarihini string olarak döner
  - clean_html()          : HTML tag'lerini temizler
  - is_similar_title()    : Başlık benzerliği kontrol eder
  - get_posted_news()     : Paylaşılmış haberler kaydını okur
  - save_posted_news()    : Paylaşılmış haberler kaydını yazar
  - is_already_posted()   : Haberin daha önce paylaşılıp paylaşılmadığını kontrol eder
  - get_today_post_count(): Bugün kaç post yapıldığını döner
  - get_last_check_time() : Son kontrol zamanını okur
  - save_last_check_time(): Son kontrol zamanını yazar
  - random_delay()        : Rastgele süre bekler

Kullanım:
    from core.helpers import get_turkey_now, clean_html, get_posted_news

YANLIŞ kullanım (YAPMA):
    from src.utils import get_turkey_now
"""

import os
import time
import random
import difflib
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

from core.logger import log
from core.config_loader import get_project_root, load_json, save_json


# ============================================================
# 1. TARİH / SAAT
# ============================================================

def get_turkey_now() -> datetime:
    """Türkiye saatinde (UTC+3) şu anki zamanı döner.

    Returns:
        datetime: Türkiye saat diliminde şu anki zaman.
    """
    turkey_tz = timezone(timedelta(hours=3))
    return datetime.now(turkey_tz)


def get_today_str() -> str:
    """Bugünün tarihini YYYY-MM-DD formatında döner (Türkiye saati).

    Returns:
        str: Tarih stringi. Örnek: "2025-01-15"
    """
    return get_turkey_now().strftime("%Y-%m-%d")


# ============================================================
# 2. HTML TEMİZLEME
# ============================================================

def clean_html(html_text: str) -> str:
    """HTML tag'lerini temizleyip düz metin döner.

    BeautifulSoup kullanır. Parser olarak 'html.parser' kullanılır
    (Python ile birlikte gelir, ekstra kütüphane gerektirmez).

    Args:
        html_text: HTML içerikli metin.

    Returns:
        str: Tag'lerden arınmış düz metin. None/boş girişte boş string.
    """
    if not html_text:
        return ""

    soup = BeautifulSoup(html_text, "html.parser")
    return soup.get_text(separator=" ", strip=True)


# ============================================================
# 3. BAŞLIK BENZERLİĞİ
# ============================================================

def is_similar_title(title1: str, title2: str, threshold: float = 0.80) -> bool:
    """İki haber başlığının benzerliğini kontrol eder.

    difflib.SequenceMatcher kullanarak iki stringin benzerlik
    oranını hesaplar. Eşik değerin üstündeyse 'aynı haber'
    kabul edilir.

    Args:
        title1: Birinci başlık.
        title2: İkinci başlık.
        threshold: Benzerlik eşiği (0.0 - 1.0). Varsayılan 0.80.

    Returns:
        bool: Benzerlik oranı >= threshold ise True.
    """
    if not title1 or not title2:
        return False

    clean1 = title1.lower().strip()
    clean2 = title2.lower().strip()

    ratio = difflib.SequenceMatcher(None, clean1, clean2).ratio()
    return ratio >= threshold


# ============================================================
# 4. PAYLAŞILMIŞ HABERLER KAYDI (OKUMA)
# ============================================================

def get_posted_news() -> dict:
    """data/posted_news.json dosyasını okur.

    Dosya yoksa veya bozuksa varsayılan yapıyı döner.
    Eksik anahtarları otomatik ekler.

    Returns:
        dict: Yapı → {
            "posts": [...],
            "daily_counts": {"2025-01-15": 3, ...},
            "last_check_time": "2025-01-15T14:30:00+03:00"
        }
    """
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
# 5. PAYLAŞILMIŞ HABERLER KAYDI (YAZMA)
# ============================================================

def save_posted_news(data: dict) -> bool:
    """data/posted_news.json dosyasına yazar. Otomatik temizlik yapar.

    Eğer posts listesi 500'den fazla kayıt içeriyorsa,
    en eski kayıtları silerek sadece son 300 kaydı tutar.

    Args:
        data: Kaydedilecek dict.

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    if "posts" in data and isinstance(data["posts"], list):
        post_count = len(data["posts"])
        if post_count > 500:
            data["posts"] = data["posts"][-300:]
            log(f"Eski kayıtlar temizlendi: {post_count} → 300", "INFO")

    filepath = os.path.join(get_project_root(), "data", "posted_news.json")
    return save_json(filepath, data)


# ============================================================
# 6. TEKRAR KONTROL
# ============================================================

def is_already_posted(url: str, title: str, posted_data: dict) -> bool:
    """Haberin daha önce paylaşılıp paylaşılmadığını kontrol eder.

    İki yöntemle kontrol yapar:
    1. URL tam eşleşmesi
    2. Başlık benzerliği (%80 üzeri)

    Args:
        url: Kontrol edilecek haberin URL'si.
        title: Kontrol edilecek haberin başlığı.
        posted_data: get_posted_news() ile alınan dict.

    Returns:
        bool: Daha önce paylaşıldıysa True, ilk kez görülüyorsa False.
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
# 7. GÜNLÜK POST SAYISI
# ============================================================

def get_today_post_count(posted_data: dict) -> int:
    """Bugün kaç post yapıldığını döner.

    Args:
        posted_data: get_posted_news() ile alınan dict.

    Returns:
        int: Bugünkü post sayısı. Kayıt yoksa 0.
    """
    today = get_today_str()
    daily_counts = posted_data.get("daily_counts", {})
    return daily_counts.get(today, 0)


# ============================================================
# 8. AKILLI ZAMAN FİLTRESİ
# ============================================================

def get_last_check_time(posted_data: dict) -> datetime:
    """Son kontrol zamanını okur.

    posted_news.json'daki "last_check_time" alanından okur.
    Alan yoksa veya parse edilemezse varsayılan olarak
    6 saat öncesini döner (ilk çalışma güvenliği).

    Args:
        posted_data: get_posted_news() ile alınan dict.

    Returns:
        datetime: Son kontrol zamanı (timezone-aware, UTC+3).
    """
    turkey_tz = timezone(timedelta(hours=3))
    default_fallback = get_turkey_now() - timedelta(hours=6)

    raw_value = posted_data.get("last_check_time")

    if not raw_value or not isinstance(raw_value, str):
        log("last_check_time bulunamadı → varsayılan 6 saat önce", "INFO")
        return default_fallback

    try:
        parsed = datetime.fromisoformat(raw_value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=turkey_tz)

        now = get_turkey_now()
        if parsed > now:
            log("last_check_time gelecekte → şu an olarak düzeltildi", "WARNING")
            return now - timedelta(hours=1)

        max_age = now - timedelta(hours=48)
        if parsed < max_age:
            log("last_check_time 48 saatten eski → 6 saat öncesine sıfırlandı", "WARNING")
            return default_fallback

        return parsed

    except (ValueError, TypeError) as e:
        log(f"last_check_time parse hatası: {e} → varsayılan 6 saat önce", "WARNING")
        return default_fallback


def save_last_check_time(posted_data: dict) -> None:
    """Şu anki zamanı last_check_time olarak posted_data'ya yazar.

    DİKKAT: Bu fonksiyon sadece dict'e yazar, dosyaya KAYDETMEZ.
    Dosyaya kaydetmek için ardından save_posted_news() çağrılmalıdır.

    Kullanım:
        posted_data = get_posted_news()
        save_last_check_time(posted_data)
        save_posted_news(posted_data)  ← dosyaya kaydeder

    Args:
        posted_data: get_posted_news() ile alınan dict.
    """
    now = get_turkey_now()
    posted_data["last_check_time"] = now.isoformat()
    log(f"last_check_time güncellendi: {now.strftime('%Y-%m-%d %H:%M:%S')}")


# ============================================================
# 9. RASTGELE GECİKME
# ============================================================

def random_delay(max_minutes: int) -> None:
    """Rastgele bir süre bekler. Bot algılanmasını önlemeye yarar.

    0 ile max_minutes dakika arasında rastgele bir süre seçer
    ve o kadar bekler.

    Args:
        max_minutes: Maksimum bekleme süresi (dakika cinsinden).
                     0 veya negatifse bekleme yapmaz.
    """
    if max_minutes <= 0:
        return

    total_seconds = random.randint(0, max_minutes * 60)
    minutes = total_seconds // 60
    seconds = total_seconds % 60

    log(f"Rastgele gecikme: {minutes} dakika {seconds} saniye")
    time.sleep(total_seconds)


# ============================================================
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ============================================================

if __name__ == "__main__":
    log("=== core/helpers.py modül testi başlıyor ===")

    # Türkiye saati testi
    now = get_turkey_now()
    log(f"Türkiye saati: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Bugün: {get_today_str()}")

    # Başlık benzerliği testi
    t1 = "Yeni Toyota Corolla Türkiye'de Satışa Sunuldu"
    t2 = "Yeni Toyota Corolla Türkiye'de satışa sunuldu!"
    t3 = "BMW 3 Serisi Makyajlandı"
    log(f"Benzerlik (t1 vs t2): {is_similar_title(t1, t2)}  → True olmalı")
    log(f"Benzerlik (t1 vs t3): {is_similar_title(t1, t3)}  → False olmalı")

    # HTML temizleme testi
    html_sample = "<p>Bu bir <b>test</b> metnidir.</p><br><a href='#'>Link</a>"
    cleaned = clean_html(html_sample)
    log(f"HTML temizleme: '{cleaned}'")

    # Posted news testi
    posted = get_posted_news()
    log(f"Paylaşılmış haber sayısı: {len(posted.get('posts', []))}")
    log(f"Bugünkü post sayısı: {get_today_post_count(posted)}")

    # Akıllı zaman filtresi testi
    last_check = get_last_check_time(posted)
    log(f"Son kontrol zamanı: {last_check.strftime('%Y-%m-%d %H:%M:%S')}")

    log("=== core/helpers.py modül testi tamamlandı ===")
