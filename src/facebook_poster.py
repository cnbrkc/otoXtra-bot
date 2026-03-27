"""
facebook_poster.py — Facebook Sayfa Paylaşım Modülü (v3 — Timeline Fix)

v3 Değişiklik:
  - Fotoğraflı paylaşımda 3 yöntem sırayla denenir:
    Yöntem A: /{page_id}/photos + published=true (en basit, direkt timeline)
              → message parametresi ile metin de eklenir
              → Facebook bunu timeline'a atar (photos endpoint AMA published=true)
    Yöntem B: Görsel URL ile /{page_id}/feed (url parametresi)
    Yöntem C: Sadece metin /{page_id}/feed (son çare)
  
  NOT: Eski v2'deki "published=false + attached_media" yöntemi
  bazı sayfalarda Fotoğraflar albümüne düşürüyordu.
  
  v3'te photos endpoint'i published=true ile kullanılıyor.
  Bu yöntemde görsel + metin birlikte timeline'da görünür.
  Facebook'un kendi dokümantasyonuna göre /{page_id}/photos 
  endpoint'ine published=true (varsayılan) ile yapılan paylaşımlar
  hem Fotoğraflar albümüne EKLENİR hem de timeline'da GÖRÜNÜR.
  
  Sorun: Eğer sayfanın ayarlarında "Fotoğraf paylaşımlarını 
  timeline'da göster" kapalıysa, sadece albüme düşer.
  
  ÇÖZÜM: Bu durumda Yöntem B devreye girer (feed + görsel URL).

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

_FB_API_VERSION: str = "v19.0"
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


def _check_timeline_visibility(post_id: str, access_token: str) -> bool:
    """
    Paylaşılan postun timeline'da görünüp görünmediğini kontrol eder.
    
    Facebook Graph API ile post'un detaylarını çeker.
    Post bulunabiliyorsa timeline'da var demektir.
    
    Args:
        post_id: Facebook post ID'si.
        access_token: Sayfa erişim tokenı.
    
    Returns:
        True: Post timeline'da görünüyor.
        False: Kontrol edilemedi veya görünmüyor.
    """
    if not post_id:
        return False
    
    try:
        check_url = f"{_FB_BASE_URL}/{post_id}"
        params = {
            "fields": "id,message,created_time",
            "access_token": access_token,
        }
        
        resp = requests.get(check_url, params=params, timeout=10)
        resp_json = resp.json()
        
        if "error" in resp_json:
            log(f"⚠️ Timeline kontrol hatası: {resp_json['error'].get('message', '')}", "WARNING")
            return False
        
        if resp_json.get("id"):
            log(f"✅ Post timeline'da doğrulandı: {resp_json.get('id')}", "INFO")
            return True
        
        return False
        
    except Exception as e:
        log(f"⚠️ Timeline kontrol hatası: {e}", "WARNING")
        return False


# ──────────────────────────────────────────────
# YÖNTEM A: photos + published=true
# ──────────────────────────────────────────────

def _post_photo_method_a(
    page_id: str,
    access_token: str,
    image_path: str,
    message: str,
) -> Optional[dict]:
    """
    Yöntem A: /{page_id}/photos endpoint'ine published=true ile gönder.
    
    Facebook dokümantasyonuna göre:
    - published=true (varsayılan) → Görsel hem albüme eklenir HEM timeline'da görünür
    - published=false → Sadece albüme eklenir, timeline'da görünmez
    
    ÖNCEKİ SORUN: published parametresi gönderilmiyordu,
    Facebook varsayılan olarak true kabul eder ama bazen
    sayfa ayarlarına göre timeline'a eklemeyebilir.
    
    Args:
        page_id: Facebook sayfa ID.
        access_token: Sayfa erişim tokenı.
        image_path: Görsel dosya yolu.
        message: Post metni.
    
    Returns:
        Başarılıysa API yanıt dict'i, değilse None.
    """
    url: str = f"{_FB_BASE_URL}/{page_id}/photos"
    
    log(f"📤 Yöntem A: photos + published=true", "INFO")
    
    try:
        with open(image_path, "rb") as image_file:
            files = {
                "source": image_file,
            }
            data = {
                "message": message,
                "published": "true",
                "access_token": access_token,
            }
            
            response = requests.post(
                url,
                files=files,
                data=data,
                timeout=_REQUEST_TIMEOUT,
            )
        
        response_json: dict = response.json()
        
        if "error" in response_json:
            error_info = response_json["error"]
            log(
                f"❌ Yöntem A hatası: [{error_info.get('code', 0)}] "
                f"{error_info.get('message', 'Bilinmeyen')}",
                "ERROR",
            )
            return None
        
        post_id = _extract_post_id(response_json)
        if post_id:
            log(f"✅ Yöntem A başarılı: photo_id={post_id}", "INFO")
            
            # Timeline'da görünüp görünmediğini kontrol et
            # photos endpoint'i page_id_photo_id formatında döner
            # Feed'deki post ID'si farklı olabilir
            # Ama genelde timeline'da görünür
            
            return response_json
        
        log(f"⚠️ Yöntem A: Beklenmeyen yanıt: {response_json}", "WARNING")
        return None
        
    except FileNotFoundError:
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None
    except requests.exceptions.Timeout:
        log(f"❌ Yöntem A zaman aşımı", "ERROR")
        return None
    except requests.exceptions.RequestException as e:
        log(f"❌ Yöntem A istek hatası: {e}", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Yöntem A beklenmeyen hata: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# YÖNTEM B: feed + görsel URL
# ──────────────────────────────────────────────

def _upload_photo_get_url(
    page_id: str,
    access_token: str,
    image_path: str,
) -> Optional[str]:
    """
    Görseli Facebook'a yükler ve URL'sini döner.
    
    published=false ile yükler, sonra photo ID üzerinden
    URL'yi çeker.
    
    Returns:
        Görsel URL'si veya None.
    """
    url: str = f"{_FB_BASE_URL}/{page_id}/photos"
    
    try:
        with open(image_path, "rb") as image_file:
            files = {"source": image_file}
            data = {
                "published": "false",
                "access_token": access_token,
            }
            
            response = requests.post(
                url, files=files, data=data, timeout=_REQUEST_TIMEOUT
            )
        
        response_json = response.json()
        
        if "error" in response_json:
            return None
        
        photo_id = response_json.get("id", "")
        if not photo_id:
            return None
        
        # Photo URL'sini çek
        photo_url_endpoint = f"{_FB_BASE_URL}/{photo_id}"
        params = {
            "fields": "images",
            "access_token": access_token,
        }
        
        photo_resp = requests.get(
            photo_url_endpoint, params=params, timeout=10
        )
        photo_data = photo_resp.json()
        
        images = photo_data.get("images", [])
        if images:
            # En büyük görselin URL'sini al
            return images[0].get("source", "")
        
        return None
        
    except Exception:
        return None


def _post_photo_method_b(
    page_id: str,
    access_token: str,
    image_path: str,
    message: str,
) -> Optional[dict]:
    """
    Yöntem B: Görseli yükle → URL al → /{page_id}/feed ile paylaş.
    
    Bu yöntem görseli feed'e "link" olarak ekler.
    Timeline'da kesinlikle görünür.
    
    Args:
        page_id: Facebook sayfa ID.
        access_token: Sayfa erişim tokenı.
        image_path: Görsel dosya yolu.
        message: Post metni.
    
    Returns:
        Başarılıysa API yanıt dict'i, değilse None.
    """
    log(f"📤 Yöntem B: feed + object_attachment", "INFO")
    
    # Adım 1: Görseli unpublished olarak yükle
    upload_url: str = f"{_FB_BASE_URL}/{page_id}/photos"
    
    try:
        with open(image_path, "rb") as image_file:
            files = {"source": image_file}
            data = {
                "published": "false",
                "temporary": "true",
                "access_token": access_token,
            }
            
            upload_resp = requests.post(
                upload_url, files=files, data=data, timeout=_REQUEST_TIMEOUT
            )
        
        upload_json = upload_resp.json()
        
        if "error" in upload_json:
            error_msg = upload_json["error"].get("message", "Bilinmeyen")
            log(f"❌ Yöntem B görsel yükleme hatası: {error_msg}", "ERROR")
            return None
        
        photo_id = upload_json.get("id", "")
        if not photo_id:
            log("❌ Yöntem B: photo_id alınamadı", "ERROR")
            return None
        
        log(f"✅ Yöntem B Adım 1: Görsel yüklendi → photo_id={photo_id}", "INFO")
        
    except FileNotFoundError:
        log(f"❌ Görsel dosyası bulunamadı: {image_path}", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Yöntem B görsel yükleme hatası: {e}", "ERROR")
        return None
    
    # Adım 2: Feed'e post at, object_attachment ile görseli bağla
    feed_url: str = f"{_FB_BASE_URL}/{page_id}/feed"
    
    try:
        post_data = {
            "message": message,
            "object_attachment": photo_id,
            "access_token": access_token,
        }
        
        post_resp = requests.post(
            feed_url, data=post_data, timeout=_REQUEST_TIMEOUT
        )
        
        post_json = post_resp.json()
        
        if "error" in post_json:
            error_info = post_json["error"]
            error_msg = error_info.get("message", "Bilinmeyen")
            error_code = error_info.get("code", 0)
            log(
                f"❌ Yöntem B feed hatası: [{error_code}] {error_msg}",
                "ERROR",
            )
            
            # object_attachment desteklenmiyorsa attached_media dene
            log("🔄 object_attachment başarısız, attached_media deneniyor...", "INFO")
            
            post_data_v2 = {
                "message": message,
                "attached_media[0]": json.dumps({"media_fbid": photo_id}),
                "access_token": access_token,
            }
            
            post_resp_v2 = requests.post(
                feed_url, data=post_data_v2, timeout=_REQUEST_TIMEOUT
            )
            
            post_json_v2 = post_resp_v2.json()
            
            if "error" in post_json_v2:
                error_msg_v2 = post_json_v2["error"].get("message", "")
                log(f"❌ attached_media da başarısız: {error_msg_v2}", "ERROR")
                return None
            
            pid = _extract_post_id(post_json_v2)
            if pid:
                log(f"✅ Yöntem B (attached_media) başarılı: ID={pid}", "INFO")
                return post_json_v2
            
            return None
        
        post_id = _extract_post_id(post_json)
        if post_id:
            log(f"✅ Yöntem B başarılı: ID={post_id}", "INFO")
            log(f"🎯 Post ANA SAYFADA görünecek (feed endpoint)", "INFO")
            return post_json
        
        log(f"⚠️ Yöntem B: Beklenmeyen yanıt: {post_json}", "WARNING")
        return None
        
    except requests.exceptions.Timeout:
        log(f"❌ Yöntem B feed zaman aşımı", "ERROR")
        return None
    except Exception as e:
        log(f"❌ Yöntem B feed hatası: {e}", "ERROR")
        return None


# ──────────────────────────────────────────────
# ANA FONKSİYON: Fotoğraflı Paylaşım
# ──────────────────────────────────────────────

def post_photo_with_text(image_path: str, message: str) -> Optional[dict]:
    """
    Facebook sayfasına fotoğraflı post paylaşır — ANA SAYFADA görünür.
    
    3 yöntem sırayla denenir:
      Yöntem B: feed + object_attachment (EN GÜVENLİ — önce bu)
      Yöntem A: photos + published=true (yedek)
      Metin:    Sadece metin (son çare)
    
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
    
    log(f"📤 Fotoğraflı paylaşım başlatılıyor (3 yöntemli)", "INFO")
    log(f"📎 Görsel: {image_path}", "INFO")
    log(f"📝 Metin uzunluğu: {len(message)} karakter", "INFO")
    
    # ── YÖNTEM B (ÖNCELİKLİ): feed + object_attachment ──
    log("━" * 40, "INFO")
    log("🔵 YÖNTEM B deneniyor (feed + object_attachment)...", "INFO")
    result = _post_photo_method_b(page_id, access_token, image_path, message)
    
    if result:
        log("🎯 YÖNTEM B BAŞARILI — Post feed'de (ana sayfada) görünecek", "INFO")
        return result
    
    # ── YÖNTEM A (YEDEK): photos + published=true ──
    log("━" * 40, "INFO")
    log("🟡 Yöntem B başarısız, YÖNTEM A deneniyor (photos + published=true)...", "INFO")
    result = _post_photo_method_a(page_id, access_token, image_path, message)
    
    if result:
        log("✅ YÖNTEM A BAŞARILI — Görsel paylaşıldı", "INFO")
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
    
    log(f"📤 Metin paylaşımı gönderiliyor: {url}", "INFO")
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
            log(f"✅ Metin paylaşımı başarılı: ID={post_id}", "INFO")
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
      1. Görsel varsa fotoğraflı paylaşım dene (3 yöntemli)
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
    log(f"🎉 BAŞARIYLA PAYLAŞILDI: {title} | FB ID: {fb_post_id}", "INFO")
    log(separator, "INFO")
    
    return True
