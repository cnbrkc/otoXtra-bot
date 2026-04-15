"""
platforms/facebook.py - Facebook Graph API katmani

Sadece Facebook API cagrisi yapar.
Karar vermez, durum tutmaz.

v3.2:
  - post_photos(image_paths, message) eklendi (coklu gorsel)
  - Coklu gorsel icin unpublished upload + attached_media akisi eklendi
  - Upload oncesi dosya hash ile son dedupe guvenlik kati eklendi
  - attached_media alanlari loglanir
  - HTTP cevaplari icin detayli retry / status / body preview loglari eklendi
"""

import json
import os
import time
import hashlib
from typing import Optional

import requests

from core.logger import log


_FB_API_VERSION = "v25.0"
_FB_BASE_URL = f"https://graph.facebook.com/{_FB_API_VERSION}"
_REQUEST_TIMEOUT = 60
_RETRY_ATTEMPTS = 3
_RETRY_BASE_WAIT_SECONDS = 2.0
_MAX_MULTI_PHOTOS = 10


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_body_preview(text: str, limit: int = 400) -> str:
    if not text:
        return ""
    value = text.replace("\n", " ").replace("\r", " ").strip()
    return value[:limit]


def _get_credentials() -> tuple[str, str]:
    page_id = os.environ.get("FB_PAGE_ID", "")
    access_token = os.environ.get("FB_ACCESS_TOKEN", "")

    if not page_id:
        log("FB_PAGE_ID ortam degiskeni bulunamadi", "ERROR")
    if not access_token:
        log("FB_ACCESS_TOKEN ortam degiskeni bulunamadi", "ERROR")

    return page_id, access_token


def _extract_post_id(response: dict) -> str:
    post_id = response.get("post_id", "")
    if post_id:
        return post_id
    return response.get("id", "")


def _mask_id(post_id: str) -> str:
    if not post_id:
        return "???"
    parts = post_id.split("_")
    if len(parts) == 2:
        return f"***_{parts[1]}"
    return post_id


def _handle_api_error(result: dict, context: str) -> None:
    err = result.get("error", {}) if isinstance(result, dict) else {}
    log(
        f"Facebook API hatasi ({context}): "
        f"[{err.get('code', 0)}] {err.get('type', '')} {err.get('message', '')}",
        "ERROR",
    )


def _should_retry_response(result: dict) -> bool:
    """
    Facebook'tan gelen hata koduna gore tekrar deneme karari.
    4/17/32 gibi gecici throttling veya 5xx benzeri durumlarda retry.
    """
    try:
        err = result.get("error", {})
        code = int(err.get("code", 0))
        msg = str(err.get("message", "")).lower()

        if code in (1, 2, 4, 17, 32, 613):
            return True
        if "temporar" in msg or "try again" in msg or "rate limit" in msg:
            return True
        return False
    except Exception:
        return False


def _parse_json_safe(response: requests.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _post_with_retry(
    url: str,
    data: dict,
    files: dict | None = None,
    context: str = "post",
) -> dict:
    """
    HTTP ve gecici API hatalarinda exponential backoff ile tekrar dener.
    Basariliysa API yanitini dict doner, olmazsa bos dict.
    """
    last_error: str = ""

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            started = time.time()
            response = requests.post(
                url,
                data=data,
                files=files,
                timeout=_REQUEST_TIMEOUT,
            )
            elapsed_ms = int((time.time() - started) * 1000)

            result = _parse_json_safe(response)
            body_preview = _safe_body_preview(response.text)

            log(
                f"{context} attempt={attempt}/{_RETRY_ATTEMPTS} "
                f"status={response.status_code} elapsed_ms={elapsed_ms}",
                "INFO",
            )

            if response.status_code >= 500:
                last_error = f"http_{response.status_code}"
                log(
                    f"{context} server_error status={response.status_code} body={body_preview}",
                    "WARNING",
                )

            elif "error" in result:
                _handle_api_error(result, f"{context} attempt={attempt}")
                if _should_retry_response(result):
                    last_error = "retryable_api_error"
                else:
                    return result

            elif response.status_code >= 400:
                last_error = f"http_{response.status_code}"
                log(
                    f"{context} client_error_non_json status={response.status_code} body={body_preview}",
                    "WARNING",
                )
                return {}

            else:
                return result

        except requests.exceptions.Timeout:
            last_error = "timeout"
            log(f"{context} timeout (attempt {attempt}/{_RETRY_ATTEMPTS})", "WARNING")
        except requests.exceptions.ConnectionError as exc:
            last_error = f"connection_error: {exc}"
            log(f"{context} connection error (attempt {attempt}/{_RETRY_ATTEMPTS}): {exc}", "WARNING")
        except requests.exceptions.RequestException as exc:
            last_error = f"request_exception: {exc}"
            log(f"{context} request error (attempt {attempt}/{_RETRY_ATTEMPTS}): {exc}", "WARNING")
        except Exception as exc:
            last_error = f"unexpected_error: {exc}"
            log(f"{context} unexpected error (attempt {attempt}/{_RETRY_ATTEMPTS}): {exc}", "WARNING")

        if attempt < _RETRY_ATTEMPTS:
            wait_seconds = _RETRY_BASE_WAIT_SECONDS * (2 ** (attempt - 1))
            log(f"{context} retry_wait={wait_seconds:.1f}s", "INFO")
            time.sleep(wait_seconds)

    log(f"{context} tum denemeler basarisiz. son_hata={last_error}", "ERROR")
    return {}


def _upload_unpublished_photo(image_path: str, access_token: str, page_id: str) -> Optional[str]:
    """
    Coklu gorsel akisi icin gorseli published=false olarak yukler.
    Donus: media_fbid olarak kullanilacak photo id.
    """
    if not image_path or not os.path.exists(image_path):
        log(f"Gorsel dosyasi bulunamadi (upload): {image_path}", "ERROR")
        return None

    url = f"{_FB_BASE_URL}/{page_id}/photos"
    payload = {
        "published": "false",
        "access_token": access_token,
    }

    try:
        with open(image_path, "rb") as img_file:
            result = _post_with_retry(
                url=url,
                data=payload,
                files={"source": img_file},
                context="upload_unpublished_photo",
            )

        if not result:
            return None

        if "error" in result:
            _handle_api_error(result, "upload_unpublished_photo final")
            return None

        media_id = result.get("id", "")
        if media_id:
            log(f"Unpublished gorsel yuklendi: ID={_mask_id(media_id)}")
            return media_id

        log(f"Beklenmeyen upload yaniti: {result}", "WARNING")
        return None

    except Exception as exc:
        log(f"upload_unpublished_photo beklenmeyen hata: {exc}", "ERROR")
        return None


def post_photo(image_path: str, message: str) -> Optional[str]:
    page_id, access_token = _get_credentials()
    if not page_id or not access_token:
        return None

    if not image_path or not os.path.exists(image_path):
        log(f"Gorsel dosyasi bulunamadi: {image_path}", "ERROR")
        return None

    url = f"{_FB_BASE_URL}/{page_id}/photos"
    payload = {"message": message, "access_token": access_token}

    log(f"Gorselli post gonderiliyor. metin_uzunlugu={len(message)}")

    try:
        with open(image_path, "rb") as img_file:
            result = _post_with_retry(
                url=url,
                data=payload,
                files={"source": img_file},
                context="post_photo",
            )

        if not result:
            return None

        if "error" in result:
            _handle_api_error(result, "post_photo final")
            return None

        post_id = _extract_post_id(result)
        if post_id:
            log(f"Gorselli post basarili: ID={_mask_id(post_id)}")
            return post_id

        log(f"Beklenmeyen post_photo yaniti: {result}", "WARNING")
        return None

    except Exception as exc:
        log(f"post_photo beklenmeyen hata: {exc}", "ERROR")
        return None


def post_photos(image_paths: list[str], message: str) -> Optional[str]:
    """
    Facebook'a coklu gorsel + tek aciklama metni ile post atar.

    Akis:
      1) Her gorsel /{page_id}/photos endpoint'ine published=false ile yuklenir
      2) /{page_id}/feed endpoint'ine attached_media[] ile tek post olusturulur
    """
    page_id, access_token = _get_credentials()
    if not page_id or not access_token:
        return None

    valid_paths = [p for p in image_paths if isinstance(p, str) and p and os.path.exists(p)]
    if not valid_paths:
        log("post_photos: gecerli gorsel yolu yok", "WARNING")
        return None

    deduped_paths: list[str] = []
    seen_hashes: set[str] = set()
    for path in valid_paths:
        try:
            file_hash = _file_sha256(path)
        except Exception as exc:
            log(f"post_photos: hash alinamadi, dosya atlandi ({path}): {exc}", "WARNING")
            continue
        if file_hash in seen_hashes:
            log(f"post_photos: duplicate dosya elendi ({path})")
            continue
        seen_hashes.add(file_hash)
        deduped_paths.append(path)

    valid_paths = deduped_paths
    if not valid_paths:
        log("post_photos: dedupe sonrasi gecerli gorsel kalmadi", "WARNING")
        return None

    if len(valid_paths) == 1:
        log("post_photos: dedupe sonrasi tek gorsel kaldi, post_photo akisina geciliyor")
        return post_photo(valid_paths[0], message)

    if len(valid_paths) > _MAX_MULTI_PHOTOS:
        log(f"post_photos: gorsel sayisi {_MAX_MULTI_PHOTOS} ile sinirlandi")
        valid_paths = valid_paths[:_MAX_MULTI_PHOTOS]

    log(f"Coklu gorsel post basliyor. adet={len(valid_paths)}, metin_uzunlugu={len(message)}")

    media_ids: list[str] = []
    for idx, path in enumerate(valid_paths, start=1):
        try:
            size_kb = os.path.getsize(path) // 1024
        except Exception:
            size_kb = -1

        short_hash = "unknown"
        try:
            short_hash = _file_sha256(path)[:12]
        except Exception:
            pass

        log(
            f"post_photos upload_start idx={idx}/{len(valid_paths)} "
            f"size_kb={size_kb} sha12={short_hash} path={path}"
        )

        up_started = time.time()
        media_id = _upload_unpublished_photo(path, access_token, page_id)
        up_elapsed_ms = int((time.time() - up_started) * 1000)

        if not media_id:
            log(
                f"post_photos upload_fail idx={idx} elapsed_ms={up_elapsed_ms}",
                "WARNING",
            )
            log("Coklu gorsel akisi: upload basarisiz, islem durduruldu", "WARNING")
            return None

        log(
            f"post_photos upload_ok idx={idx} elapsed_ms={up_elapsed_ms} media_id={_mask_id(media_id)}"
        )
        media_ids.append(media_id)

    log("post_photos media_ids=" + ", ".join(_mask_id(m) for m in media_ids))

    feed_url = f"{_FB_BASE_URL}/{page_id}/feed"
    payload: dict[str, str] = {
        "message": message,
        "access_token": access_token,
    }

    attached_keys: list[str] = []
    for idx, media_id in enumerate(media_ids):
        key = f"attached_media[{idx}]"
        payload[key] = json.dumps({"media_fbid": media_id})
        attached_keys.append(key)

    log(f"post_photos: attached_media keys -> {', '.join(attached_keys)}")

    result = _post_with_retry(
        url=feed_url,
        data=payload,
        files=None,
        context="post_photos_feed_create",
    )

    if not result:
        return None

    if "error" in result:
        _handle_api_error(result, "post_photos final")
        return None

    post_id = _extract_post_id(result)
    if post_id:
        log(f"Coklu gorsel post basarili: ID={_mask_id(post_id)}")
        return post_id

    log(f"Beklenmeyen post_photos yaniti: {result}", "WARNING")
    return None


def post_text(message: str) -> Optional[str]:
    page_id, access_token = _get_credentials()
    if not page_id or not access_token:
        return None

    url = f"{_FB_BASE_URL}/{page_id}/feed"
    payload = {"message": message, "access_token": access_token}

    log(f"Metin post gonderiliyor. metin_uzunlugu={len(message)}")

    result = _post_with_retry(
        url=url,
        data=payload,
        files=None,
        context="post_text",
    )

    if not result:
        return None

    if "error" in result:
        _handle_api_error(result, "post_text final")
        return None

    post_id = _extract_post_id(result)
    if post_id:
        log(f"Metin post basarili: ID={_mask_id(post_id)}")
        return post_id

    log(f"Beklenmeyen post_text yaniti: {result}", "WARNING")
    return None


if __name__ == "__main__":
    log("platforms/facebook.py smoke test basladi")
    page_id, access_token = _get_credentials()

    if page_id and access_token:
        log(f"FB_PAGE_ID mevcut: ***{page_id[-4:]}")
        log(f"FB_ACCESS_TOKEN mevcut: ***{access_token[-6:]}")
        log("Kimlik bilgileri mevcut (gercek post atilmadi).")
    else:
        log("Kimlik bilgileri eksik. Secrets kontrol et.", "WARNING")

    log("platforms/facebook.py smoke test bitti")
