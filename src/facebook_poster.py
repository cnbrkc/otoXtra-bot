"""
facebook_poster.py — Facebook Sayfa Paylaşım Modülü (v11 — scheduled publish fix)

v11 Değişiklik:

  ❌ v7-v10 SORUNU: temporary=true + attached_media yöntemi
     → Çeşitli data/files/params kombinasyonları denendi
     → Hiçbiri Facebook API'de güvenilir çalışmadı

  ✅ v11 ÇÖZÜM: 3 YÖNTEM SIRALI DENENİR

     YÖNTEM A (BİRİNCİL): Zamanlanmış Gönderi (Business Suite tarzı)
       Adım 1: POST /{page_id}/photos
                 source    = <dosya>
                 published = false   ← Yayınlanmamış (taslak)
                 (temporary KULLANILMIYOR!)
               → photo_id alınır

       Adım 2: POST /{page_id}/feed
                 message              = <metin>
                 attached_media[0]    = {"media_fbid":"photo_id"}
                 published            = false
                 scheduled_publish_time = <şu andan 2 dakika sonra>
               → Facebook kendi zamanlama motoru ile 2dk sonra FEED'de yayınlar
               → Business Suite'in kullandığı aynı mekanizma

     YÖNTEM B (İKİNCİL): Doğrudan Feed Paylaşım
       Adım 1: Aynı (published=false, temporary yok)
       Adım 2: POST /{page_id}/feed
                 message           = <metin>
                 attached_media[0] = {"media_fbid":"photo_id"}
               → Anında yayınlanmayı dener

     YÖNTEM C (YEDEK): photos + published=true
       → Tek adımda doğrudan fotoğraf paylaşımı
       → Feed + Fotoğraflar'da görünür (en azından paylaşım olsun)

  ✅ API versiyonu v21.0 → v19.0 olarak değiştirildi
     → v19.0 daha kararlı ve yaygın kullanılan bir versiyon

Ortam değişkenleri (GitHub Secrets):
  - FB_PAGE_ID       → Facebook sayfa ID
  - FB_ACCESS_TOKEN  → Uzun süreli sayfa erişim tokenı
"""

import os
import time
from typing import Optional
from datetime import datetime, timezone, timedelta

import requests

from utils import (
    log,
    get_posted_news,
    save_posted_news,
    get_today_str,
    get_turkey_now,
)


# ──────────────────────────────────────────────
# Sabitler
# ──────────────────────────────────────────────

# ✅ v11: API versiyonu v21.0 → v19.0 olarak değiştirildi
# v19.0 daha kararlı ve yaygın kullanılan bir versiyon
_FB_API_VERSION: str = "v19.0"
_FB_BASE_URL: str = f"https://graph.facebook.com/{_FB_API_VERSION}"

_REQUEST_TIMEOUT: int = 60
_RETRY_DELAY: int = 5
_VERIFY_DELAY: int = 4

# Zamanlanmış gönderi için minimum gecikme (saniye)
# Facebook en az 10 dakika sonrasını kabul eder
_SCHEDULE_DELAY_SECONDS: int = 620  # 10 dakika 20 saniye (güvenlik payı)


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


def _get_scheduled_time() -> int:
    """
    Şu andan _SCHEDULE_DELAY_SECONDS saniye sonrasının
    Unix timestamp'ini döndürür.

    Facebook scheduled_publish_time için Unix timestamp (epoch) bekler.
    Minimum 10 dakika sonrası olmalı, biz güvenlik payı ile 10dk 20sn kullanıyoruz.
    """
    future_time = datetime.now(timezone.utc) + timedelta(seconds=_SCHEDULE_DELAY_SECONDS)
    return int(future_time.timestamp())


# ──────────────────────────────────────────────
# Fotoğraf Yükleme (Ortak Adım 1)
# ──────────────────────────────────────────────

def _upload_photo_unpublished(
    page_id: str,
    access_token: str,
    image_path: str,
) -> Optional[str]:
    """
    Fotoğrafı YAYINLANMAMIŞ (taslak) olarak yükler ve photo_id döndürür.

    ✅ v11: temporary=true KULLANILMIYOR!
    → published=false → Fotoğraf taslak olarak yüklenir
    → Kimse göremez, ama kalıcıdır (silinmez)
    → Bu photo_id ile feed postu oluşturulabilir

    Bu fonksiyon Yöntem A ve Yöntem B tarafından ortak kullanılır.
    """
    upload_url: str = f"{_FB_BASE_URL}/{page_id}/photos"

    try:
        with open(image_path, "rb") as image_file:
            files_payload = {"source": image_file}

            # ✅ Parametreler URL'ye ekleniyor (v9'dan öğrenilen)
            upload_params = {
                "published": "false",
                "access_token": access_token,
                # ❌ temporary=true YOK! Taslak olarak kalıcı yükleniyor
            }

            log("📤 Fotoğraf yayınlanmamış (taslak) olarak yükleniyor...", "INFO")
            log("  ℹ️ published=false, temporary YOK (kalıcı taslak)", "INFO")

            upload_resp = requests.post(
                upload_url,
                params=upload_params,
                files=files_payload,
                timeout=_REQUEST_TIMEOUT,
            )

        upload_json = upload_resp.json()

        if "error" in upload_json:
            error_msg = upload_json["error"].get("message", "Bilinmeyen")
            error_code = upload_json["error"].get("code", 0)
            log(f"❌ Fotoğraf yükleme hatası: [{error_code}] {error_msg}", "ERROR")
            return None

        photo_id = upload_json.get("id", "")
        if not photo_id:
            log("❌ photo_id alınamadı", "ERROR")
            return None

        log(f"✅ Fotoğraf yüklendi (taslak) → photo_id={photo_id}", "INFO")
        return photo_id

    except FileNotFoundError:
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None
    except requests.exceptions.Timeout:
        log("❌ Fotoğraf yükleme zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Fotoğraf yükleme hatası: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# Feed Doğrulama
# ──────────────────────────────────────────────

def _verify_in_feed(
    page_id: str, access_token: str, target_id: str
) -> bool:
    """
    Paylaşımdan sonra post'un gerçekten feed'de göründüğünü doğrular.
    Teşhis amaçlıdır — sonucu loglara yazar.
    """
    try:
        resp = requests.get(
            f"{_FB_BASE_URL}/{page_id}/feed",
            params={
                "access_token": access_token,
                "limit": 5,
                "fields": "id,type,status_type,is_published,scheduled_publish_time",
            },
            timeout=30,
        )
        data = resp.json()

        if "error" in data:
            err_msg = data["error"].get("message", "Bilinmeyen")
            log(f"⚠️ Feed doğrulama API hatası: {err_msg}", "WARNING")
            return False

        feed_posts = data.get("data", [])
        feed_ids = [p.get("id", "") for p in feed_posts]

        # Feed'deki son postların detaylarını logla (teşhis)
        for p in feed_posts[:3]:
            p_id = _mask_id(p.get("id", ""))
            p_type = p.get("type", "?")
            p_status = p.get("status_type", "?")
            p_published = p.get("is_published", "?")
            p_scheduled = p.get("scheduled_publish_time", "yok")
            log(
                f"  📋 Feed'de: {p_id} | type={p_type} | "
                f"status={p_status} | published={p_published} | "
                f"scheduled={p_scheduled}",
                "INFO",
            )

        # Tam eşleşme
        if target_id in feed_ids:
            log("✅ FEED DOĞRULAMA: Post feed'de BULUNDU!", "INFO")
            return True

        # Kısmi eşleşme
        target_suffix = (
            target_id.split("_")[-1] if "_" in target_id else target_id
        )
        for fid in feed_ids:
            if target_suffix in fid:
                log(
                    "✅ FEED DOĞRULAMA: Post feed'de bulundu (kısmi eşleşme)!",
                    "INFO",
                )
                return True

        log("⚠️ FEED DOĞRULAMA: Post henüz feed'de görünmüyor", "WARNING")
        log(f"  🔍 Aranan  : {_mask_id(target_id)}", "WARNING")
        log(
            f"  📋 Feed'de : {[_mask_id(x) for x in feed_ids[:5]]}",
            "WARNING",
        )
        return False

    except Exception as e:
        log(f"⚠️ Feed doğrulama hatası: {e}", "WARNING")
        return False


# ──────────────────────────────────────────────
# Zamanlanmış Gönderi Doğrulama
# ──────────────────────────────────────────────

def _verify_scheduled_post(
    page_id: str, access_token: str, target_id: str
) -> bool:
    """
    Zamanlanmış gönderinin Facebook'un zamanlama kuyruğunda olduğunu doğrular.

    Zamanlanmış gönderiler normal feed'de görünmez,
    /{page_id}/scheduled_posts ucundan kontrol edilir.
    """
    try:
        resp = requests.get(
            f"{_FB_BASE_URL}/{page_id}/scheduled_posts",
            params={
                "access_token": access_token,
                "limit": 5,
                "fields": "id,message,scheduled_publish_time,is_published",
            },
            timeout=30,
        )
        data = resp.json()

        if "error" in data:
            err_msg = data["error"].get("message", "Bilinmeyen")
            log(f"⚠️ Zamanlanmış gönderi doğrulama hatası: {err_msg}", "WARNING")
            return False

        scheduled_posts = data.get("data", [])

        if not scheduled_posts:
            log("⚠️ Zamanlanmış gönderi kuyruğu boş", "WARNING")
            return False

        scheduled_ids = [p.get("id", "") for p in scheduled_posts]

        # Detayları logla
        for p in scheduled_posts[:3]:
            p_id = _mask_id(p.get("id", ""))
            p_msg = (p.get("message", "")[:50] + "...") if p.get("message", "") else "?"
            p_time = p.get("scheduled_publish_time", "?")
            log(
                f"  📅 Zamanlanmış: {p_id} | zaman={p_time} | mesaj={p_msg}",
                "INFO",
            )

        # Tam eşleşme
        if target_id in scheduled_ids:
            log("✅ ZAMANLANMIŞ DOĞRULAMA: Gönderi kuyrukta BULUNDU!", "INFO")
            return True

        # Kısmi eşleşme
        target_suffix = (
            target_id.split("_")[-1] if "_" in target_id else target_id
        )
        for sid in scheduled_ids:
            if target_suffix in sid:
                log(
                    "✅ ZAMANLANMIŞ DOĞRULAMA: Gönderi kuyrukta bulundu (kısmi)!",
                    "INFO",
                )
                return True

        log("⚠️ Gönderi zamanlanmış kuyrukta bulunamadı", "WARNING")
        return False

    except Exception as e:
        log(f"⚠️ Zamanlanmış gönderi doğrulama hatası: {e}", "WARNING")
        return False


# ──────────────────────────────────────────────
# YÖNTEM A (BİRİNCİL): Zamanlanmış Gönderi
# ──────────────────────────────────────────────

def _post_photo_method_a(
    page_id: str,
    access_token: str,
    image_path: str,
    message: str,
) -> Optional[dict]:
    """
    Yöntem A: Business Suite tarzı ZAMANLANMIŞ GÖNDERİ

    Bu yöntem, Facebook Business Suite'in arka planda kullandığı
    aynı zamanlama mekanizmasını kullanır.

    ADIM 1: Fotoğrafı yayınlanmamış (taslak) olarak yükle
            → published=false, temporary YOK
            → photo_id alınır

    ADIM 2: Zamanlanmış feed postu oluştur
            → published=false
            → scheduled_publish_time = şu andan ~10dk sonra
            → attached_media[0] = {"media_fbid":"photo_id"}
            → Facebook kendi zamanlama motoru ile yayınlar

    AVANTAJ: Facebook'un kendi zamanlama motoru gönderiyi yayınladığı için
    feed'de düzgün görünmesi GARANTİ. Business Suite de bunu kullanır.
    """
    log("📤 Yöntem A: Zamanlanmış gönderi (Business Suite tarzı)", "INFO")

    # ── ADIM 1: Fotoğrafı taslak olarak yükle ──
    log("━" * 30, "INFO")
    log("📤 Adım 1/2: Fotoğraf taslak olarak yükleniyor...", "INFO")

    photo_id = _upload_photo_unpublished(page_id, access_token, image_path)

    if not photo_id:
        log("❌ Yöntem A: Fotoğraf yüklenemedi", "ERROR")
        return None

    # ── ADIM 2: Zamanlanmış feed postu oluştur ──
    feed_url: str = f"{_FB_BASE_URL}/{page_id}/feed"
    scheduled_time: int = _get_scheduled_time()
    scheduled_dt = datetime.fromtimestamp(scheduled_time, tz=timezone.utc)

    log("━" * 30, "INFO")
    log("📤 Adım 2/2: Zamanlanmış feed postu oluşturuluyor...", "INFO")
    log(f"  📎 photo_id = {photo_id}", "INFO")
    log(f"  📅 Zamanlanmış yayın: {scheduled_dt.isoformat()} UTC", "INFO")
    log(f"  ⏰ Unix timestamp: {scheduled_time}", "INFO")
    log(f"  📝 Mesaj: {len(message)} karakter", "INFO")

    try:
        # ✅ v11 ÇÖZÜM: Zamanlanmış gönderi
        # attached_media → files= (multipart zorlaması)
        # message, access_token, published, scheduled_publish_time → data=
        post_files_payload = {
            "attached_media[0]": (None, f'{{"media_fbid":"{photo_id}"}}'),
        }

        post_data_payload = {
            "message": message,
            "published": "false",
            "scheduled_publish_time": str(scheduled_time),
            "access_token": access_token,
        }

        post_resp = requests.post(
            feed_url,
            files=post_files_payload,
            data=post_data_payload,
            timeout=_REQUEST_TIMEOUT,
        )

        post_json = post_resp.json()

        if "error" in post_json:
            error_info = post_json["error"]
            error_msg = error_info.get("message", "Bilinmeyen")
            error_code = error_info.get("code", 0)
            error_subcode = error_info.get("error_subcode", 0)
            log(
                f"❌ Yöntem A hatası: [{error_code}] (sub:{error_subcode}) {error_msg}",
                "ERROR",
            )
            return None

        post_id = _extract_post_id(post_json)
        if post_id:
            log(f"✅ Zamanlanmış gönderi oluşturuldu → ID={_mask_id(post_id)}", "INFO")
            log(f"🎯 Gönderi ~{_SCHEDULE_DELAY_SECONDS // 60} dakika sonra FEED'de yayınlanacak!", "INFO")
            log("  ℹ️ Facebook'un kendi zamanlama motoru yayınlayacak", "INFO")
            log("  ℹ️ Business Suite > Zamanlanmış İçerik'te görülebilir", "INFO")

            # Zamanlanmış gönderi doğrulama
            log(f"⏳ Zamanlanmış gönderi doğrulaması için {_VERIFY_DELAY}sn bekleniyor...", "INFO")
            time.sleep(_VERIFY_DELAY)
            _verify_scheduled_post(page_id, access_token, post_id)

            return post_json

        log(f"⚠️ Yöntem A: Beklenmeyen yanıt: {post_json}", "WARNING")
        return None

    except requests.exceptions.Timeout:
        log("❌ Yöntem A: Feed paylaşım zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Yöntem A hatası: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# YÖNTEM B (İKİNCİL): Doğrudan Feed Paylaşım
# ──────────────────────────────────────────────

def _post_photo_method_b(
    page_id: str,
    access_token: str,
    image_path: str,
    message: str,
) -> Optional[dict]:
    """
    Yöntem B: Taslak fotoğraf + doğrudan feed paylaşım (zamanlama yok).

    ADIM 1: Fotoğrafı yayınlanmamış (taslak) olarak yükle
    ADIM 2: attached_media ile anında feed'e paylaş

    Yöntem A başarısız olursa denenecek alternatif.
    """
    log("📤 Yöntem B: Taslak fotoğraf + doğrudan feed paylaşım", "INFO")

    # ── ADIM 1: Fotoğrafı taslak olarak yükle ──
    photo_id = _upload_photo_unpublished(page_id, access_token, image_path)

    if not photo_id:
        log("❌ Yöntem B: Fotoğraf yüklenemedi", "ERROR")
        return None

    # ── ADIM 2: Doğrudan feed'e paylaş ──
    feed_url: str = f"{_FB_BASE_URL}/{page_id}/feed"

    log("📤 Yöntem B Adım 2: Doğrudan feed'e paylaşılıyor...", "INFO")

    try:
        post_files_payload = {
            "attached_media[0]": (None, f'{{"media_fbid":"{photo_id}"}}'),
        }

        post_data_payload = {
            "message": message,
            "access_token": access_token,
        }

        post_resp = requests.post(
            feed_url,
            files=post_files_payload,
            data=post_data_payload,
            timeout=_REQUEST_TIMEOUT,
        )

        post_json = post_resp.json()

        if "error" in post_json:
            error_info = post_json["error"]
            error_msg = error_info.get("message", "Bilinmeyen")
            error_code = error_info.get("code", 0)
            error_subcode = error_info.get("error_subcode", 0)
            log(
                f"❌ Yöntem B hatası: [{error_code}] (sub:{error_subcode}) {error_msg}",
                "ERROR",
            )
            return None

        post_id = _extract_post_id(post_json)
        if post_id:
            log(f"✅ Yöntem B başarılı: Feed post → ID={_mask_id(post_id)}", "INFO")

            log(f"⏳ Feed doğrulaması için {_VERIFY_DELAY}sn bekleniyor...", "INFO")
            time.sleep(_VERIFY_DELAY)
            _verify_in_feed(page_id, access_token, post_id)

            return post_json

        log(f"⚠️ Yöntem B: Beklenmeyen yanıt: {post_json}", "WARNING")
        return None

    except requests.exceptions.Timeout:
        log("❌ Yöntem B: Zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Yöntem B hatası: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# YÖNTEM C (YEDEK): photos + published=true
# ──────────────────────────────────────────────

def _post_photo_method_c(
    page_id: str,
    access_token: str,
    image_path: str,
    message: str,
) -> Optional[dict]:
    """
    Yöntem C (yedek): /{page_id}/photos + published=true.

    Bu yöntemde görsel Fotoğraflar sekmesine VE feed'e eklenir.
    Yöntem A ve B başarısız olursa en azından paylaşım yapılsın diye kullanılır.

    NOT: Bu yöntemde post Fotoğraflar sekmesinde de görünür (beklenen davranış).
    """
    url: str = f"{_FB_BASE_URL}/{page_id}/photos"

    log("📤 Yöntem C (yedek): photos + published=true", "INFO")
    log("  ⚠️ Bu yöntemde post Fotoğraflar'da da görünür", "INFO")

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
                f"❌ Yöntem C hatası: [{error_code}] {error_msg}",
                "ERROR",
            )
            return None

        post_id = _extract_post_id(response_json)
        if post_id:
            log(f"✅ Yöntem C başarılı: ID={_mask_id(post_id)}", "INFO")
            log("ℹ️ Görsel Feed + Fotoğraflar'da görünecek", "INFO")
            return response_json

        log(f"⚠️ Yöntem C: Beklenmeyen yanıt: {response_json}", "WARNING")
        return None

    except FileNotFoundError:
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None
    except requests.exceptions.Timeout:
        log("❌ Yöntem C zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Yöntem C hatası: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# ANA FONKSİYON: Fotoğraflı Paylaşım
# ──────────────────────────────────────────────

def post_photo_with_text(image_path: str, message: str) -> Optional[dict]:
    """
    Facebook sayfasına fotoğraflı post paylaşır.

    3 yöntem sırayla denenir:
      Yöntem A: Zamanlanmış gönderi — Business Suite tarzı (EN GÜVENİLİR)
      Yöntem B: Doğrudan feed paylaşım — attached_media ile
      Yöntem C: photos + published=true — yedek (Feed + Fotoğraflar)
    """
    page_id, access_token = _get_fb_credentials()

    if not page_id or not access_token:
        log("❌ Facebook kimlik bilgileri eksik", "ERROR")
        return None

    log("📤 Fotoğraflı paylaşım başlatılıyor", "INFO")
    log(f"📎 Görsel: {image_path}", "INFO")
    log(f"📝 Metin: {len(message)} karakter", "INFO")

    # ── YÖNTEM A (BİRİNCİL): Zamanlanmış gönderi ──
    log("━" * 40, "INFO")
    log("🔵 YÖNTEM A: Zamanlanmış gönderi (Business Suite tarzı)", "INFO")
    result = _post_photo_method_a(page_id, access_token, image_path, message)

    if result:
        log("🎯 YÖNTEM A BAŞARILI — Gönderi zamanlandı!", "INFO")
        return result

    # ── YÖNTEM B (İKİNCİL): Doğrudan feed paylaşım ──
    log("━" * 40, "INFO")
    log("🟡 Yöntem A başarısız, YÖNTEM B deneniyor (doğrudan feed)...", "INFO")
    result = _post_photo_method_b(page_id, access_token, image_path, message)

    if result:
        log("✅ YÖNTEM B BAŞARILI — Doğrudan feed postu!", "INFO")
        return result

    # ── YÖNTEM C (YEDEK): photos + published=true ──
    log("━" * 40, "INFO")
    log("🟠 Yöntem B başarısız, YÖNTEM C deneniyor (photos + published=true)...", "INFO")
    result = _post_photo_method_c(page_id, access_token, image_path, message)

    if result:
        log("✅ YÖNTEM C BAŞARILI — Görsel paylaşıldı (Feed + Fotoğraflar)", "INFO")
        return result

    log("━" * 40, "INFO")
    log("❌ Tüm görsel yöntemleri başarısız (A, B, C)", "ERROR")
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

    log("📤 Sadece metin paylaşımı gönderiliyor", "INFO")
    log(f"📝 Metin: {len(message)} karakter", "INFO")

    try:
        response = requests.post(
            url,
            data={
                "message": message,
                "access_token": access_token,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        result: dict = response.json()

        if "error" in result:
            err = result["error"]
            log(
                f"❌ Facebook API hatası: [{err.get('code', 0)}] "
                f"{err.get('type', '')} — {err.get('message', '')}",
                "ERROR",
            )
            return None

        post_id = _extract_post_id(result)
        if post_id:
            log(
                f"✅ Metin paylaşımı başarılı: ID={_mask_id(post_id)}",
                "INFO",
            )
            return result

        log(f"⚠️ Beklenmeyen yanıt: {result}", "WARNING")
        return None

    except requests.exceptions.Timeout:
        log(f"❌ Zaman aşımı ({_REQUEST_TIMEOUT}sn)", "ERROR")
        return None
    except requests.exceptions.RequestException as e:
        log(f"❌ HTTP istek hatası: {e}", "ERROR")
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
      1. Görsel varsa → fotoğraflı paylaş (A: zamanlanmış, B: doğrudan, C: yedek)
      2. Fotoğraflı başarısızsa → sadece metin paylaşımı dene
      3. İlk deneme başarısızsa → 5 saniye bekleyip tekrar dene
      4. Başarılıysa → paylaşımı kaydet
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
        log(
            f"🖼️ Görsel mevcut: {image_path} (kaynak: {image_source})",
            "INFO",
        )
    else:
        if image_path:
            log(f"⚠️ Görsel dosyası bulunamadı: {image_path}", "WARNING")
        else:
            log("ℹ️ Görsel yok, sadece metin paylaşılacak", "INFO")

    # ── Post metnini logla ──
    text_preview: str = (
        post_text[:200] + "..." if len(post_text) > 200 else post_text
    )
    log(f"📝 Post metni önizleme:\n{text_preview}", "INFO")

    # ── İlk deneme ──
    fb_response: Optional[dict] = None

    if has_image:
        log("📤 Deneme 1/2: Fotoğraflı paylaşım (3 yöntem)...", "INFO")
        fb_response = post_photo_with_text(image_path, post_text)

        if fb_response is None:
            log(
                "⚠️ Fotoğraflı paylaşım başarısız, metin olarak deneniyor...",
                "WARNING",
            )
            fb_response = post_text_only(post_text)
    else:
        log("📤 Deneme 1/2: Metin paylaşımı...", "INFO")
        fb_response = post_text_only(post_text)

    # ── İlk deneme başarısız → tekrar dene ──
    if fb_response is None:
        log(
            f"⚠️ İlk deneme başarısız, {_RETRY_DELAY} saniye beklenip "
            f"tekrar denenecek...",
            "WARNING",
        )
        time.sleep(_RETRY_DELAY)

        if has_image:
            log("📤 Deneme 2/2: Fotoğraflı paylaşım (tekrar)...", "INFO")
            fb_response = post_photo_with_text(image_path, post_text)

            if fb_response is None:
                log(
                    "📤 Deneme 2/2: Son çare — sadece metin...",
                    "INFO",
                )
                fb_response = post_text_only(post_text)
        else:
            log("📤 Deneme 2/2: Metin paylaşımı (tekrar)...", "INFO")
            fb_response = post_text_only(post_text)

    # ── Sonuç kontrolü ──
    if fb_response is None:
        log(
            f"❌ Facebook paylaşımı BAŞARISIZ (tüm denemeler): {title}",
            "ERROR",
        )
        log(separator, "INFO")
        return False

    # ── Başarılı — kaydı tut ──
    fb_post_id: str = _extract_post_id(fb_response)

    record_posted(article, fb_response, image_source)

    log(separator, "INFO")
    log(
        f"🎉 BAŞARIYLA PAYLAŞILDI: {title} | FB ID: {_mask_id(fb_post_id)}",
        "INFO",
    )
    log(separator, "INFO")

    return True
