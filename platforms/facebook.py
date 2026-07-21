"""
platforms/facebook.py - Facebook Graph API katmani (v3.6 - FB Story Endpoint Fix)

v3.6:
  - FB Story paylasimi /{page-id}/stories yerine dogru akisla guncellendi:
    1) /{page-id}/photos (published=false) ile photo_id al
    2) /{page-id}/photo_stories ile publish et
  - Story hata loglari code/subcode/type/message seklinde detaylandirildi

v3.5:
  - core/image_uploader modulu entegre edildi (DRY prensibi)
  - Tekrarlanan upload fonksiyonlari kaldirildi
  
v3.4:
  - post_story(image_path) eklendi (Facebook Story/Hikaye paylaşımı)
  - Instagram'dan farklı olarak Facebook direkt file upload kabul eder
  - /{page-id}/stories endpoint'i kullanılır

v3.3 ULTRA FIXED:
  - CRITICAL FIX: Correct attached_media format for Graph API v25.0
  - CRITICAL FIX: Payload size validation (prevent "too much data" error)
  - CRITICAL FIX: Alternative format fallback (array vs key-value)
  - Enhanced error handling for 500/400 responses

v3.2:
  - post_photos(image_paths, message) eklendi (coklu gorsel)
  - Coklu gorsel icin unpublished upload + attached_media akisi eklendi
  - Upload oncesi dosya hash ile son dedupe guvenlik kati eklendi
  - attached_media alanlari loglanir
  - HTTP cevaplari icin detayli retry / status / body preview loglari eklendi

Sadece Facebook API cagrisi yapar.
Karar vermez, durum tutmaz.
"""

import json
import os
import time
import hashlib
from typing import Optional

import requests

from core.logger import log


def _get_fb_api_version() -> str:
    """
    Facebook API versiyonunu config'den okur, yoksa default döner.
    Bu sayede Meta API versiyon değiştiğinde kod değişikliği gerekmez.
    """
    try:
        from core.config_loader import load_config
        settings = load_config("settings")
        facebook = settings.get("facebook", {})
        if isinstance(facebook, dict):
            version = facebook.get("api_version", "")
            if version and isinstance(version, str):
                return version.strip()
    except Exception:
        pass
    # Fallback default
    return "v25.0"


_FB_API_VERSION = _get_fb_api_version()
_FB_BASE_URL = f"https://graph.facebook.com/{_FB_API_VERSION}"
_REQUEST_TIMEOUT = 60
_RETRY_ATTEMPTS = 3
_RETRY_BASE_WAIT_SECONDS = 2.0
_MAX_MULTI_PHOTOS = 10
_MAX_PAYLOAD_SIZE_BYTES = 1024 * 1024  # CRITICAL FIX: 1MB payload limit


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
        f"[{err.get('code', 0)}|{err.get('error_subcode', 0)}] "
        f"{err.get('type', '')} {err.get('message', '')}",
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


def _estimate_payload_size(payload: dict) -> int:
    """
    CRITICAL FIX: Estimate payload size in bytes to prevent "too much data" error
    """
    try:
        # Serialize to JSON and get byte size
        json_str = json.dumps(payload)
        return len(json_str.encode('utf-8'))
    except Exception:
        # Fallback: rough estimate
        return sum(len(str(k)) + len(str(v)) for k, v in payload.items())


def _create_attached_media_payload_v1(media_ids: list[str]) -> dict:
    """
    CRITICAL FIX: Format 1 - Individual key-value pairs (Facebook Graph API standard)
    
    Example:
    {
        "attached_media[0]": '{"media_fbid": "123"}',
        "attached_media[1]": '{"media_fbid": "456"}',
    }
    """
    payload = {}
    for idx, media_id in enumerate(media_ids):
        key = f"attached_media[{idx}]"
        payload[key] = json.dumps({"media_fbid": media_id})
    return payload


def _create_attached_media_payload_v2(media_ids: list[str]) -> str:
    """
    CRITICAL FIX: Format 2 - JSON array string (alternative format)
    
    Example:
    '[{"media_fbid": "123"}, {"media_fbid": "456"}]'
    """
    media_objects = [{"media_fbid": mid} for mid in media_ids]
    return json.dumps(media_objects)


def _post_feed_with_media(
    page_id: str,
    access_token: str,
    message: str,
    media_ids: list[str],
    format_version: int = 1
) -> dict:
    """
    CRITICAL FIX: Post to feed with attached_media using specified format
    
    Args:
        page_id: Facebook Page ID
        access_token: Access token
        message: Post message
        media_ids: List of media_fbid values
        format_version: 1 = key-value pairs, 2 = JSON array
    
    Returns:
        API response dict
    """
    feed_url = f"{_FB_BASE_URL}/{page_id}/feed"
    
    payload = {
        "message": message,
        "access_token": access_token,
    }
    
    if format_version == 1:
        # Format 1: Individual key-value pairs
        media_payload = _create_attached_media_payload_v1(media_ids)
        payload.update(media_payload)
        log(f"[FB_API] Using attached_media format v1 (key-value pairs)", "INFO")
    else:
        # Format 2: JSON array
        payload["attached_media"] = _create_attached_media_payload_v2(media_ids)
        log(f"[FB_API] Using attached_media format v2 (JSON array)", "INFO")
    
    # CRITICAL FIX: Check payload size
    payload_size = _estimate_payload_size(payload)
    if payload_size > _MAX_PAYLOAD_SIZE_BYTES:
        log(
            f"[FB_API] WARNING: Payload size ({payload_size} bytes) exceeds limit "
            f"({_MAX_PAYLOAD_SIZE_BYTES} bytes)",
            "WARNING"
        )
    
    log(f"[FB_API] Payload size: {payload_size} bytes, media_count: {len(media_ids)}", "INFO")
    
    return _post_with_retry(
        url=feed_url,
        data=payload,
        files=None,
        context=f"post_feed_with_media_v{format_version}",
    )


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
    CRITICAL FIX v3.3: Facebook'a coklu gorsel + tek aciklama metni ile post atar.
    
    Improvements:
    - Correct attached_media format for Graph API v25.0
    - Payload size validation
    - Alternative format fallback
    - Better error handling

    Akis:
      1) Her gorsel /{page_id}/photos endpoint'ine published=false ile yuklenir
      2) /{page_id}/feed endpoint'ine attached_media ile tek post olusturulur
      3) Format 1 basarisiz olursa Format 2 denenir
    """
    page_id, access_token = _get_credentials()
    if not page_id or not access_token:
        return None

    valid_paths = [p for p in image_paths if isinstance(p, str) and p and os.path.exists(p)]
    if not valid_paths:
        log("post_photos: gecerli gorsel yolu yok", "WARNING")
        return None

    # Dedupe by file hash
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

    # Upload all images as unpublished
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

    log(f"[FB_API] All media uploaded successfully: {len(media_ids)} items")
    log("post_photos media_ids=" + ", ".join(_mask_id(m) for m in media_ids))

    # CRITICAL FIX: Try format 1 first (key-value pairs)
    log("[FB_API] Attempting feed post with attached_media format v1", "INFO")
    result = _post_feed_with_media(page_id, access_token, message, media_ids, format_version=1)

    # If format 1 failed, try format 2 (JSON array)
    if not result or "error" in result:
        if "error" in result:
            error = result.get("error", {})
            error_msg = error.get("message", "").lower()
            error_code = error.get("code", 0)
            
            log(
                f"[FB_API] Format v1 failed: code={error_code}, msg={error_msg}",
                "WARNING"
            )
            
            # Check for specific errors that might be resolved by format change
            if "too much data" in error_msg or "invalid parameter" in error_msg or error_code == 100:
                log("[FB_API] Retrying with attached_media format v2", "INFO")
                result = _post_feed_with_media(page_id, access_token, message, media_ids, format_version=2)
            else:
                log("[FB_API] Error not related to format, skipping v2 attempt", "WARNING")
        else:
            log("[FB_API] Format v1 returned empty, trying format v2", "INFO")
            result = _post_feed_with_media(page_id, access_token, message, media_ids, format_version=2)

    if not result:
        log("[FB_API] Both attached_media formats failed", "ERROR")
        return None

    if "error" in result:
        _handle_api_error(result, "post_photos final (all formats failed)")
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


def post_story(image_path: str) -> Optional[str]:
    """
    Facebook Story (Photo Story) paylasimi yapar.

    Dogru Graph API akisi:
      1) /{page_id}/photos -> published=false ile upload (photo_id al)
      2) /{page_id}/photo_stories -> photo_id ile publish et

    Args:
        image_path: Yerel story gorseli

    Returns:
        Story post id (varsa) veya None
    """
    page_id, access_token = _get_credentials()
    if not page_id or not access_token:
        return None

    if not image_path or not os.path.exists(image_path):
        log(f"Facebook Story: Gorsel bulunamadi: {image_path}", "ERROR")
        return None

    log("Facebook Story: Adim 1/2 - unpublished photo upload basliyor...")
    photo_id = _upload_unpublished_photo(image_path, access_token, page_id)
    if not photo_id:
        log("Facebook Story: Adim 1 basarisiz, publish durduruldu.", "ERROR")
        return None

    log(f"Facebook Story: Adim 1 tamamlandi. photo_id={_mask_id(photo_id)}")

    story_url = f"{_FB_BASE_URL}/{page_id}/photo_stories"
    payload = {
        "photo_id": photo_id,
        "access_token": access_token,
    }

    log("Facebook Story: Adim 2/2 - /photo_stories publish basliyor...")
    result = _post_with_retry(
        url=story_url,
        data=payload,
        files=None,
        context="fb_photo_story_publish",
    )

    if not result:
        log("Facebook Story: Publish bos yanit dondu.", "ERROR")
        return None

    if "error" in result:
        err = result.get("error", {}) if isinstance(result, dict) else {}
        log(
            "Facebook Story publish hatasi: "
            f"code={err.get('code')} "
            f"subcode={err.get('error_subcode')} "
            f"type={err.get('type')} "
            f"message={err.get('message')}",
            "ERROR",
        )
        return None

    post_id = result.get("post_id", "") or result.get("id", "")
    if post_id:
        log(f"Facebook Story BASARIYLA yayinlandi! Story Post ID={_mask_id(post_id)}")
        return str(post_id)

    if result.get("success") is True:
        log("Facebook Story: success=true dondu ama post_id yok.", "WARNING")
        return "story_success_no_id"

    log(f"Facebook Story: Beklenmeyen yanit: {result}", "WARNING")
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
