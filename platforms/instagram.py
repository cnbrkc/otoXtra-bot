"""
platforms/instagram.py - Instagram Graph API katmani (v1.1 - Story Support)
  - Story (Hikaye) paylasimi yapar.
  - ImgBB/Catbox/0x0.st gibi ucretsiz servislerle local gorseli public URL yapar.
  - Instagram API'nin zorunlu "Container Processing" bekleme suresini yonetir.
"""

import base64
import os
import time
import requests
from core.logger import log

# ── Instagram API ────────────────────────────────────────────────────────────
_IG_API_VERSION = "v20.0"
_BASE_URL = f"https://graph.facebook.com/{_IG_API_VERSION}"
_REQUEST_TIMEOUT = 60

# ── Upload servis limitleri ──────────────────────────────────────────────────
_IMGBB_API_URL = "https://api.imgbb.com/1/upload"
_IMGBB_MAX_FILE_SIZE = 32 * 1024 * 1024

_CATBOX_API_URL = "https://catbox.moe/user/api.php"
_CATBOX_MAX_FILE_SIZE = 200 * 1024 * 1024

_ZER0X_API_URL = "https://0x0.st"
_ZER0X_MAX_FILE_SIZE = 512 * 1024 * 1024

_TELEGRAPH_API_URL = "https://telegra.ph/upload"
_TELEGRAPH_MAX_FILE_SIZE = 5 * 1024 * 1024

_UPLOAD_USER_AGENT = "otoXtraBot/5.0"

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
# UPLOAD SERVISLERI 
# ═══════════════════════════════════════════════════════════════════════════════

def _upload_imgbb(image_path: str) -> str | None:
    api_key = os.environ.get("IMGBB_API_KEY", "").strip()
    if not api_key or not image_path or not os.path.exists(image_path): return None
    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
        resp = requests.post(_IMGBB_API_URL, data={"key": api_key, "image": image_data}, headers={"User-Agent": _UPLOAD_USER_AGENT}, timeout=30)
        if resp.status_code == 200 and resp.json().get("success"):
            url = resp.json()["data"].get("url")
            log(f"ImgBB: Basarili! URL={url[:60]}")
            return url
        return None
    except Exception as e:
        log(f"ImgBB: Hata: {e}", "WARNING"); return None

def _upload_catbox(image_path: str) -> str | None:
    if not image_path or not os.path.exists(image_path): return None
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(_CATBOX_API_URL, data={"reqtype": "fileupload"}, files={"fileToUpload": f}, headers={"User-Agent": _UPLOAD_USER_AGENT}, timeout=30)
        if resp.status_code == 200 and resp.text.strip().startswith("https://"):
            log(f"Catbox: Basarili! URL={resp.text.strip()[:60]}")
            return resp.text.strip()
        return None
    except Exception as e:
        log(f"Catbox: Hata: {e}", "WARNING"); return None

def _upload_0x0(image_path: str) -> str | None:
    if not image_path or not os.path.exists(image_path): return None
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(_ZER0X_API_URL, files={"file": f}, headers={"User-Agent": _UPLOAD_USER_AGENT}, timeout=30)
        if resp.status_code == 200 and resp.text.strip().startswith("https://"):
            log(f"0x0.st: Basarili! URL={resp.text.strip()[:60]}")
            return resp.text.strip()
        return None
    except Exception as e:
        log(f"0x0.st: Hata: {e}", "WARNING"); return None

def _upload_telegraph(image_path: str) -> str | None:
    if not image_path or not os.path.exists(image_path): return None
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(_TELEGRAPH_API_URL, files={"file": ("image.jpg", f, "image/jpeg")}, headers={"User-Agent": _UPLOAD_USER_AGENT}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data and "src" in data[0]:
                url = f"https://telegra.ph{data[0]['src']}"
                log(f"Telegraph: Basarili! URL={url[:60]}")
                return url
        return None
    except Exception as e:
        log(f"Telegraph: Hata: {e}", "WARNING"); return None

def _get_public_url(image_path: str) -> str | None:
    """Local dosyayı public URL'ye çevirir (Fallback zinciri)."""
    if not image_path or not os.path.exists(image_path):
        return None
        
    file_size = os.path.getsize(image_path)
    
    # F4 Düzeltmesi: ImgBB (en IG-uyumlu) başa alındı ve boyut limitleri uygulandı.
    upload_services = [
        ("ImgBB", _upload_imgbb, _IMGBB_MAX_FILE_SIZE),
        ("Catbox", _upload_catbox, _CATBOX_MAX_FILE_SIZE),
        ("0x0.st", _upload_0x0, _ZER0X_MAX_FILE_SIZE),
        ("Telegraph", _upload_telegraph, _TELEGRAPH_MAX_FILE_SIZE),
    ]
    
    for name, fn, limit in upload_services:
        if file_size > limit:
            log(f"IG Story Upload: {name} atlandi (boyut limiti aşıldı: {file_size // 1024}KB)", "INFO")
            continue
        log(f"IG Story Upload: {name} deneniyor...")
        url = fn(image_path)
        if url:
            return url
    return None

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

    # 1. Görseli Public URL'ye çevir
    public_url = _get_public_url(image_path)
    if not public_url:
        log("IG Story: Tum upload servisleri basarisiz oldu. Story atilamadi.", "ERROR")
        return None

    # 2. Container Oluştur (media_type=STORY)
    container_url = f"{_BASE_URL}/{ig_user_id}/media"
    container_data = {
        "media_type": "STORY",
        "image_url": public_url,
        "access_token": token,
    }

    log("IG Story: Container olusturuluyor (media_type=STORY)...")
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
