"""
facebook_poster.py — Facebook Sayfa Paylaşım Modülü

Bu modül Facebook Graph API kullanarak otoXtra Facebook sayfasına
otomatik paylaşım yapar ve paylaşım kaydını tutar.

İki tür paylaşım desteklenir:
  1. Fotoğraflı paylaşım (öncelikli) → /{page_id}/photos
  2. Sadece metin paylaşım (yedek)   → /{page_id}/feed

Her başarılı paylaşım data/posted_news.json dosyasına kaydedilir.
Bu sayede aynı haber iki kez paylaşılmaz ve günlük istatistikler tutulur.

Facebook Graph API v19.0 kullanılır (2024-2025 için güncel).
Facebook yaklaşık her 2 yılda eski API versiyonlarını kapatır.
Hata alınırsa bu dosyadaki "v19.0" ifadelerini güncel versiyonla değiştir.

Ortam değişkenleri (GitHub Secrets'ta saklanır):
  - FB_PAGE_ID       → Facebook sayfa ID numarası
  - FB_ACCESS_TOKEN  → Uzun süreli sayfa erişim tokenı

Kullandığı modüller:
  - utils.py → load_config(), log(), get_posted_news(), save_posted_news(),
               get_today_str(), get_turkey_now()

Kullandığı dosyalar:
  - config/settings.json  → facebook ayarları
  - data/posted_news.json → paylaşım kaydı (okuma + yazma)
"""

import os
import time
from typing import Optional

import requests

from utils import (
    load_config,
    log,
    get_posted_news,
    save_posted_news,
    get_today_str,
    get_turkey_now,
)


# ──────────────────────────────────────────────
# Sabitler
# ──────────────────────────────────────────────

# Facebook Graph API base URL'i
# API versiyonu güncellenecekse sadece burayı değiştirmek yeterli
_FB_API_VERSION: str = "v19.0"
_FB_BASE_URL: str = f"https://graph.facebook.com/{_FB_API_VERSION}"

# HTTP istek zaman aşımı (saniye)
_REQUEST_TIMEOUT: int = 30

# Başarısız paylaşımda tekrar denemeden önce bekleme süresi (saniye)
_RETRY_DELAY: int = 5


# ──────────────────────────────────────────────
# Yardımcı (private) fonksiyon
# ──────────────────────────────────────────────

def _get_fb_credentials() -> tuple[str, str]:
    """
    Facebook API kimlik bilgilerini ortam değişkenlerinden okur.

    Returns:
        (page_id, access_token) tuple'ı.
        Bulunamazsa boş string döner.
    """
    page_id: str = os.environ.get("FB_PAGE_ID", "")
    access_token: str = os.environ.get("FB_ACCESS_TOKEN", "")

    if not page_id:
        log("❌ FB_PAGE_ID ortam değişkeni bulunamadı!", "ERROR")

    if not access_token:
        log("❌ FB_ACCESS_TOKEN ortam değişkeni bulunamadı!", "ERROR")

    return page_id, access_token


def _extract_post_id(fb_response: dict) -> str:
    """
    Facebook API yanıtından post ID'sini çıkarır.

    Facebook bazen "id", bazen "post_id" alanı döner.
    Her iki durumu da kontrol eder.

    Args:
        fb_response: Facebook API'den gelen JSON yanıt dict'i.

    Returns:
        Post ID string'i. Bulunamazsa boş string.
    """
    # Önce "post_id" kontrol et (daha spesifik)
    post_id: str = fb_response.get("post_id", "")
    if post_id:
        return post_id

    # Sonra genel "id" alanını kontrol et
    return fb_response.get("id", "")


# ──────────────────────────────────────────────
# 1) Fotoğraflı Paylaşım
# ──────────────────────────────────────────────

def post_photo_with_text(image_path: str, message: str) -> Optional[dict]:
    """
    Facebook sayfasına fotoğraflı post paylaşır.

    Facebook Graph API /{page_id}/photos endpoint'ini kullanır.
    Görsel multipart/form-data olarak gönderilir.

    Args:
        image_path: Paylaşılacak görselin dosya yolu.
        message:    Post metni (görselin altında görünür).

    Returns:
        Başarılıysa Facebook API yanıt dict'i ({"id": "...", ...}).
        Başarısızsa None.
    """
    page_id, access_token = _get_fb_credentials()

    if not page_id or not access_token:
        log("❌ Facebook kimlik bilgileri eksik, paylaşım yapılamıyor", "ERROR")
        return None

    # Endpoint URL
    url: str = f"{_FB_BASE_URL}/{page_id}/photos"

    log(f"📤 Fotoğraflı paylaşım gönderiliyor: {url}", "INFO")
    log(f"📎 Görsel: {image_path}", "INFO")
    log(f"📝 Metin uzunluğu: {len(message)} karakter", "INFO")

    try:
        # Görsel dosyasını binary olarak aç
        with open(image_path, "rb") as image_file:
            files = {
                "source": image_file,
            }
            data = {
                "message": message,
                "access_token": access_token,
            }

            response = requests.post(
                url,
                files=files,
                data=data,
                timeout=_REQUEST_TIMEOUT,
            )

        # Yanıtı parse et
        response_json: dict = response.json()

        # Hata kontrolü
        if "error" in response_json:
            error_info: dict = response_json["error"]
            error_message: str = error_info.get("message", "Bilinmeyen hata")
            error_type: str = error_info.get("type", "Bilinmeyen tip")
            error_code: int = error_info.get("code", 0)

            log(
                f"❌ Facebook API hatası: [{error_code}] {error_type} — {error_message}",
                "ERROR",
            )
            return None

        # Başarı kontrolü — "id" veya "post_id" alanı olmalı
        post_id: str = _extract_post_id(response_json)
        if post_id:
            log(f"✅ Fotoğraflı paylaşım başarılı: ID={post_id}", "INFO")
            return response_json

        # Beklenmeyen yanıt formatı
        log(
            f"⚠️ Facebook API beklenmeyen yanıt: {response_json}",
            "WARNING",
        )
        return None

    except requests.exceptions.Timeout:
        log(
            f"❌ Facebook API zaman aşımı ({_REQUEST_TIMEOUT}sn)",
            "ERROR",
        )
        return None

    except requests.exceptions.ConnectionError as conn_err:
        log(f"❌ Facebook API bağlantı hatası: {conn_err}", "ERROR")
        return None

    except requests.exceptions.RequestException as req_err:
        log(f"❌ Facebook API istek hatası: {req_err}", "ERROR")
        return None

    except FileNotFoundError:
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None

    except Exception as e:
        log(f"❌ Facebook paylaşım beklenmeyen hata: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# 2) Sadece Metin Paylaşım
# ──────────────────────────────────────────────

def post_text_only(message: str) -> Optional[dict]:
    """
    Facebook sayfasına görselsiz (sadece metin) post paylaşır.

    Görsel elde edilemediğinde yedek olarak kullanılır.
    Facebook Graph API /{page_id}/feed endpoint'ini kullanır.

    Args:
        message: Post metni.

    Returns:
        Başarılıysa Facebook API yanıt dict'i ({"id": "..."}).
        Başarısızsa None.
    """
    page_id, access_token = _get_fb_credentials()

    if not page_id or not access_token:
        log("❌ Facebook kimlik bilgileri eksik, paylaşım yapılamıyor", "ERROR")
        return None

    # Endpoint URL
    url: str = f"{_FB_BASE_URL}/{page_id}/feed"

    log(f"📤 Metin paylaşımı gönderiliyor: {url}", "INFO")
    log(f"📝 Metin uzunluğu: {len(message)} karakter", "INFO")

    try:
        data = {
            "message": message,
            "access_token": access_token,
        }

        response = requests.post(
            url,
            data=data,
            timeout=_REQUEST_TIMEOUT,
        )

        # Yanıtı parse et
        response_json: dict = response.json()

        # Hata kontrolü
        if "error" in response_json:
            error_info: dict = response_json["error"]
            error_message: str = error_info.get("message", "Bilinmeyen hata")
            error_type: str = error_info.get("type", "Bilinmeyen tip")
            error_code: int = error_info.get("code", 0)

            log(
                f"❌ Facebook API hatası: [{error_code}] {error_type} — {error_message}",
                "ERROR",
            )
            return None

        # Başarı kontrolü
        post_id: str = _extract_post_id(response_json)
        if post_id:
            log(f"✅ Metin paylaşımı başarılı: ID={post_id}", "INFO")
            return response_json

        # Beklenmeyen yanıt formatı
        log(
            f"⚠️ Facebook API beklenmeyen yanıt: {response_json}",
            "WARNING",
        )
        return None

    except requests.exceptions.Timeout:
        log(
            f"❌ Facebook API zaman aşımı ({_REQUEST_TIMEOUT}sn)",
            "ERROR",
        )
        return None

    except requests.exceptions.ConnectionError as conn_err:
        log(f"❌ Facebook API bağlantı hatası: {conn_err}", "ERROR")
        return None

    except requests.exceptions.RequestException as req_err:
        log(f"❌ Facebook API istek hatası: {req_err}", "ERROR")
        return None

    except Exception as e:
        log(f"❌ Facebook metin paylaşımı beklenmeyen hata: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# 3) Paylaşım Kaydı
# ──────────────────────────────────────────────

def record_posted(
    article: dict,
    fb_response: dict,
    image_source: str,
) -> None:
    """
    Başarılı paylaşımı data/posted_news.json dosyasına kaydeder.

    Bu kayıt iki amaçla kullanılır:
      1. Aynı haberin tekrar paylaşılmasını engeller (duplicate kontrolü)
      2. Günlük paylaşım sayısını takip eder (günlük limit kontrolü)

    Kayıt formatı:
      {
        "title":        "Haber başlığı",
        "original_url": "https://haber-sitesi.com/...",
        "source":       "Kaynak adı",
        "score":        78,
        "posted_at":    "2024-01-15T14:30:00+03:00",
        "fb_post_id":   "123456789_987654321",
        "image_source": "og:image" | "ai_generated" | "none"
      }

    Args:
        article:      Paylaşılan haber dict'i.
        fb_response:  Facebook API'den gelen başarılı yanıt dict'i.
        image_source: Görsel kaynağı ("og:image", "ai_generated", "none").
    """
    try:
        # Mevcut kayıtları oku
        posted_data: dict = get_posted_news()

        # posts listesini al (yoksa oluştur)
        posts_list: list = posted_data.get("posts", [])

        # daily_counts'u al (yoksa oluştur)
        daily_counts: dict = posted_data.get("daily_counts", {})

        # Facebook post ID'sini çıkar
        fb_post_id: str = _extract_post_id(fb_response)

        # Şu anki Türkiye zamanı
        turkey_now = get_turkey_now()

        # Yeni kayıt oluştur
        new_record: dict = {
            "title": article.get("title", "Başlık yok"),
            "original_url": article.get("link", ""),
            "source": article.get("source_name", "Bilinmeyen kaynak"),
            "score": article.get("score", 0),
            "posted_at": turkey_now.isoformat(),
            "fb_post_id": fb_post_id,
            "image_source": image_source,
        }

        # Posts listesine ekle
        posts_list.append(new_record)

        # Günlük sayacı güncelle
        today_str: str = get_today_str()
        current_daily_count: int = daily_counts.get(today_str, 0)
        daily_counts[today_str] = current_daily_count + 1

        # Veriyi güncelle
        posted_data["posts"] = posts_list
        posted_data["daily_counts"] = daily_counts

        # Dosyaya kaydet
        save_posted_news(posted_data)

        title: str = article.get("title", "Başlık yok")
        log(
            f"💾 Paylaşım kaydedildi: {title} "
            f"(bugün toplam: {daily_counts[today_str]} post)",
            "INFO",
        )

    except Exception as e:
        log(f"⚠️ Paylaşım kaydı sırasında hata: {e}", "WARNING")
        # Kayıt hatası paylaşımı engellemez — sadece logla


# ──────────────────────────────────────────────
# 4) Ana Paylaşım Fonksiyonu
# ──────────────────────────────────────────────

def publish(
    article: dict,
    post_text: str,
    image_path: Optional[str],
) -> bool:
    """
    ANA FONKSİYON — Haberi Facebook sayfasında paylaşır.

    İşleyiş:
      1. Görsel varsa fotoğraflı paylaşım dene
      2. Görsel yoksa sadece metin paylaşımı dene
      3. İlk deneme başarısızsa 5 saniye bekleyip tekrar dene (1 retry)
      4. Başarılıysa paylaşımı kaydet (record_posted)

    Args:
        article:    Paylaşılacak haber dict'i.
        post_text:  Facebook'ta görünecek post metni (YZ tarafından yazılmış).
        image_path: Görsel dosya yolu. None ise sadece metin paylaşılır.

    Returns:
        True: Paylaşım başarılı.
        False: Paylaşım başarısız (2 deneme sonra).
    """
    title: str = article.get("title", "Başlık yok")
    separator: str = "=" * 60

    log(separator, "INFO")
    log(f"📣 Facebook'a paylaşılıyor: {title}", "INFO")
    log(separator, "INFO")

    # ── Görsel durumunu belirle ──
    has_image: bool = False
    image_source: str = "none"

    if image_path and os.path.exists(image_path):
        has_image = True
        image_source = article.get("image_source", "unknown")
        log(f"🖼️ Görsel mevcut: {image_path} (kaynak: {image_source})", "INFO")
    else:
        if image_path:
            log(
                f"⚠️ Görsel dosyası bulunamadı: {image_path}. "
                "Sadece metin paylaşılacak.",
                "WARNING",
            )
        else:
            log("ℹ️ Görsel yok, sadece metin paylaşılacak", "INFO")

    # ── Post metnini logla (ilk 200 karakter) ──
    text_preview: str = post_text[:200] + "..." if len(post_text) > 200 else post_text
    log(f"📝 Post metni önizleme:\n{text_preview}", "INFO")

    # ── İlk deneme ──
    fb_response: Optional[dict] = None

    if has_image:
        log("📤 Deneme 1/2: Fotoğraflı paylaşım...", "INFO")
        fb_response = post_photo_with_text(image_path, post_text)
    else:
        log("📤 Deneme 1/2: Metin paylaşımı...", "INFO")
        fb_response = post_text_only(post_text)

    # ── İlk deneme başarısız → tekrar dene ──
    if fb_response is None:
        log(
            f"⚠️ İlk deneme başarısız, {_RETRY_DELAY} saniye "
            "beklenip tekrar denenecek...",
            "WARNING",
        )
        time.sleep(_RETRY_DELAY)

        if has_image:
            log("📤 Deneme 2/2: Fotoğraflı paylaşım (tekrar)...", "INFO")
            fb_response = post_photo_with_text(image_path, post_text)
        else:
            log("📤 Deneme 2/2: Metin paylaşımı (tekrar)...", "INFO")
            fb_response = post_text_only(post_text)

    # ── Sonuç kontrolü ──
    if fb_response is None:
        log(
            f"❌ Facebook paylaşımı BAŞARISIZ (2 deneme sonra): {title}",
            "ERROR",
        )
        log(separator, "INFO")
        return False

    # ── Başarılı — kaydı tut ──
    fb_post_id: str = _extract_post_id(fb_response)

    record_posted(article, fb_response, image_source)

    log(separator, "INFO")
    log(
        f"🎉 BAŞARIYLA PAYLAŞILDI: {title} | FB ID: {fb_post_id}",
        "INFO",
    )
    log(separator, "INFO")

    return True
