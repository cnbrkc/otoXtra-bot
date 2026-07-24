"""
platforms/threads_api.py - Threads API iletişim katmanı (v5.1)
Token, HTTP istekleri ve container oluşturma işlemleri burada.
"""
import os
import time
import requests
from core.logger import log

# ── Threads API ──────────────────────────────────────────────────────────────
_THREADS_API_VERSION = "v1.0"
_BASE_URL = f"https://graph.threads.net/{_THREADS_API_VERSION}"
_REQUEST_TIMEOUT = 60
_RETRY_ATTEMPTS = 3
_RETRY_BASE_WAIT = 2.0

# ── Threads limitleri ────────────────────────────────────────────────────────
_THREADS_MAX_TEXT_LENGTH = 500

# ═══════════════════════════════════════════════════════════════════════════════
# CREDENTIALS & HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_credentials():
    user_id = os.environ.get("THREADS_USER_ID", "").strip()
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")

    token = (
        token.replace('"', "").replace("'", "")
        .replace("\n", "").replace("\r", "")
        .strip()
    )

    if not user_id:
        log("THREADS_USER_ID env bulunamadi", "ERROR")
    else:
        log(f"THREADS_USER_ID okundu: uzunluk={len(user_id)}")

    if not token:
        log("THREADS_ACCESS_TOKEN env bulunamadi", "ERROR")
    else:
        log(f"THREADS_ACCESS_TOKEN okundu: uzunluk={len(token)}, ilk_4={token[:4]}")

    return user_id, token


def _truncate_for_threads(text: str, max_length: int = _THREADS_MAX_TEXT_LENGTH) -> str:
    if not text:
        return ""
    if len(text) <= max_length:
        return text

    truncated = text[: max_length - 3]
    last_space = truncated.rfind(" ")
    if last_space > max_length * 0.6:
        truncated = truncated[:last_space]
    return truncated + "..."


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP RETRY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _post_with_retry(url, data, context="threads", headers=None):
    last_error = ""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            started = time.time()
            resp = requests.post(url, data=data, headers=headers, timeout=_REQUEST_TIMEOUT)
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


def _get_with_retry(url, params, context="threads_get", headers=None):
    last_error = ""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            started = time.time()
            resp = requests.get(url, params=params, headers=headers, timeout=_REQUEST_TIMEOUT)
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


# ═══════════════════════════════════════════════════════════════════════════════
# THREADS USER ID & PUBLISH
# ═══════════════════════════════════════════════════════════════════════════════

def _get_threads_user_id(ig_user_id, token):
    """Threads User ID'sini bulur."""
    url = f"{_BASE_URL}/me"
    params = {"fields": "id,username", "access_token": token}
    headers = {"Authorization": f"Bearer {token}"}

    log("Threads User ID '/me' uzerinden kontrol ediliyor...")
    result = _get_with_retry(url, params, "get_me_profile", headers=headers)

    me_id = result.get("id")
    if me_id:
        log(f"Threads User ID '/me' basarili: {me_id} (username: {result.get('username', 'N/A')})")
        return me_id

    if ig_user_id:
        log(f"/me basarisiz. Env THREADS_USER_ID ({ig_user_id}) kullanilacak.", "WARNING")
        return ig_user_id

    log("Threads User ID hicbir sekilde bulunamadi.", "ERROR")
    return None


def _publish_container(threads_user_id, container_id, token, media_type):
    """Container'i publish eder."""
    publish_url = f"{_BASE_URL}/{threads_user_id}/threads_publish"
    publish_data = {"creation_id": container_id, "access_token": token}
    headers = {"Authorization": f"Bearer {token}"}

    log(f"Threads {media_type} publish ediliyor...")
    publish_resp = _post_with_retry(publish_url, publish_data, f"publish_{media_type}", headers=headers)

    publish_id = publish_resp.get("id")
    if publish_id:
        log(f"Threads {media_type} basariyla publish edildi! ID={publish_id}")
    else:
        log(f"Threads {media_type} publish basarisiz", "ERROR")

    return publish_id


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API - TEMEL FONKSIYONLAR
# ═══════════════════════════════════════════════════════════════════════════════

def post_text(message: str) -> str | None:
    """Threads'a sadece metin paylasimi yapar."""
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None

    threads_user_id = _get_threads_user_id(ig_user_id, token)
    if not threads_user_id:
        return None

    truncated_message = _truncate_for_threads(message)
    if len(truncated_message) < len(message):
        log(f"Threads: Metin {len(message)} -> {len(truncated_message)} karaktere kisildi", "WARNING")

    container_url = f"{_BASE_URL}/{threads_user_id}/threads"
    container_data = {
        "media_type": "TEXT",
        "text": truncated_message,
        "access_token": token,
    }
    headers = {"Authorization": f"Bearer {token}"}

    log("Threads TEXT container olusturuluyor...")
    container_resp = _post_with_retry(container_url, container_data, "create_text_container", headers=headers)
    container_id = container_resp.get("id")
    if not container_id:
        log("Threads text container olusturulamadi", "ERROR")
        return None

    return _publish_container(threads_user_id, container_id, token, "text")


def post_image(message: str, image_url: str, auto_fallback: bool = True) -> str | None:
    """
    Threads'a public URL ile gorsel paylasimi yapar.
    """
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None

    threads_user_id = _get_threads_user_id(ig_user_id, token)
    if not threads_user_id:
        return None

    if not image_url:
        log("post_image: image_url bos", "WARNING")
        if auto_fallback:
            return post_text(message)
        return None

    is_local = (
        image_url.startswith("/")
        or (len(image_url) > 2 and image_url[1] == ":")
    )
    if is_local:
        log(f"post_image: YEREL DOSYA YOLU TESPIT EDILDI! URL={image_url}", "ERROR")
        log("post_image: Yerel dosyalar icin post_with_image() kullanin!", "ERROR")
        if auto_fallback:
            return post_text(message)
        return None

    truncated_message = _truncate_for_threads(message)
    if len(truncated_message) < len(message):
        log(f"Threads: Metin {len(message)} -> {len(truncated_message)} karaktere kisildi", "WARNING")

    container_url = f"{_BASE_URL}/{threads_user_id}/threads"
    container_data = {
        "media_type": "IMAGE",
        "text": truncated_message,
        "image_url": image_url,
        "access_token": token,
    }
    headers = {"Authorization": f"Bearer {token}"}

    log(f"Threads IMAGE container olusturuluyor (public URL ile)...")
    container_resp = _post_with_retry(container_url, container_data, "create_image_container", headers=headers)
    container_id = container_resp.get("id")

    if not container_id:
        log("Threads image container olusturulamadi", "WARNING")
        if auto_fallback:
            log("Threads: Metin fallback yapiliyor...", "WARNING")
            return post_text(message)
        return None

    return _publish_container(threads_user_id, container_id, token, "image")
