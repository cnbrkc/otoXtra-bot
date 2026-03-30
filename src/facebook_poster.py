"""
facebook_poster.py — Facebook Sayfa Paylaşım Modülü (v10 — files+data split fix)

v10 Değişiklik:

  ❌ v9 HATASI: Adım 2'de access_token ve message dahil HER ŞEY
     files= sözlüğüne konulmuştu → API bunları "dosya" gibi yorumladı
     → Facebook access_token'ı ve message'ı düzgün ayrıştıramadı

  ✅ v10 ÇÖZÜM:
     ADIM 1: (v9'dan kalma, doğru)
             params= ile published/temporary/access_token URL'ye ekleniyor
             files=  ile sadece dosya gönderiliyor

     ADIM 2: (YENİ FIX)
             files= → SADECE attached_media[0] (dosya referansı)
             data=  → message + access_token (standart metin alanları)
             → requests ikisini aynı multipart isteğinde birleştirir
             → Facebook her alanı doğru yorumlar

  ✅ YEDEK: photos + published=true (en azından paylaşım yapılsın)

Ortam değişkenleri (GitHub Secrets):
  - FB_PAGE_ID       → Facebook sayfa ID
  - FB_ACCESS_TOKEN  → Uzun süreli sayfa erişim tokenı
"""

import os
import time
from typing import Optional

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

_FB_API_VERSION: str = "v21.0"
_FB_BASE_URL: str = f"https://graph.facebook.com/{_FB_API_VERSION}"

_REQUEST_TIMEOUT: int = 60
_RETRY_DELAY: int = 5
_VERIFY_DELAY: int = 4


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
                "fields": "id,type,status_type",
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

        # Feed'deki son 5 postun tiplerini logla (teşhis)
        for p in feed_posts[:3]:
            p_id = _mask_id(p.get("id", ""))
            p_type = p.get("type", "?")
            p_status = p.get("status_type", "?")
            log(f"  📋 Feed'de: {p_id} | type={p_type} | status={p_status}", "INFO")

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

        log("⚠️ FEED DOĞRULAMA: Post feed'de BULUNAMADI!", "WARNING")
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
# YÖNTEM A (BİRİNCİL): temporary upload → feed post
# ──────────────────────────────────────────────

def _post_photo_method_a(
    page_id: str,
    access_token: str,
    image_path: str,
    message: str,
) -> Optional[dict]:
    """
    Yöntem A: Görseli GEÇİCİ olarak yükle → Feed'e attached_media ile paylaş.

    ADIM 1: POST /{page_id}/photos?published=false&temporary=true&access_token=...
              params: published, temporary, access_token → URL query string
              files:  source = <dosya> → body
            → photo_id alınır
            → Fotoğraflar'a DÜŞMEZ (temporary=true)

    ADIM 2: POST /{page_id}/feed
              files: attached_media[0] → dosya referansı olarak (multipart)
              data:  message, access_token → standart metin alanları olarak
            → requests ikisini aynı multipart isteğinde birleştirir
            → Feed'de görselli post oluşturulur

    v10 FIX:
      - Adım 1: v9'dan kalma (doğru) — params= ile URL'ye, files= ile body'ye
      - Adım 2: files= ve data= AYRILDI
        → files= SADECE attached_media[0] içeriyor (dosya referansı)
        → data= message + access_token içeriyor (standart alanlar)
        → v9'da hepsi files= içindeydi → API bunları "dosya" sanıyordu
    """
    log("📤 Yöntem A: Geçici yükleme (temporary=true) → Feed postu", "INFO")

    # ── ADIM 1: Görseli GEÇİCİ olarak yükle ──
    upload_url: str = f"{_FB_BASE_URL}/{page_id}/photos"

    try:
        # ✅ v9'dan kalma (doğru): params= ile URL query string'ine ekleniyor
        upload_params = {
            "published": "false",
            "temporary": "true",
            "access_token": access_token,
        }

        with open(image_path, "rb") as image_file:
            files_payload = {"source": image_file}

            log("📤 Adım 1: Görsel geçici olarak yükleniyor (parametreler URL'de)...", "INFO")
            log("  ℹ️ params= kullanılıyor (published/temporary URL query string'inde)", "INFO")

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
            log(
                f"❌ Yöntem A Adım 1 hatası: [{error_code}] {error_msg}",
                "ERROR",
            )
            return None

        photo_id = upload_json.get("id", "")
        if not photo_id:
            log("❌ Yöntem A: photo_id alınamadı", "ERROR")
            return None

        log(f"✅ Adım 1 OK: Geçici görsel yüklendi → photo_id={photo_id}", "INFO")
        log("  ℹ️ temporary=true (URL'de) → Bu görsel Fotoğraflar'a EKLENMEDİ", "INFO")

    except FileNotFoundError:
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None
    except requests.exceptions.Timeout:
        log("❌ Adım 1: Görsel yükleme zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Adım 1 hatası: {e}", "ERROR")
        return None

    # ── ADIM 2: Feed'e attached_media ile paylaş ──
    feed_url: str = f"{_FB_BASE_URL}/{page_id}/feed"

    try:
        # ✅ v10 FİNAL FIX: files= ve data= AYRILDI
        #
        # ÖNCEKİ (v9 — ÇALIŞMIYORDU):
        #   post_files_payload = {
        #       "message": (None, message),                              ← YANLIŞ YER
        #       "attached_media[0]": (None, f'{{"media_fbid":"..."}}'),
        #       "access_token": (None, access_token),                    ← YANLIŞ YER
        #   }
        #   requests.post(feed_url, files=post_files_payload)
        #   → message ve access_token da "dosya" gibi gönderiliyordu
        #   → Facebook bunları doğru ayrıştıramıyordu
        #
        # YENİ (v10 — FIX):
        #   files= → SADECE attached_media[0] (dosya referansı)
        #   data=  → message + access_token (standart metin alanları)
        #   → requests ikisini aynı multipart isteğinde doğru şekilde birleştirir
        #   → Facebook her alanı beklediği formatta alır

        # 1. files= → SADECE dosya referansı (attached_media)
        post_files_payload = {
            "attached_media[0]": (None, f'{{"media_fbid":"{photo_id}"}}'),
        }

        # 2. data= → Standart metin alanları (message + access_token)
        post_data_payload = {
            "message": message,
            "access_token": access_token,
        }

        log("📤 Adım 2: Feed'e files= + data= ile paylaşılıyor...", "INFO")
        log(f"  📎 [files] attached_media[0] = {{\"media_fbid\":\"{photo_id}\"}}", "INFO")
        log(f"  📝 [data]  message = {len(message)} karakter", "INFO")
        log("  🔑 [data]  access_token = *** (gizli)", "INFO")

        # ✅ files= VE data= AYNI ANDA, AYRI AYRI kullanılıyor!
        # requests her ikisini de tek bir multipart/form-data isteğinde birleştirir:
        #   - files'daki alanlar → dosya referansı olarak paketlenir
        #   - data'daki alanlar → standart metin alanları olarak paketlenir
        post_resp = requests.post(
            feed_url,
            files=post_files_payload,   # Dosya referansları için
            data=post_data_payload,     # Metin ve token gibi standart alanlar için
            timeout=_REQUEST_TIMEOUT,
        )

        post_json = post_resp.json()

        if "error" in post_json:
            error_info = post_json["error"]
            error_msg = error_info.get("message", "Bilinmeyen")
            error_code = error_info.get("code", 0)
            error_subcode = error_info.get("error_subcode", 0)
            log(
                f"❌ Yöntem A Adım 2 hatası: [{error_code}] (sub:{error_subcode}) {error_msg}",
                "ERROR",
            )
            return None

        post_id = _extract_post_id(post_json)
        if post_id:
            log(f"✅ Adım 2 OK: Feed post oluşturuldu → ID={_mask_id(post_id)}", "INFO")
            log("🎯 Post SADECE FEED'de görünecek (Fotoğraflar'a DÜŞMEYECEK)!", "INFO")

            # Feed doğrulama
            log(f"⏳ Feed doğrulaması için {_VERIFY_DELAY}sn bekleniyor...", "INFO")
            time.sleep(_VERIFY_DELAY)
            _verify_in_feed(page_id, access_token, post_id)

            return post_json

        log(f"⚠️ Yöntem A: Beklenmeyen yanıt: {post_json}", "WARNING")
        return None

    except requests.exceptions.Timeout:
        log("❌ Adım 2: Feed paylaşım zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Adım 2 hatası: {e}", "ERROR")
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

    Bu yöntemde görsel Fotoğraflar sekmesine VE feed'e eklenir.
    Yöntem A başarısız olursa en azından paylaşım yapılsın diye kullanılır.

    NOT: Bu yöntemde post Fotoğraflar sekmesinde de görünür (beklenen davranış).
    """
    url: str = f"{_FB_BASE_URL}/{page_id}/photos"

    log("📤 Yöntem B (yedek): photos + published=true", "INFO")
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
                f"❌ Yöntem B hatası: [{error_code}] {error_msg}",
                "ERROR",
            )
            return None

        post_id = _extract_post_id(response_json)
        if post_id:
            log(f"✅ Yöntem B başarılı: ID={_mask_id(post_id)}", "INFO")
            log("ℹ️ Görsel Feed + Fotoğraflar'da görünecek", "INFO")
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
    Facebook sayfasına fotoğraflı post paylaşır — SADECE FEED'de görünür.

    2 yöntem sırayla denenir:
      Yöntem A: temporary upload → feed post (SADECE FEED)
      Yöntem B: photos + published=true (Feed + Fotoğraflar — yedek)
    """
    page_id, access_token = _get_fb_credentials()

    if not page_id or not access_token:
        log("❌ Facebook kimlik bilgileri eksik", "ERROR")
        return None

    log("📤 Fotoğraflı paylaşım başlatılıyor", "INFO")
    log(f"📎 Görsel: {image_path}", "INFO")
    log(f"📝 Metin: {len(message)} karakter", "INFO")

    # ── YÖNTEM A (BİRİNCİL): temporary upload → feed post ──
    log("━" * 40, "INFO")
    log("🔵 YÖNTEM A: Geçici yükleme → Feed postu (Fotoğraflar'a DÜŞMEZ)", "INFO")
    result = _post_photo_method_a(page_id, access_token, image_path, message)

    if result:
        log("🎯 YÖNTEM A BAŞARILI — Post SADECE FEED'de!", "INFO")
        return result

    # ── YÖNTEM B (YEDEK): photos + published=true ──
    log("━" * 40, "INFO")
    log("🟡 Yöntem A başarısız, YÖNTEM B deneniyor (photos + published=true)...", "INFO")
    result = _post_photo_method_b(page_id, access_token, image_path, message)

    if result:
        log("✅ YÖNTEM B BAŞARILI — Görsel paylaşıldı (Feed + Fotoğraflar)", "INFO")
        return result

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
      1. Görsel varsa → fotoğraflı paylaş (Yöntem A: temporary, Yöntem B: yedek)
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
        log("📤 Deneme 1/2: Fotoğraflı paylaşım...", "INFO")
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
