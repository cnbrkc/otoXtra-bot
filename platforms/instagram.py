"""
platforms/instagram.py - Instagram Graph API katmani (v1.4 - DRY Refactoring Fix)
  - Story (Hikaye) paylasimi yapar.
  - API Host URL graph.instagram.com olarak guncellendi (Instagram Login tokenlari icin).
  - media_type STORIES olarak duzeltilmis (Meta dokumaninda belirtildigi uzere).
  - v1.3: Gorsel yukleme fonksiyonlari core/image_uploader.py'a tasindi (DRY)
  - v1.4: Tekrar eden upload fonksiyonlari kaldirildi, merkezi modül kullaniliyor.
"""

import os
import time
import requests
from typing import Optional

from core.logger import log
from core.image_uploader import get_public_url_fallback

# ── Instagram API Sabitleri ──────────────────────────────────────────────────
# F1 Düzeltmesi: graph.facebook.com yerine graph.instagram.com kullanıldı (Instagram Login tokenları için zorunlu)
_IG_API_VERSION = "v21.0"
_BASE_URL = f"https://graph.instagram.com/{_IG_API_VERSION}"
_REQUEST_TIMEOUT = 60

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

    # 1. Görseli Public URL'ye çevir (merkezi modülden)
    public_url = get_public_url_fallback(image_path, platform_name="Instagram")
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
