"""
platforms/threads.py - Threads API katmani (v3.3 - Toksik Token Temizleyici)

v3.3:
  - "Cannot parse access token" (190) hatasini onlemek icin tokenin icindeki 
    tum gorunmez satir sonu, bosluk ve tirnak isaretlerini otomatik yok eder.
"""

import os
import time
import requests
from core.logger import log

_THREADS_API_VERSION = "v25.0"
_BASE_URL = f"https://graph.facebook.com/{_THREADS_API_VERSION}"
_REQUEST_TIMEOUT = 60
_RETRY_ATTEMPTS = 3
_RETRY_BASE_WAIT = 2.0


def _get_credentials():
    user_id = os.environ.get("THREADS_USER_ID", "").strip()
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")

    # 🧹 TOKSIK TEMIZLEYICI: GitHub Secrets'a yapistirirken araya giren gizli 
    # satir sonu (\n, \r), bosluk veya tirnak isaretlerini tamamen yok eder.
    token = token.replace('"', '').replace("'", "").replace("\n", "").replace("\r", "").replace(" ", "")

    if not user_id:
        log("THREADS_USER_ID env bulunamadi", "ERROR")
    else:
        log(f"THREADS_USER_ID okundu: uzunluk={len(user_id)}")

    if not token:
        log("THREADS_ACCESS_TOKEN env bulunamadi", "ERROR")
    else:
        log(f"THREADS_ACCESS_TOKEN okundu: uzunluk={len(token)}, ilk_4={token[:4]}")

    return user_id, token


def _post_with_retry(url, data, context="threads"):
    last_error = ""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            started = time.time()
            resp = requests.post(url, data=data, timeout=_REQUEST_TIMEOUT)
            elapsed = int((time.time() - started) * 1000)

            try:
                result = resp.json()
            except Exception:
                result = {"error": {"message": resp.text, "code": resp.status_code}}

            log(
                f"{context} attempt={attempt}/{_RETRY_ATTEMPTS} "
                f"status={resp.status_code} elapsed_ms={elapsed}",
                "INFO",
            )

            if resp.status_code == 200 and "id" in result:
                return result
            if "error" in result:
                err = result["error"]
                msg = err.get("error_user_msg") or err.get("message", "")
                log(f"Threads API hatasi: {err.get('code')} - {msg}", "ERROR")
                if "temporarily" in msg.lower() or err.get("code") in (4, 17, 32, 80000, 80001, 80002):
                    last_error = f"retryable: {msg}"
                else:
                    return result
            else:
                last_error = f"http {resp.status_code}"
        except Exception as e:
            last_error = str(e)
            log(f"{context} request error: {e}", "WARNING")

        if attempt < _RETRY_ATTEMPTS:
            wait = _RETRY_BASE_WAIT * (2 ** (attempt - 1))
            log(f"{context} retry wait {wait:.1f}s", "INFO")
            time.sleep(wait)

    log(f"{context} all retries failed: {last_error}", "ERROR")
    return {}


def _get_with_retry(url, params, context="threads_get"):
    last_error = ""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            started = time.time()
            resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
            elapsed = int((time.time() - started) * 1000)

            try:
                result = resp.json()
            except Exception:
                result = {"error": {"message": resp.text, "code": resp.status_code}}

            log(
                f"{context} attempt={attempt}/{_RETRY_ATTEMPTS} "
                f"status={resp.status_code} elapsed_ms={elapsed}",
                "INFO",
            )

            if resp.status_code == 200 and "id" in result:
                return result
            if "error" in result:
                err = result["error"]
                msg = err.get("error_user_msg") or err.get("message", "")
                log(f"Threads API GET hatasi: {err.get('code')} - {msg}", "ERROR")
                return result
            else:
                last_error = f"http {resp.status_code}"
        except Exception as e:
            last_error = str(e)
            log(f"{context} request error: {e}", "WARNING")

        if attempt < _RETRY_ATTEMPTS:
            wait = _RETRY_BASE_WAIT * (2 ** (attempt - 1))
            log(f"{context} retry wait {wait:.1f}s", "INFO")
            time.sleep(wait)

    log(f"{context} all retries failed: {last_error}", "ERROR")
    return {}


def _get_threads_user_id(ig_user_id, token):
    """
    Threads User ID'sini bulur.
    Yeni Threads API yapisinda /me uzerinden direkt kimlik dogrulanir.
    Eger /me basarisiz olursa, env'den gelen ID'yi (ki dogru ayarlandiysa Threads ID'sidir) kullanir.
    """
    # 1. Once /me endpoint'ini deneyerek token'in ait oldugu Threads ID'sini cekelim.
    url = f"{_BASE_URL}/me"
    params = {
        "fields": "id,username",
        "access_token": token
    }
    log("Threads User ID '/me' uzerinden kontrol ediliyor...")
    result = _get_with_retry(url, params, "get_me_profile")

    me_id = result.get("id")
    if me_id:
        log(f"Threads User ID '/me' uzerinden basariyla bulundu: {me_id} (username: {result.get('username', 'N/A')})")
        return me_id

    # 2. Eger /me cagrisi basarisiz olursa, env'den okunan ID'yi direkt Threads ID olarak kabul et.
    if ig_user_id:
        log(f"/me cagrisi basarisiz veya izin yok. Env'deki THREADS_USER_ID ({ig_user_id}) direkt kullanilacak.", "WARNING")
        return ig_user_id

    log("Threads User ID hicbir sekilde bulunamadi.", "ERROR")
    return None


def post_text(message: str) -> str | None:
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None

    # 1. Dogru Threads User ID'sini bul
    threads_user_id = _get_threads_user_id(ig_user_id, token)
    if not threads_user_id:
        return None

    container_url = f"{_BASE_URL}/{threads_user_id}/threads"
    container_data = {
        "media_type": "TEXT",
        "text": message,
        "access_token": token,
    }

    log("Threads TEXT container olusturuluyor...")
    container_resp = _post_with_retry(container_url, container_data, "create_text_container")
    container_id = container_resp.get("id")
    if not container_id:
        log("Threads text container olusturulamadi", "ERROR")
        return None

    return _publish_container(threads_user_id, container_id, token, "text")


def post_image(message: str, image_url: str) -> str | None:
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None

    # 1. Dogru Threads User ID'sini bul
    threads_user_id = _get_threads_user_id(ig_user_id, token)
    if not threads_user_id:
        return None

    if not image_url:
        log("post_image: image_url bos, metine dönülmeli", "WARNING")
        return None

    container_url = f"{_BASE_URL}/{threads_user_id}/threads"
    container_data = {
        "media_type": "IMAGE",
        "text": message,
        "image_url": image_url,
        "access_token": token,
    }

    log(f"Threads IMAGE container olusturuluyor (orijinal URL ile)...")
    container_resp = _post_with_retry(container_url, container_data, "create_image_container")
    container_id = container_resp.get("id")
    if not container_id:
        log("Threads image container olusturulamadi, metine fallback.", "ERROR")
        return post_text(message)  # Otomatik metin fallback

    return _publish_container(threads_user_id, container_id, token, "image")


def _publish_container(threads_user_id, container_id, token, media_type):
    publish_url = f"{_BASE_URL}/{threads_user_id}/threads_publish"
    publish_data = {
        "creation_id": container_id,
        "access_token": token,
    }
    log(f"Threads {media_type} publish ediliyor...")
    publish_resp = _post_with_retry(publish_url, publish_data, f"publish_{media_type}")
    post_id = publish_resp.get("id")
    if post_id:
        log(f"Threads {media_type} paylasimi basarili. Post ID: {post_id}")
        return post_id
    log(f"Threads {media_type} publish basarisiz", "ERROR")
    return None


if __name__ == "__main__":
    log("threads.py smoke test (v3.3)")
    uid, tok = _get_credentials()
    if uid and tok:
        log("Threads kimlik bilgileri mevcut.")
    else:
        log("Kimlik bilgileri eksik.", "WARNING")
