"""
platforms/threads.py - Threads API katmani (v1.1)

v1.1: Ayrı THREADS_ACCESS_TOKEN desteği eklendi.
      FB_ACCESS_TOKEN'e dokunmaz, sadece Threads için ayrı token kullanır.

Gerekli env:
  - THREADS_USER_ID
  - THREADS_ACCESS_TOKEN  <-- YENİ
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
    user_id = os.environ.get("THREADS_USER_ID", "")
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")  # <-- AYRI TOKEN

    if not user_id:
        log("THREADS_USER_ID env bulunamadi", "ERROR")
    if not token:
        log("THREADS_ACCESS_TOKEN env bulunamadi", "ERROR")
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
                if "temporarily" in msg.lower() or err.get("code") in (4, 17, 32):
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


def post_text(message: str) -> str | None:
    """Threads’e yalnızca metin gönderir. Başarılıysa post ID döner."""
    user_id, token = _get_credentials()
    if not user_id or not token:
        return None

    # 1. Container olustur
    container_url = f"{_BASE_URL}/{user_id}/threads"
    container_data = {
        "media_type": "TEXT",
        "text": message,
        "access_token": token,
    }

    log("Threads container olusturuluyor...")
    container_resp = _post_with_retry(container_url, container_data, "create_container")
    container_id = container_resp.get("id")
    if not container_id:
        log("Threads container olusturulamadi", "ERROR")
        return None

    log(f"Container ID: {container_id}")

    # 2. Yayinla
    publish_url = f"{_BASE_URL}/{user_id}/threads_publish"
    publish_data = {
        "creation_id": container_id,
        "access_token": token,
    }

    log("Threads publish ediliyor...")
    publish_resp = _post_with_retry(publish_url, publish_data, "publish_container")
    post_id = publish_resp.get("id")
    if post_id:
        log(f"Threads paylasimi basarili. Post ID: {post_id}")
        return post_id

    log("Threads publish basarisiz", "ERROR")
    return None


if __name__ == "__main__":
    log("threads.py smoke test (v1.1 - ayrı token)")
    uid, tok = _get_credentials()
    if uid and tok:
        log("Threads kimlik bilgileri mevcut (gercek post atilmadi).")
    else:
        log("Kimlik bilgileri eksik.", "WARNING")
