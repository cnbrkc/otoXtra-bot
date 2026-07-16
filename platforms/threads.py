"""
platforms/threads.py - Threads API katmani (v2.1)

v2.1:
  - 0x0.st yerine file.io kullanildi.
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
_UPLOAD_URL = "https://file.io"
_UPLOAD_TIMEOUT = 30


def _get_credentials():
    user_id = os.environ.get("THREADS_USER_ID", "")
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
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


def _upload_image_to_public_url(image_path: str) -> str | None:
    """Yerel dosyayı file.io'ya yükler, public URL döner."""
    if not os.path.exists(image_path):
        log(f"file.io upload: Dosya bulunamadi: {image_path}", "ERROR")
        return None

    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                _UPLOAD_URL,
                files={"file": f},
                timeout=_UPLOAD_TIMEOUT,
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                url = data.get("link")
                if url:
                    log(f"file.io upload basarili: {url}")
                    return url
            log(f"file.io upload basarisiz: {resp.text[:200]}", "ERROR")
            return None
        else:
            log(f"file.io upload hatasi: {resp.status_code} {resp.text[:200]}", "ERROR")
            return None
    except Exception as exc:
        log(f"file.io upload exception: {exc}", "ERROR")
        return None


def post_text(message: str) -> str | None:
    user_id, token = _get_credentials()
    if not user_id or not token:
        return None

    container_url = f"{_BASE_URL}/{user_id}/threads"
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

    log(f"Container ID: {container_id}")
    publish_url = f"{_BASE_URL}/{user_id}/threads_publish"
    publish_data = {
        "creation_id": container_id,
        "access_token": token,
    }
    log("Threads text publish ediliyor...")
    publish_resp = _post_with_retry(publish_url, publish_data, "publish_text")
    post_id = publish_resp.get("id")
    if post_id:
        log(f"Threads text paylasimi basarili. Post ID: {post_id}")
        return post_id
    log("Threads text publish basarisiz", "ERROR")
    return None


def post_image(message: str, image_path: str) -> str | None:
    user_id, token = _get_credentials()
    if not user_id or not token:
        return None
    if not image_path:
        log("post_image: image_path bos", "WARNING")
        return None

    image_url = _upload_image_to_public_url(image_path)
    if not image_url:
        log("post_image: upload basarisiz, gorselli paylasim iptal", "ERROR")
        return None

    container_url = f"{_BASE_URL}/{user_id}/threads"
    container_data = {
        "media_type": "IMAGE",
        "text": message,
        "image_url": image_url,
        "access_token": token,
    }

    log("Threads IMAGE container olusturuluyor...")
    container_resp = _post_with_retry(container_url, container_data, "create_image_container")
    container_id = container_resp.get("id")
    if not container_id:
        log("Threads image container olusturulamadi", "ERROR")
        return None

    log(f"Container ID: {container_id}")
    publish_url = f"{_BASE_URL}/{user_id}/threads_publish"
    publish_data = {
        "creation_id": container_id,
        "access_token": token,
    }
    log("Threads image publish ediliyor...")
    publish_resp = _post_with_retry(publish_url, publish_data, "publish_image")
    post_id = publish_resp.get("id")
    if post_id:
        log(f"Threads image paylasimi basarili. Post ID: {post_id}")
        return post_id
    log("Threads image publish basarisiz", "ERROR")
    return None


if __name__ == "__main__":
    log("threads.py smoke test (v2.1)")
    uid, tok = _get_credentials()
    if uid and tok:
        log("Threads kimlik bilgileri mevcut.")
    else:
        log("Kimlik bilgileri eksik.", "WARNING")
