"""
facebook_poster.py — Facebook Sayfa Paylaşım Modülü (v4 — Feed Fix)

v4 Değişiklik:
  - Fotoğraflı paylaşımda TEK DOĞRU YÖNTEM kullanılır:
    1. Görseli published=false ile yükle (temporary OLMADAN)
    2. /{page_id}/feed + attached_media ile paylaş
    
  - Bu yöntem görseli FEED'de (ana sayfada) gösterir
  - Eski yöntemlerdeki sorunlar:
    * object_attachment → "unknown error" hatası veriyordu
    * temporary=true → Fotoğraflar sekmesine düşürüyordu
    * photos + published=true → Bazen sadece albüme ekliyordu
    
  - Yedek olarak: photos + published=true (Yöntem B)
  - Son çare: Sadece metin paylaşımı

Ortam değişkenleri (GitHub Secrets'ta saklanır):
  - FB_PAGE_ID       → Facebook sayfa ID numarası  
  - FB_ACCESS_TOKEN  → Uzun süreli sayfa erişim tokenı
"""

import os
import time
import json
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

_FB_API_VERSION: str = "v21.0"
_FB_BASE_URL: str = f"https://graph.facebook.com/{_FB_API_VERSION}"

# HTTP istek zaman aşımı (saniye)
_REQUEST_TIMEOUT: int = 60

# Başarısız paylaşımda tekrar denemeden önce bekleme süresi (saniye)
_RETRY_DELAY: int = 5


# ──────────────────────────────────────────────
# Yardımcı (private) fonksiyonlar
# ──────────────────────────────────────────────

def _get_fb_credentials() -> tuple[str, str]:
    """Facebook API kimlik bilgilerini ortam değişkenlerinden okur."""
    page_id: str = os.environ.get("FB_PAGE_ID", "")
    access_token: str = os.environ.get("FB_ACCESS_TOKEN", "")

    if not page_id:
        log("❌ FB_PAGE_ID ortam değişkeni bulunamadı!", "ERROR")
    if not access_token:
        log("❌ FB_ACCESS_TOKEN ortam değişkeni bulunamadı!", "ERROR")

    return page_id, access_token


def _extract_post_id(fb_response: dict) -> str:
    """Facebook API yanıtından post ID'sini çıkarır."""
    post_id: str = fb_response.get("post_id", "")
    if post_id:
        return post_id
    return fb_response.get("id", "")


def _mask_id(post_id: str) -> str:
    """Post ID'sini logda kısmen maskeler."""
    if not post_id:
        return "???"
    parts = post_id.split("_")
    if len(parts) == 2:
        return f"***_{parts[1]}"
    return post_id


# ──────────────────────────────────────────────
# YÖNTEM A (ÖNCELİKLİ): Upload + Feed attached_media
# ──────────────────────────────────────────────

def _post_photo_method_a(
    page_id: str,
    access_token: str,
    image_path: str,
    message: str,
) -> Optional[dict]:
    """
    Yöntem A: Görseli unpublished yükle → feed'e attached_media ile paylaş.
    
    Bu yöntem Facebook'un resmi olarak desteklediği
    "fotoğraflı feed post" yöntemidir.
    
    ADIM 1: /{page_id}/photos → published=false (temporary YOK!)
            → photo_id alınır
    ADIM 2: /{page_id}/feed  → attached_media[0]={"media_fbid": photo_id}
            → Post FEED'de (ana sayfada) görünür
    
    ÖNEMLİ: "temporary" parametresi KULLANILMAZ!
    temporary=true olunca Facebook görseli geçici sayar ve
    feed'e düzgün bağlayamaz, Fotoğraflar sekmesine düşürür.
    
    Args:
        page_id: Facebook sayfa ID.
        access_token: Sayfa erişim tokenı.
        image_path: Görsel dosya yolu.
        message: Post metni.
    
    Returns:
        Başarılıysa API yanıt dict'i, değilse None.
    """
    log("📤 Yöntem A: Upload (published=false) → Feed (attached_media)", "INFO")
    
    # ── ADIM 1: Görseli unpublished olarak yükle ──
    upload_url: str = f"{_FB_BASE_URL}/{page_id}/photos"
    
    try:
        with open(image_path, "rb") as image_file:
            files = {"source": image_file}
            data = {
                "published": "false",
                "access_token": access_token,
            }
            # NOT: "temporary" parametresi KASITLI OLARAK YOK!
            # temporary=true görseli geçici yapar ve feed'e düzgün bağlanamaz
            
            upload_resp = requests.post(
                upload_url, files=files, data=data, timeout=_REQUEST_TIMEOUT
            )
        
        upload_json = upload_resp.json()
        
        if "error" in upload_json:
            error_msg = upload_json["error"].get("message", "Bilinmeyen")
            error_code = upload_json["error"].get("code", 0)
            log(
                f"❌ Yöntem A Adım 1 hatası: [{error_code}] {error_msg}",
                "ERROR",
            )
            return None
        
        photo_id = upload_json.get("id", "")
        if not photo_id:
            log("❌ Yöntem A: photo_id alınamadı", "ERROR")
            return None
        
        log(f"✅ Yöntem A Adım 1: Görsel yüklendi → photo_id={photo_id}", "INFO")
        
    except FileNotFoundError:
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None
    except requests.exceptions.Timeout:
        log("❌ Yöntem A Adım 1: Görsel yükleme zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Yöntem A Adım 1 hatası: {e}", "ERROR")
        return None
    
    # ── ADIM 2: Feed'e attached_media ile paylaş ──
    feed_url: str = f"{_FB_BASE_URL}/{page_id}/feed"
    
    try:
        post_data = {
            "message": message,
            "attached_media[0]": json.dumps({"media_fbid": photo_id}),
            "access_token": access_token,
        }
        
        log("📤 Yöntem A Adım 2: Feed'e attached_media ile paylaşılıyor...", "INFO")
        
        post_resp = requests.post(
            feed_url, data=post_data, timeout=_REQUEST_TIMEOUT
        )
        
        post_json = post_resp.json()
        
        if "error" in post_json:
            error_info = post_json["error"]
            error_msg = error_info.get("message", "Bilinmeyen")
            error_code = error_info.get("code", 0)
            log(
                f"❌ Yöntem A Adım 2 hatası: [{error_code}] {error_msg}",
                "ERROR",
            )
            return None
        
        post_id = _extract_post_id(post_json)
        if post_id:
            log(f"✅ Yöntem A Adım 2: Feed post başarılı → ID={_mask_id(post_id)}", "INFO")
            log("🎯 Post ANA SAYFADA (feed'de) görünecek!", "INFO")
            return post_json
        
        log(f"⚠️ Yöntem A: Beklenmeyen yanıt: {post_json}", "WARNING")
        return None
        
    except requests.exceptions.Timeout:
        log("❌ Yöntem A Adım 2: Feed paylaşım zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Yöntem A Adım 2 hatası: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# YÖNTEM B (YEDEK): photos + published=true
# ──────────────────────────────────────────────

def _post_photo_method_b(
    page_id: str,
    access_token: str,
    image_path: str,
    message: str,
) -> Optional[dict]:
    """
    Yöntem B (yedek): /{page_id}/photos + published=true.
    
    Bu yöntemde görsel Fotoğraflar albümüne eklenir ve
    AYNI ZAMANDA timeline'da da görünür (sayfa ayarlarına bağlı).
    
    Yöntem A başarısız olursa bu denenır.
    
    Args:
        page_id: Facebook sayfa ID.
        access_token: Sayfa erişim tokenı.
        image_path: Görsel dosya yolu.
        message: Post metni.
    
    Returns:
        Başarılıysa API yanıt dict'i, değilse None.
    """
    url: str = f"{_FB_BASE_URL}/{page_id}/photos"
    
    log("📤 Yöntem B: photos + published=true (yedek)", "INFO")
    
    try:
        with open(image_path, "rb") as image_file:
            files = {"source": image_file}
            data = {
                "message": message,
                "published": "true",
                "access_token": access_token,
            }
            
            response = requests.post(
                url, files=files, data=data, timeout=_REQUEST_TIMEOUT
            )
        
        response_json: dict = response.json()
        
        if "error" in response_json:
            error_info = response_json["error"]
            error_msg = error_info.get("message", "Bilinmeyen")
            error_code = error_info.get("code", 0)
            log(
                f"❌ Yöntem B hatası: [{error_code}] {error_msg}",
                "ERROR",
            )
            return None
        
        post_id = _extract_post_id(response_json)
        if post_id:
            log(f"✅ Yöntem B başarılı: photo_id={_mask_id(post_id)}", "INFO")
            log("ℹ️ Görsel albüme + timeline'a eklendi (sayfa ayarına bağlı)", "INFO")
            return response_json
        
        log(f"⚠️ Yöntem B: Beklenmeyen yanıt: {response_json}", "WARNING")
        return None
        
    except FileNotFoundError:
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None
    except requests.exceptions.Timeout:
        log("❌ Yöntem B zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Yöntem B hatası: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# ANA FONKSİYON: Fotoğraflı Paylaşım
# ──────────────────────────────────────────────

def post_photo_with_text(image_path: str, message: str) -> Optional[dict]:
    """
    Facebook sayfasına fotoğraflı post paylaşır — ANA SAYFADA görünür.
    
    2 yöntem sırayla denenir:
      Yöntem A: Upload (published=false) → Feed (attached_media) — ÖNCELİKLİ
      Yöntem B: photos + published=true — YEDEK
    
    Args:
        image_path: Paylaşılacak görselin dosya yolu.
        message: Post metni.
    
    Returns:
        Başarılıysa Facebook API yanıt dict'i, değilse None.
    """
    page_id, access_token = _get_fb_credentials()
    
    if not page_id or not access_token:
        log("❌ Facebook kimlik bilgileri eksik", "ERROR")
        return None
    
    log("📤 Fotoğraflı paylaşım başlatılıyor (2 yöntemli)", "INFO")
    log(f"📎 Görsel: {image_path}", "INFO")
    log(f"📝 Metin uzunluğu: {len(message)} karakter", "INFO")
    
    # ── YÖNTEM A (ÖNCELİKLİ): Upload → Feed ──
    log("━" * 40, "INFO")
    log("🔵 YÖNTEM A deneniyor (Upload → Feed attached_media)...", "INFO")
    result = _post_photo_method_a(page_id, access_token, image_path, message)
    
    if result:
        log("🎯 YÖNTEM A BAŞARILI — Post FEED'de (ana sayfada) görünecek!", "INFO")
        return result
    
    # ── YÖNTEM B (YEDEK): photos + published=true ──
    log("━" * 40, "INFO")
    log("🟡 Yöntem A başarısız, YÖNTEM B deneniyor (photos + published=true)...", "INFO")
    result = _post_photo_method_b(page_id, access_token, image_path, message)
    
    if result:
        log("✅ YÖNTEM B BAŞARILI — Görsel paylaşıldı (albüm + timeline)", "INFO")
        return result
    
    # ── Her iki yöntem de başarısız ──
    log("━" * 40, "INFO")
    log("❌ Tüm görsel yöntemleri başarısız", "ERROR")
    return None


# ──────────────────────────────────────────────
# Sadece Metin Paylaşım
# ──────────────────────────────────────────────

def post_text_only(message: str) -> Optional[dict]:
    """Facebook sayfasına görselsiz (sadece metin) post paylaşır."""
    page_id, access_token = _get_fb_credentials()
    
    if not page_id or not access_token:
        log("❌ Facebook kimlik bilgileri eksik", "ERROR")
        return None
    
    url: str = f"{_FB_BASE_URL}/{page_id}/feed"
    
    log(f"📤 Metin paylaşımı gönderiliyor", "INFO")
    log(f"📝 Metin uzunluğu: {len(message)} karakter", "INFO")
    
    try:
        data = {
            "message": message,
            "access_token": access_token,
        }
        
        response = requests.post(url, data=data, timeout=_REQUEST_TIMEOUT)
        response_json: dict = response.json()
        
        if "error" in response_json:
            error_info = response_json["error"]
            log(
                f"❌ Facebook API hatası: [{error_info.get('code', 0)}] "
                f"{error_info.get('type', '')} — {error_info.get('message', '')}",
                "ERROR",
            )
            return None
        
        post_id = _extract_post_id(response_json)
        if post_id:
            log(f"✅ Metin paylaşımı başarılı: ID={_mask_id(post_id)}", "INFO")
            return response_json
        
        log(f"⚠️ Beklenmeyen yanıt: {response_json}", "WARNING")
        return None
        
    except requests.exceptions.Timeout:
        log(f"❌ Zaman aşımı ({_REQUEST_TIMEOUT}sn)", "ERROR")
        return None
    except requests.exceptions.RequestException as e:
        log(f"❌ İstek hatası: {e}", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Beklenmeyen hata: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# Paylaşım Kaydı
# ──────────────────────────────────────────────

def record_posted(
    article: dict,
    fb_response: dict,
    image_source: str,
) -> None:
    """Başarılı paylaşımı data/posted_news.json dosyasına kaydeder."""
    try:
        posted_data: dict = get_posted_news()
        posts_list: list = posted_data.get("posts", [])
        daily_counts: dict = posted_data.get("daily_counts", {})
        
        fb_post_id: str = _extract_post_id(fb_response)
        turkey_now = get_turkey_now()
        
        new_record: dict = {
            "title": article.get("title", "Başlık yok"),
            "original_url": article.get("link", ""),
            "source": article.get("source_name", "Bilinmeyen kaynak"),
            "score": article.get("score", 0),
            "posted_at": turkey_now.isoformat(),
            "fb_post_id": fb_post_id,
            "image_source": image_source,
        }
        
        posts_list.append(new_record)
        
        today_str: str = get_today_str()
        current_daily_count: int = daily_counts.get(today_str, 0)
        daily_counts[today_str] = current_daily_count + 1
        
        posted_data["posts"] = posts_list
        posted_data["daily_counts"] = daily_counts
        
        save_posted_news(posted_data)
        
        title: str = article.get("title", "Başlık yok")
        log(
            f"💾 Paylaşım kaydedildi: {title} "
            f"(bugün toplam: {daily_counts[today_str]} post)",
            "INFO",
        )
        
    except Exception as e:
        log(f"⚠️ Paylaşım kaydı sırasında hata: {e}", "WARNING")


# ──────────────────────────────────────────────
# Ana Paylaşım Fonksiyonu
# ──────────────────────────────────────────────

def publish(
    article: dict,
    post_text: str,
    image_path: Optional[str],
) -> bool:
    """
    ANA FONKSİYON — Haberi Facebook sayfasında paylaşır.
    
    İşleyiş:
      1. Görsel varsa fotoğraflı paylaşım dene (2 yöntemli)
      2. Fotoğraflı başarısızsa sadece metin paylaşımı dene
      3. İlk deneme başarısızsa 5 saniye bekleyip tekrar dene
      4. Başarılıysa paylaşımı kaydet
    
    Args:
        article: Paylaşılacak haber dict'i.
        post_text: Facebook'ta görünecek post metni.
        image_path: Görsel dosya yolu. None ise sadece metin.
    
    Returns:
        True: Paylaşım başarılı.
        False: Paylaşım başarısız.
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
            log(f"⚠️ Görsel dosyası bulunamadı: {image_path}", "WARNING")
        else:
            log("ℹ️ Görsel yok, sadece metin paylaşılacak", "INFO")
    
    # ── Post metnini logla ──
    text_preview: str = post_text[:200] + "..." if len(post_text) > 200 else post_text
    log(f"📝 Post metni önizleme:\n{text_preview}", "INFO")
    
    # ── İlk deneme ──
    fb_response: Optional[dict] = None
    
    if has_image:
        log("📤 Deneme 1/2: Fotoğraflı paylaşım...", "INFO")
        fb_response = post_photo_with_text(image_path, post_text)
        
        # Fotoğraflı başarısızsa metin olarak dene
        if fb_response is None:
            log("⚠️ Fotoğraflı paylaşım başarısız, metin olarak deneniyor...", "WARNING")
            fb_response = post_text_only(post_text)
    else:
        log("📤 Deneme 1/2: Metin paylaşımı...", "INFO")
        fb_response = post_text_only(post_text)
    
    # ── İlk deneme başarısız → tekrar dene ──
    if fb_response is None:
        log(
            f"⚠️ İlk deneme başarısız, {_RETRY_DELAY} saniye beklenip tekrar denenecek...",
            "WARNING",
        )
        time.sleep(_RETRY_DELAY)
        
        if has_image:
            log("📤 Deneme 2/2: Fotoğraflı paylaşım (tekrar)...", "INFO")
            fb_response = post_photo_with_text(image_path, post_text)
            
            if fb_response is None:
                log("📤 Deneme 2/2: Son çare — sadece metin...", "INFO")
                fb_response = post_text_only(post_text)
        else:
            log("📤 Deneme 2/2: Metin paylaşımı (tekrar)...", "INFO")
            fb_response = post_text_only(post_text)
    
    # ── Sonuç kontrolü ──
    if fb_response is None:
        log(f"❌ Facebook paylaşımı BAŞARISIZ (tüm denemeler): {title}", "ERROR")
        log(separator, "INFO")
        return False
    
    # ── Başarılı — kaydı tut ──
    fb_post_id: str = _extract_post_id(fb_response)
    
    record_posted(article, fb_response, image_source)
    
    log(separator, "INFO")
    log(f"🎉 BAŞARIYLA PAYLAŞILDI: {title} | FB ID: {_mask_id(fb_post_id)}", "INFO")
    log(separator, "INFO")
    
    return True
