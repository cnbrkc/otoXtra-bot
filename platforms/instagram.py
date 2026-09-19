"""
platforms/instagram.py - Instagram Graph API katmani (v1.5 - Multi-host Retry Fix)
  - Story (Hikaye) paylasimi yapar.
  - API Host URL graph.instagram.com olarak guncellendi (Instagram Login tokenlari icin).
  - media_type STORIES olarak duzeltilmis (Meta dokumaninda belirtildigi uzere).
  - v1.3: Gorsel yukleme fonksiyonlari core/image_uploader.py'a tasindi (DRY)
  - v1.4: Tekrar eden upload fonksiyonlari kaldirildi, merkezi modül kullaniliyor.
  - v1.5: Meta'nin bazi upload servislerinden (ozellikle ImgBB) gorseli
    cekemedigi ("Media download has failed") durumlar icin, container
    olusturma basarisiz olursa otomatik olarak bir sonraki public URL
    servisi denenir (en fazla 3 farkli host).
"""

import os
import time
import requests
from typing import Optional

from core.logger import log
from core.image_uploader import get_public_url_with_host

# ── Instagram API Sabitleri ──────────────────────────────────────────────────
_IG_API_VERSION = "v21.0"
_BASE_URL = f"https://graph.instagram.com/{_IG_API_VERSION}"
_REQUEST_TIMEOUT = 60
_MAX_HOST_ATTEMPTS = 5  # kac farkli upload servisi denenecek (mevcut tum servisler)

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


def _create_story_container(ig_user_id: str, token: str, public_url: str):
    """
    Instagram'da STORIES container olusturmayi dener.
    Donus: (container_id, is_media_fetch_error)
      - container_id: basarili ise ID, degilse None
      - is_media_fetch_error: Meta'nin gorseli cekemedigi (retry edilebilir)
        bir hata mi, yoksa baska/kalici bir hata mi
    """
    container_url = f"{_BASE_URL}/{ig_user_id}/media"
    container_data = {
        "media_type": "STORIES",
        "image_url": public_url,
        "access_token": token,
    }

    try:
        resp = requests.post(container_url, data=container_data, timeout=_REQUEST_TIMEOUT)
        result = resp.json()

        if resp.status_code == 200 and "id" in result:
            return result["id"], False

        error = result.get("error", result)
        log(f"IG Story Container hatasi: {error}", "ERROR")

        # Meta'nin gorseli cekemedigi/kabul etmedigi hatalar (retry edilebilir).
        # code=9004 Meta'nin "medya" hata ailesi - farkli subcode'lar farkli
        # sebeplerle gelebilir (2207052=fetch basarisiz, 2207083=format
        # desteklenmiyor, vb.) ama hepsi host degistirerek cozulebilir.
        error_code = error.get("code") if isinstance(error, dict) else None
        error_msg = str(error.get("message", "")) if isinstance(error, dict) else ""
        error_user_msg = str(error.get("error_user_msg", "")) if isinstance(error, dict) else ""
        is_fetch_error = (
            error_code == 9004
            or "media download has failed" in error_msg.lower()
            or "could not be fetched" in error_user_msg.lower()
            or "format is not supported" in error_msg.lower()
        )
        return None, is_fetch_error

    except Exception as e:
        log(f"IG Story Container request hatasi: {e}", "ERROR")
        return None, False


# ═══════════════════════════════════════════════════════════════════════════════
# INSTAGRAM STORY PUBLISH
# ═══════════════════════════════════════════════════════════════════════════════

def post_story(image_path: str) -> str | None:
    """
    Verilen yerel gorseli Instagram'a Hikaye (Story) olarak yukler.
    Meta bir upload servisinin (ornegin ImgBB) verdigi URL'den gorseli
    cekemezse, otomatik olarak baska bir servisle tekrar dener.
    """
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None

    if not image_path or not os.path.exists(image_path):
        log(f"IG Story: Gorsel bulunamadi: {image_path}", "ERROR")
        return None

    container_id = None
    tried_hosts: set = set()

    for attempt in range(1, _MAX_HOST_ATTEMPTS + 1):
        # 1. Görseli Public URL'ye çevir (daha once denenmemis bir servisle)
        result = get_public_url_with_host(image_path, platform_name="Instagram", exclude=tried_hosts)
        if not result:
            log("IG Story: Tum upload servisleri basarisiz oldu. Story atilamadi.", "ERROR")
            return None

        public_url, host_name = result
        tried_hosts.add(host_name)

        log(f"IG Story: Container olusturuluyor (media_type=STORIES, host={host_name}, deneme={attempt}/{_MAX_HOST_ATTEMPTS})...")
        container_id, is_fetch_error = _create_story_container(ig_user_id, token, public_url)

        if container_id:
            log(f"IG Story: Container olusturuldu! ID={container_id} (host={host_name})")
            break

        if is_fetch_error and attempt < _MAX_HOST_ATTEMPTS:
            log(f"IG Story: {host_name} Meta tarafindan reddedildi, baska host deneniyor...", "WARNING")
            continue

        # Fetch-disi bir hata (ornegin token/izin sorunu) ise tekrar denemenin anlami yok
        return None

    if not container_id:
        log("IG Story: Tum host denemeleri basarisiz oldu.", "ERROR")
        return None

    # 2. Instagram'ın Görseli İşlemesini Bekle (Polling)
    log("IG Story: Instagram islem tamamlana kadar bekleniyor...")
    status_url = f"{_BASE_URL}/{container_id}?fields=status_code&access_token={token}"

    for attempt in range(1, 11):  # Max 10 deneme (yaklasik 30 saniye)
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

    # 3. Yayınla (Publish)
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
