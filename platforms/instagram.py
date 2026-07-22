"""
platforms/instagram.py - Instagram Graph API katmani (v1.5 - Bulletproof Upload)
  - Story (Hikaye) paylasimi yapar.
  - API Host URL graph.instagram.com olarak guncellendi (Instagram Login tokenlari icin).
  - media_type STORIES olarak duzeltilmis (Meta dokumaninda belirtildigi uzere).
  - v1.5: GitHub IP ban'larini asmak icin yeni guvenilir upload servisleri (tmpfiles, freeimage) eklendi.
"""

import os
import time
import requests
from typing import Optional

from core.logger import log

# ── Instagram API Sabitleri ──────────────────────────────────────────────────
_IG_API_VERSION = "v21.0"
_BASE_URL = f"https://graph.instagram.com/{_IG_API_VERSION}"
_REQUEST_TIMEOUT = 60

# ═══════════════════════════════════════════════════════════════════════════════
# YENI GUVENILIR GORSEL UPLOAD SERVISLERI
# ═══════════════════════════════════════════════════════════════════════════════

def _upload_to_tmpfiles(image_path: str) -> Optional[str]:
    try:
        log("Instagram Upload: tmpfiles.org deneniyor...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) otoXtraBot/1.0"}
        with open(image_path, "rb") as f:
            resp = requests.post("https://tmpfiles.org/api/v1/upload", files={"file": f}, headers=headers, timeout=30)
        if resp.status_code == 200:
            url = resp.json().get("data", {}).get("url", "")
            if url:
                log(f"tmpfiles.org: Basarili! URL={url}")
                return url
        log(f"tmpfiles.org: Basarisiz status={resp.status_code} body={resp.text[:100]}")
    except Exception as e:
        log(f"tmpfiles.org: Hata {e}", "WARNING")
    return None

def _upload_to_freeimage(image_path: str) -> Optional[str]:
    try:
        log("Instagram Upload: freeimage.host deneniyor...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) otoXtraBot/1.0"}
        # freeimage.host anonim public key
        with open(image_path, "rb") as f:
            resp = requests.post(
                "https://freeimage.host/api/1/upload",
                params={"key": "6d207e02198a847aa98d0a2a901485a5"},
                files={"source": f},
                headers=headers,
                timeout=30
            )
        if resp.status_code == 200:
            url = resp.json().get("image", {}).get("url", "")
            if url:
                log(f"freeimage.host: Basarili! URL={url}")
                return url
        log(f"freeimage.host: Basarisiz status={resp.status_code} body={resp.text[:100]}")
    except Exception as e:
        log(f"freeimage.host: Hata {e}", "WARNING")
    return None

def _upload_to_catbox_fixed(image_path: str) -> Optional[str]:
    try:
        log("Instagram Upload: Catbox deneniyor...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) otoXtraBot/1.0"}
        with open(image_path, "rb") as f:
            resp = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": f},
                headers=headers,
                timeout=30
            )
        if resp.status_code == 200 and "https://" in resp.text:
            log(f"Catbox: Basarili! URL={resp.text.strip()}")
            return resp.text.strip()
        log(f"Catbox: Basarisiz status={resp.status_code} body={resp.text[:100]}")
    except Exception as e:
        log(f"Catbox: Hata {e}", "WARNING")
    return None

def _upload_to_imgbb_safe(image_path: str) -> Optional[str]:
    api_key = os.environ.get("IMGBB_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        log("Instagram Upload: ImgBB deneniyor...")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) otoXtraBot/1.0"}
        with open(image_path, "rb") as f:
            resp = requests.post(
                "https://api.imgbb.com/1/upload",
                params={"key": api_key},
                files={"image": f},
                headers=headers,
                timeout=30
            )
        if resp.status_code == 200:
            url = resp.json().get("data", {}).get("url", "")
            if url:
                log(f"ImgBB: Basarili! URL={url}")
                return url
        log(f"ImgBB: Basarisiz status={resp.status_code} body={resp.text[:100]}")
    except Exception as e:
        log(f"ImgBB: Hata {e}", "WARNING")
    return None

def _get_public_url_for_instagram(image_path: str) -> Optional[str]:
    """Instagram icin guvenilir public URL uretir."""
    # Sira: ImgBB (varsa) -> tmpfiles -> freeimage -> catbox
    uploaders = [_upload_to_imgbb_safe, _upload_to_tmpfiles, _upload_to_freeimage, _upload_to_catbox_fixed]
    for uploader in uploaders:
        url = uploader(image_path)
        if url:
            return url
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# CREDENTIALS & HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_credentials():
    user_id = os.environ.get("IG_USER_ID", "").strip()
    token = os.environ.get("IG_ACCESS_TOKEN", "")

    token = (
        token.replace('"', "").replace("'", "")
        .replace("\n", "").replace("\r", "").replace(" ", "")
        .strip()
    )

    if not user_id:
        log("IG_USER_ID env bulunamadi", "ERROR")
    else:
        log(f"IG_USER_ID okundu: {user_id}")

    if not token:
        log("IG_ACCESS_TOKEN env bulunamadi", "ERROR")
    else:
        log(f"IG_ACCESS_TOKEN okundu: uzunluk={len(token)}")

    return user_id, token

# ═══════════════════════════════════════════════════════════════════════════════
# INSTAGRAM STORY PUBLISH
# ═══════════════════════════════════════════════════════════════════════════════

def post_story(image_path: str) -> str | None:
    """
    Verilen yerel gorseli Instagram'a Hikaye (Story) olarak yukler.
    """
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None

    if not image_path or not os.path.exists(image_path):
        log(f"IG Story: Gorsel bulunamadi: {image_path}", "ERROR")
        return None

    # 1. Görseli Public URL'ye çevir (Yeni Guvenilir Servisler ile)
    public_url = _get_public_url_for_instagram(image_path)
    if not public_url:
        log("IG Story: Tum upload servisleri basarisiz oldu. Story atilamadi.", "ERROR")
        return None

    # 2. Container Oluştur (media_type=STORIES - Dokümana göre düzeltildi)
    container_url = f"{_BASE_URL}/{ig_user_id}/media"
    container_data = {
        "media_type": "STORIES", # Dokümanda STORIES olarak geçiyor.
        "image_url": public_url,
        "access_token": token,
    }

    log("IG Story: Container olusturuluyor (media_type=STORIES)...")
    try:
        resp = requests.post(container_url, data=container_data, timeout=_REQUEST_TIMEOUT)
        result = resp.json()
        
        if resp.status_code != 200 or "id" not in result:
            log(f"IG Story Container hatasi: {result.get('error', result)}", "ERROR")
            return None
            
        container_id = result["id"]
        log(f"IG Story: Container olusturuldu! ID={container_id}")
    except Exception as e:
        log(f"IG Story Container request hatasi: {e}", "ERROR")
        return None

    # 3. Instagram'ın Görseli İşlemesini Bekle (Polling)
    log("IG Story: Instagram islem tamamlana kadar bekleniyor...")
    status_url = f"{_BASE_URL}/{container_id}?fields=status_code&access_token={token}"
    
    for attempt in range(1, 11): # Max 10 deneme (yaklasik 30 saniye)
        time.sleep(3)
        try:
            status_resp = requests.get(status_url, timeout=10)
            status_data = status_resp.json()
            status = status_data.get("status_code")
            
            if status == "FINISHED":
                log("IG Story: Islem tamamlandi (FINISHED).")
                break
            elif status == "ERROR":
                log("IG Story: Instagram isleme hatasi (ERROR).", "ERROR")
                return None
            else:
                log(f"IG Story: Henuz isleniyor (IN_PROGRESS) - Deneme {attempt}/10")
        except Exception:
            pass
    else:
        log("IG Story: Islem zaman asimina ugradi.", "ERROR")
        return None

    # 4. Yayınla (Publish)
    publish_url = f"{_BASE_URL}/{ig_user_id}/media_publish"
    publish_data = {
        "creation_id": container_id,
        "access_token": token,
    }

    log("IG Story: Yayinlaniyor (media_publish)...")
    try:
        publish_resp = requests.post(publish_url, data=publish_data, timeout=_REQUEST_TIMEOUT)
        publish_result = publish_resp.json()
        
        if publish_resp.status_code == 200 and "id" in publish_result:
            story_id = publish_result["id"]
            log(f"IG Story BASARIYLA yayinlandi! Story ID={story_id}")
            return story_id
        else:
            log(f"IG Story Publish hatasi: {publish_result.get('error', publish_result)}", "ERROR")
            return None
    except Exception as e:
        log(f"IG Story Publish request hatasi: {e}", "ERROR")
        return None
