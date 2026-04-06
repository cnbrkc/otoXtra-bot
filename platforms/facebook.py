"""
platforms/facebook.py — Facebook Graph API Katmanı

otoXtra Facebook Botu için SADECE Facebook API çağrısı yapar.
Karar vermez, kayıt tutmaz, limit kontrolü yapmaz.
Bu dosya bir "sürücü" gibi davranır — sadece teknik iletişimi sağlar.

Fonksiyonlar:
    post_photo(image_path, message) → Görselli post atar, post_id döner
    post_text(message)              → Sadece metin post atar, post_id döner

Kullanım:
    from platforms.facebook import post_photo, post_text

    post_id = post_photo("/tmp/gorsel.jpg", "Post metni")
    post_id = post_text("Sadece metin post")

Ortam değişkenleri (GitHub Secrets):
    FB_PAGE_ID       → Facebook sayfa ID
    FB_ACCESS_TOKEN  → Uzun süreli sayfa erişim tokenı
"""

import os
import time
from typing import Optional

import requests

from core.logger import log


# ============================================================
# SABİTLER
# ============================================================

_FB_API_VERSION = "v25.0"
_FB_BASE_URL = f"https://graph.facebook.com/{_FB_API_VERSION}"
_REQUEST_TIMEOUT = 60


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def _get_credentials() -> tuple:
    """Facebook API kimlik bilgilerini ortam değişkenlerinden okur.

    Returns:
        tuple: (page_id, access_token)
               Eksik varsa boş string döner, log yazar.
    """
    page_id = os.environ.get("FB_PAGE_ID", "")
    access_token = os.environ.get("FB_ACCESS_TOKEN", "")

    if not page_id:
        log("❌ FB_PAGE_ID ortam değişkeni bulunamadı!", "ERROR")
    if not access_token:
        log("❌ FB_ACCESS_TOKEN ortam değişkeni bulunamadı!", "ERROR")

    return page_id, access_token


def _extract_post_id(response: dict) -> str:
    """Facebook API yanıtından post ID'sini çıkarır.

    Args:
        response: Facebook API'den gelen dict.

    Returns:
        str: Post ID. Bulunamazsa boş string.
    """
    post_id = response.get("post_id", "")
    if post_id:
        return post_id
    return response.get("id", "")


def _mask_id(post_id: str) -> str:
    """Post ID'sini logda kısmen maskeler (güvenlik).

    Args:
        post_id: Maskelenecek ID.

    Returns:
        str: Örnek: "***_123456789"
    """
    if not post_id:
        return "???"
    parts = post_id.split("_")
    if len(parts) == 2:
        return f"***_{parts[1]}"
    return post_id


def _handle_api_error(result: dict, context: str) -> None:
    """Facebook API hata yanıtını loglar.

    Args:
        result:  Facebook API yanıtı.
        context: Hangi işlemde hata oluştu (log için).
    """
    err = result.get("error", {})
    log(
        f"❌ Facebook API hatası ({context}): "
        f"[{err.get('code', 0)}] "
        f"{err.get('type', '')} — "
        f"{err.get('message', '')}",
        "ERROR",
    )


# ============================================================
# 1. GÖRSELLİ POST
# ============================================================

def post_photo(image_path: str, message: str) -> Optional[str]:
    """Facebook sayfasına görselli post atar.

    Graph API /photos endpoint'ini kullanır.
    Görsel multipart/form-data olarak gönderilir.

    Args:
        image_path: Gönderilecek görselin yerel dosya yolu.
        message:    Post metni.

    Returns:
        str: Başarılıysa post_id. Başarısızsa None.
    """
    page_id, access_token = _get_credentials()
    if not page_id or not access_token:
        return None

    if not image_path or not os.path.exists(image_path):
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None

    url = f"{_FB_BASE_URL}/{page_id}/photos"

    log(f"📤 Görselli post gönderiliyor: {len(message)} karakter metin")
    log(f"🖼️ Görsel: {image_path}")

    try:
        with open(image_path, "rb") as img_file:
            response = requests.post(
                url,
                data={
                    "message": message,
                    "access_token": access_token,
                },
                files={"source": img_file},
                timeout=_REQUEST_TIMEOUT,
            )

        result = response.json()

        if "error" in result:
            _handle_api_error(result, "post_photo")
            return None

        post_id = _extract_post_id(result)
        if post_id:
            log(f"✅ Görselli post başarılı: ID={_mask_id(post_id)}")
            return post_id

        log(f"⚠️ Beklenmeyen yanıt: {result}", "WARNING")
        return None

    except requests.exceptions.Timeout:
        log(f"❌ Zaman aşımı ({_REQUEST_TIMEOUT}sn) — post_photo", "ERROR")
        return None
    except requests.exceptions.RequestException as exc:
        log(f"❌ HTTP istek hatası — post_photo: {exc}", "ERROR")
        return None
    except Exception as exc:
        log(f"❌ Beklenmeyen hata — post_photo: {exc}", "ERROR")
        return None


# ============================================================
# 2. SADECE METİN POST
# ============================================================

def post_text(message: str) -> Optional[str]:
    """Facebook sayfasına sadece metin post atar.

    Graph API /feed endpoint'ini kullanır.

    Args:
        message: Post metni.

    Returns:
        str: Başarılıysa post_id. Başarısızsa None.
    """
    page_id, access_token = _get_credentials()
    if not page_id or not access_token:
        return None

    url = f"{_FB_BASE_URL}/{page_id}/feed"

    log(f"📤 Metin post gönderiliyor: {len(message)} karakter")

    try:
        response = requests.post(
            url,
            data={
                "message": message,
                "access_token": access_token,
            },
            timeout=_REQUEST_TIMEOUT,
        )

        result = response.json()

        if "error" in result:
            _handle_api_error(result, "post_text")
            return None

        post_id = _extract_post_id(result)
        if post_id:
            log(f"✅ Metin post başarılı: ID={_mask_id(post_id)}")
            return post_id

        log(f"⚠️ Beklenmeyen yanıt: {result}", "WARNING")
        return None

    except requests.exceptions.Timeout:
        log(f"❌ Zaman aşımı ({_REQUEST_TIMEOUT}sn) — post_text", "ERROR")
        return None
    except requests.exceptions.RequestException as exc:
        log(f"❌ HTTP istek hatası — post_text: {exc}", "ERROR")
        return None
    except Exception as exc:
        log(f"❌ Beklenmeyen hata — post_text: {exc}", "ERROR")
        return None


# ============================================================
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ============================================================

if __name__ == "__main__":
    log("=== platforms/facebook.py modül testi başlıyor ===")

    page_id, access_token = _get_credentials()

    if page_id and access_token:
        log(f"✅ FB_PAGE_ID: ***{page_id[-4:]}")
        log(f"✅ FB_ACCESS_TOKEN: ***{access_token[-6:]}")
        log("Kimlik bilgileri mevcut — gerçek post ATILMAYACAK (test modu)")
    else:
        log("⚠️ Kimlik bilgileri eksik — GitHub Secrets kontrol et", "WARNING")

    log("=== platforms/facebook.py modül testi tamamlandı ===")
