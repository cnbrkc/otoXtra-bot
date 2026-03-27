"""
facebook_poster.py — Facebook Sayfa Paylaşım Modülü (v5 — Direct Photo + Feed Verify)

v5 DEĞİŞİKLİK (v4'ten farklar):

  ❌ KALDIRILAN: 2 adımlı yöntem (published=false → attached_media)
     → Bu yöntem postları sadece Fotoğraflar'a atıyordu, Feed'de göstermiyordu
     → Sebep: published=false ile yüklenen fotoğraf bazen doğrudan yayınlanıyor,
       ardından attached_media bağlantısı kopuyor

  ✅ YENİ BİRİNCİL YÖNTEM: Tek çağrı ile fotoğraf paylaşımı
     POST /{page_id}/photos  →  source + message + published=true
     → Fotoğraf hem Feed'de hem Fotoğraflar'da görünür (bu normal davranış)
     → API yanıtında "post_id" varsa → feed story kesin oluşturulmuş demek

  ✅ FEED DOĞRULAMA: Paylaşımdan sonra post'un feed'de göründüğü kontrol edilir
     → Sonuç loglara yazılır (teşhis amaçlı)

  ✅ YEDEK YÖNTEM: Fotoğraf başarısızsa sadece metin paylaşımı (/{page_id}/feed)

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

# HTTP istek zaman aşımı (saniye)
_REQUEST_TIMEOUT: int = 60

# Başarısız paylaşımda tekrar denemeden önce bekleme süresi (saniye)
_RETRY_DELAY: int = 5

# Feed doğrulama için bekleme süresi (Facebook indekslesin)
_VERIFY_DELAY: int = 3


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
    """Facebook API yanıtından post ID'sini çıkarır.

    /{page_id}/photos yanıtı:  {"id": "PHOTO_ID", "post_id": "PAGE_POST_ID"}
    /{page_id}/feed yanıtı:    {"id": "PAGE_POST_ID"}

    post_id varsa onu döndürür (feed story ID'si), yoksa id döndürür.
    """
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

    Feed'in son 5 postuna bakarak target_id ile eşleşme arar.
    Bu fonksiyon sadece TEŞHİS amaçlıdır — sonucu loglara yazar.
    Başarısız olması paylaşımı geçersiz KILMAZ.
    """
    try:
        resp = requests.get(
            f"{_FB_BASE_URL}/{page_id}/feed",
            params={
                "access_token": access_token,
                "limit": 5,
                "fields": "id",
            },
            timeout=30,
        )
        data = resp.json()

        if "error" in data:
            err_msg = data["error"].get("message", "Bilinmeyen")
            log(f"⚠️ Feed doğrulama API hatası: {err_msg}", "WARNING")
            return False

        feed_ids = [p.get("id", "") for p in data.get("data", [])]

        # Tam eşleşme
        if target_id in feed_ids:
            log(
                "✅ FEED DOĞRULAMA: Post feed'de BULUNDU!",
                "INFO",
            )
            return True

        # Kısmi eşleşme (PAGE_ID_PHOTO_ID formatı farklı olabilir)
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
# Fotoğraflı Paylaşım (TEK ÇAĞRI — BİRİNCİL YÖNTEM)
# ──────────────────────────────────────────────

def post_photo_with_text(image_path: str, message: str) -> Optional[dict]:
    """
    Facebook sayfasına fotoğraflı post paylaşır.

    TEK API ÇAĞRISI:
        POST /{page_id}/photos
            source    = <resim dosyası>
            message   = <post metni>
            published = true

    Bu yöntem:
        ✅ Feed'de (ana sayfada) görsel post oluşturur
        ✅ Fotoğraflar albümüne de ekler (tüm foto postlar için normal)
        ✅ Takipçilerin akışında görünür

    API yanıtında "post_id" dönerse → feed story KESİN oluşturulmuş demek.
    Sadece "id" dönerse → fotoğraf yüklendi, feed durumu belirsiz.

    Paylaşımdan sonra feed doğrulaması yapılır (teşhis amaçlı).

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

    url: str = f"{_FB_BASE_URL}/{page_id}/photos"

    log("📤 Fotoğraflı paylaşım başlatılıyor (tek çağrı yöntemi)", "INFO")
    log(f"📎 Görsel: {image_path}", "INFO")
    log(f"📝 Metin: {len(message)} karakter", "INFO")
    log("━" * 40, "INFO")
    log(
        f"🔵 POST /{page_id}/photos  (source + message + published=true)",
        "INFO",
    )

    try:
        with open(image_path, "rb") as img_file:
            response = requests.post(
                url,
                files={"source": img_file},
                data={
                    "message": message,
                    "published": "true",
                    "access_token": access_token,
                },
                timeout=_REQUEST_TIMEOUT,
            )

        result: dict = response.json()

        # ── Hata kontrolü ──
        if "error" in result:
            err = result["error"]
            log(
                f"❌ Facebook API hatası: [{err.get('code', 0)}] "
                f"{err.get('message', 'Bilinmeyen')}",
                "ERROR",
            )
            return None

        photo_id: str = result.get("id", "")
        post_id: str = result.get("post_id", "")

        # ── post_id döndüyse → feed story oluşturulmuş ──
        if post_id:
            log("✅ Fotoğraflı post BAŞARILI!", "INFO")
            log(f"  📸 photo_id : {photo_id}", "INFO")
            log(f"  📰 post_id  : {_mask_id(post_id)}", "INFO")
            log(
                "🎯 Feed story OLUŞTURULDU — post ANA SAYFADA görünecek!",
                "INFO",
            )

            # Feed doğrulama (Facebook indekslesin diye kısa bekleme)
            log(
                f"⏳ Feed doğrulaması için {_VERIFY_DELAY}sn bekleniyor...",
                "INFO",
            )
            time.sleep(_VERIFY_DELAY)
            _verify_in_feed(page_id, access_token, post_id)

            return result

        # ── Sadece photo_id döndüyse → fotoğraf yüklendi, feed belirsiz ──
        if photo_id:
            log(
                f"⚠️ Fotoğraf yüklendi (photo_id={photo_id}) "
                f"ama post_id dönmedi",
                "WARNING",
            )
            log(
                "⚠️ Fotoğraf albüme eklendi — feed görünürlüğü belirsiz",
                "WARNING",
            )

            # Yine de kontrol et (constructed ID ile)
            constructed_id: str = f"{page_id}_{photo_id}"
            log(
                f"⏳ Feed doğrulaması deneniyor ({_VERIFY_DELAY}sn)...",
                "INFO",
            )
            time.sleep(_VERIFY_DELAY)
            _verify_in_feed(page_id, access_token, constructed_id)

            # Fotoğraf yüklendi, result'ı döndür (kayıt tutulsun)
            return result

        # ── Hiçbir ID dönmediyse ──
        log(f"⚠️ Beklenmeyen API yanıtı: {result}", "WARNING")
        return None

    except FileNotFoundError:
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None
    except requests.exceptions.Timeout:
        log(f"❌ İstek zaman aşımı ({_REQUEST_TIMEOUT}sn)", "ERROR")
        return None
    except requests.exceptions.RequestException as e:
        log(f"❌ HTTP istek hatası: {e}", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Beklenmeyen hata: {e}", "ERROR")
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
      1. Görsel varsa → fotoğraflı paylaş (tek çağrı)
      2. Fotoğraflı başarısızsa → sadece metin paylaşımı dene
      3. İlk deneme başarısızsa → 5 saniye bekleyip tekrar dene
      4. Başarılıysa → paylaşımı kaydet

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

        # Fotoğraflı başarısızsa metin olarak dene
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
