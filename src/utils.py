"""
src/utils.py — Yardımcı Fonksiyonlar

otoXtra Facebook Botu için tüm modüller tarafından kullanılan
ortak yardımcı fonksiyonları içerir.

İçerdiği fonksiyonlar:
  - get_project_root()    : Proje kök dizinini döner
  - load_json()           : JSON dosyası okur
  - save_json()           : JSON dosyasına yazar
  - load_config()         : Config klasöründen ayar okur
  - get_turkey_now()      : Türkiye saatini döner
  - get_today_str()       : Bugünün tarihini string olarak döner
  - log()                 : Formatlı log yazar
  - is_similar_title()    : Başlık benzerliği kontrol eder
  - clean_html()          : HTML tag'lerini temizler
  - get_posted_news()     : Paylaşılmış haberler kaydını okur
  - save_posted_news()    : Paylaşılmış haberler kaydını yazar
  - is_already_posted()   : Haberin daha önce paylaşılıp paylaşılmadığını kontrol eder
  - get_today_post_count(): Bugün kaç post yapıldığını döner
  - get_last_check_time() : Son kontrol zamanını okur (Akıllı Zaman Filtresi)
  - save_last_check_time(): Son kontrol zamanını yazar (Akıllı Zaman Filtresi)
  - random_delay()        : Rastgele süre bekler

Diğer modüller bu dosyayı şöyle import eder:
    from utils import load_config, log, get_project_root

YANLIŞ kullanım (YAPMA):
    from src.utils import load_config
"""

import os
import json
import time
import random
import difflib
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup


# ============================================================
# 1. PROJE KÖK DİZİNİ
# ============================================================

def get_project_root() -> str:
    """Proje kök dizininin mutlak yolunu döner.

    Bu dosya (utils.py) src/ klasöründe bulunur.
    Bir üst dizin proje kök dizinidir (otoXtra-bot/).

    Returns:
        str: Proje kök dizininin mutlak yolu.
             Örnek: /home/runner/work/otoXtra-bot/otoXtra-bot
    """
    # __file__        → .../otoXtra-bot/src/utils.py
    # dirname 1. kez  → .../otoXtra-bot/src
    # dirname 2. kez  → .../otoXtra-bot          ← proje kökü
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# 2. JSON OKUMA / YAZMA
# ============================================================

def load_json(filepath: str) -> dict:
    """JSON dosyası okur ve dict olarak döner.

    Args:
        filepath: Okunacak JSON dosyasının tam yolu.

    Returns:
        dict: JSON içeriği. Dosya yoksa veya bozuksa boş dict {}.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log(f"JSON dosyası bulunamadı: {filepath}", "WARNING")
        return {}
    except json.JSONDecodeError as e:
        log(f"JSON parse hatası ({filepath}): {e}", "ERROR")
        return {}
    except Exception as e:
        log(f"JSON okuma hatası ({filepath}): {e}", "ERROR")
        return {}


def save_json(filepath: str, data: dict) -> bool:
    """Dict'i JSON dosyasına yazar.

    Yazmadan önce hedef klasörün var olduğundan emin olur.
    Klasör yoksa otomatik oluşturur.

    Args:
        filepath: Yazılacak JSON dosyasının tam yolu.
        data: Kaydedilecek dict.

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    try:
        # Klasör yoksa oluştur (örn: data/ klasörü ilk çalışmada olmayabilir)
        directory = os.path.dirname(filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log(f"JSON yazma hatası ({filepath}): {e}", "ERROR")
        return False


# ============================================================
# 3. CONFIG YÜKLEME
# ============================================================

def load_config(config_name: str) -> dict:
    """Config klasöründen belirtilen JSON ayar dosyasını okur.

    Proje kök dizinindeki config/ klasöründen dosyayı bulur.
    Bu sayede bot hangi dizinden çalıştırılırsa çalıştırılsın
    doğru config dosyasını okur.

    Args:
        config_name: Dosya adı (uzantısız).
                     Örnek: "settings" → config/settings.json

    Returns:
        dict: Config içeriği. Dosya yoksa boş dict {}.
    """
    filepath = os.path.join(get_project_root(), "config", f"{config_name}.json")
    data = load_json(filepath)
    if not data:
        log(f"Config yüklenemedi: {config_name}.json", "WARNING")
    return data


# ============================================================
# 4. TARİH / SAAT
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
# 5. LOG
# ============================================================

def log(message: str, level: str = "INFO") -> None:
    """Konsola formatlı log yazar. GitHub Actions loglarında görünür.

    Format: [2025-01-15 14:32:00] [INFO] Mesaj metni

    Args:
        message: Log mesajı.
        level: Seviye — "INFO", "WARNING" veya "ERROR".
    """
    now_str = get_turkey_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] [{level}] {message}")


# ============================================================
# 6. BAŞLIK BENZERLİĞİ
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

    # Karşılaştırma öncesi küçük harfe çevir ve boşlukları temizle
    clean1 = title1.lower().strip()
    clean2 = title2.lower().strip()

    ratio = difflib.SequenceMatcher(None, clean1, clean2).ratio()
    return ratio >= threshold


# ============================================================
# 7. HTML TEMİZLEME
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

    # 'html.parser' kullan — lxml veya başka parser KULLANMA
    soup = BeautifulSoup(html_text, "html.parser")
    return soup.get_text(separator=" ", strip=True)


# ============================================================
# 8. PAYLAŞILMIŞ HABERLER KAYDI (OKUMA)
# ============================================================

def get_posted_news() -> dict:
    """data/posted_news.json dosyasını okur.

    Dosya yoksa veya bozuksa varsayılan yapıyı döner.
    Eksik anahtarları otomatik ekler.

    Returns:
        dict: Yapı → {
            "posts": [...],
            "daily_counts": {"2025-01-15": 3, ...},
            "last_check_time": "2025-01-15T14:30:00+03:00"  (opsiyonel)
        }
    """
    filepath = os.path.join(get_project_root(), "data", "posted_news.json")
    data = load_json(filepath)

    # Varsayılan yapı — eksik anahtarları tamamla
    if not data or not isinstance(data, dict):
        return {"posts": [], "daily_counts": {}}

    if "posts" not in data or not isinstance(data.get("posts"), list):
        data["posts"] = []

    if "daily_counts" not in data or not isinstance(data.get("daily_counts"), dict):
        data["daily_counts"] = {}

    # last_check_time alanı opsiyoneldir, yoksa eklenmez
    # (get_last_check_time() bu durumu kendi yönetir)

    return data


# ============================================================
# 9. PAYLAŞILMIŞ HABERLER KAYDI (YAZMA)
# ============================================================

def save_posted_news(data: dict) -> bool:
    """data/posted_news.json dosyasına yazar. Otomatik temizlik yapar.

    Eğer posts listesi 500'den fazla kayıt içeriyorsa,
    en eski kayıtları silerek sadece son 300 kaydı tutar.
    Bu sayede dosya süresiz büyümez.

    Args:
        data: Kaydedilecek dict. Yapı → {"posts": [...], "daily_counts": {...}}

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    # Otomatik temizlik: 500'den fazla kayıt varsa son 300'ü tut
    if "posts" in data and isinstance(data["posts"], list):
        post_count = len(data["posts"])
        if post_count > 500:
            data["posts"] = data["posts"][-300:]  # Son 300 kayıt (en yeniler)
            log(f"Eski kayıtlar temizlendi: {post_count} → 300", "INFO")

    filepath = os.path.join(get_project_root(), "data", "posted_news.json")
    return save_json(filepath, data)


# ============================================================
# 10. TEKRAR KONTROL
# ============================================================

def is_already_posted(url: str, title: str, posted_data: dict) -> bool:
    """Haberin daha önce paylaşılıp paylaşılmadığını kontrol eder.

    İki yöntemle kontrol yapar:
    1. URL tam eşleşmesi
    2. Başlık benzerliği (%80 üzeri — aynı haber farklı kaynaktan gelebilir)

    Args:
        url: Kontrol edilecek haberin URL'si.
        title: Kontrol edilecek haberin başlığı.
        posted_data: get_posted_news() ile alınan dict.

    Returns:
        bool: Daha önce paylaşıldıysa True, ilk kez görülüyorsa False.
    """
    posts = posted_data.get("posts", [])

    for post in posts:
        # Yöntem 1: URL eşleşmesi (kesin tekrar)
        posted_url = post.get("url", "")
        if posted_url and url and posted_url == url:
            return True

        # Yöntem 2: Başlık benzerliği (%80+, muhtemel tekrar)
        posted_title = post.get("title", "")
        if is_similar_title(title, posted_title):
            return True

    return False


# ============================================================
# 11. GÜNLÜK POST SAYISI
# ============================================================

def get_today_post_count(posted_data: dict) -> int:
    """Bugün kaç post yapıldığını döner.

    posted_data["daily_counts"] sözlüğünden bugünün tarihine
    karşılık gelen değeri okur.

    Args:
        posted_data: get_posted_news() ile alınan dict.

    Returns:
        int: Bugünkü post sayısı. Kayıt yoksa 0.
    """
    today = get_today_str()
    daily_counts = posted_data.get("daily_counts", {})
    return daily_counts.get(today, 0)






# ============================================================
# 12. AKILLI ZAMAN FİLTRESİ
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
        # ISO format parse: "2025-01-15T14:30:00+03:00"
        parsed = datetime.fromisoformat(raw_value)

        # Timezone bilgisi yoksa UTC+3 olarak kabul et
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=turkey_tz)

        # Mantık kontrolü: gelecekte olamaz
        now = get_turkey_now()
        if parsed > now:
            log("last_check_time gelecekte → şu an olarak düzeltildi", "WARNING")
            return now - timedelta(hours=1)

        # Mantık kontrolü: 48 saatten eski olamaz (çok eski veriyle uğraşma)
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
        save_posted_news(posted_data)  # ← dosyaya kaydeder

    Args:
        posted_data: get_posted_news() ile alınan dict.
                     Bu dict'e "last_check_time" anahtarı eklenir/güncellenir.
    """
    now = get_turkey_now()
    posted_data["last_check_time"] = now.isoformat()
    log(f"last_check_time güncellendi: {now.strftime('%Y-%m-%d %H:%M:%S')}")


# ============================================================
# 13. RASTGELE GECİKME
# ============================================================

def random_delay(max_minutes: int) -> None:
    """Rastgele bir süre bekler. Bot algılanmasını önlemeye yarar.

    0 ile max_minutes dakika arasında rastgele bir süre seçer
    ve o kadar bekler. Bekleme süresini loglar.

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
    # Bu dosya doğrudan çalıştırılırsa basit testler yapar
    log("=== utils.py modül testi başlıyor ===")

    # Proje kökü testi
    root = get_project_root()
    log(f"Proje kökü: {root}")



    # Türkiye saati testi
    now = get_turkey_now()
    log(f"Türkiye saati: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Bugün: {get_today_str()}")

    # Config yükleme testi

    settings = load_config("settings")
    if settings:
        log(f"settings.json yüklendi — {len(settings)} anahtar")
    else:
        log("settings.json bulunamadı veya boş", "WARNING")

    scoring = load_config("scoring")
    if scoring:
        log(f"scoring.json yüklendi — {len(scoring)} anahtar")
    else:
        log("scoring.json bulunamadı veya boş", "WARNING")

    prompts = load_config("prompts")
    if prompts:
        log(f"prompts.json yüklendi — {len(prompts)} anahtar")
    else:
        log("prompts.json bulunamadı veya boş", "WARNING")

    # Başlık benzerliği testi
    t1 = "Yeni Toyota Corolla Türkiye'de Satışa Sunuldu"
    t2 = "Yeni Toyota Corolla Türkiye'de satışa sunuldu!"
    t3 = "BMW 3 Serisi Makyajlandı"
    log(f"Benzerlik (t1 vs t2): {is_similar_title(t1, t2)}  (True olmalı)")
    log(f"Benzerlik (t1 vs t3): {is_similar_title(t1, t3)}  (False olmalı)")

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

    log("=== utils.py modül testi tamamlandı ===")
