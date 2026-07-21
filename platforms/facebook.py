"""
platforms/facebook.py - Facebook Graph API katmani (v4.0 - Refined)

v4.0:
  - Facebook Story publish akisi duzeltildi:
      1) /{page-id}/photos (published=false) -> photo_id
      2) /{page-id}/photo_stories -> publish
  - Hata loglari code/subcode/type/message formatinda iyilestirildi
  - Retry davranisi rafine edildi (429 ve gecici kodlar)
  - Token/Page ID sanitization eklendi
  - Coklu gorsel post akisinda format fallback korundu
  - Geriye donuk alias fonksiyonlar eklendi (post_multi_photo/post_album)

Sadece Facebook API cagrisi yapar.
Karar vermez, durum tutmaz.
"""

import hashlib
import json
import os
import time
from typing import Optional

import requests

from core.logger import log


def _get_fb_api_version() -> str:
    """
    Facebook API versiyonunu config'den okur, yoksa default doner.
    """
    try:
        from core.config_loader import load_config

        settings = load_config("settings")
        facebook = settings.get("facebook", {})
        if isinstance(facebook, dict):
            version = facebook.get("api_version", "")
            if isinstance(version, str) and version.strip():
                return version.strip()
    except Exception:
        pass
    return "v25.0"


_FB_API_VERSION = _get_fb_api_version()
_FB_BASE_URL = f"https://graph.facebook.com/{_FB_API_VERSION}"

_REQUEST_TIMEOUT = 60
_RETRY_ATTEMPTS = 3
_RETRY_BASE_WAIT_SECONDS = 2.0

_MAX_MULTI_PHOTOS = 10
_MAX_PAYLOAD_SIZE_BYTES = 1024 * 1024  # 1MB payload guard
_FB_STORY_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # Meta dokumanina gore story image icin tavsiye/limit


def _sanitize_credential(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return (
        value.replace('"', "")
        .replace("'", "")
        .replace("\n", "")
        .replace("\r", "")
        .strip()
    )


def _safe_body_preview(text: str, limit: int = 400) -> str:
    if not text:
        return ""
    return text.replace("\n", " ").replace("\r", " ").strip()[:limit]


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _mask_id(value: str) -> str:
    if not value:
        return "???"
    parts = value.split("_")
    if len(parts) == 2:
        return f"***_{parts[1]}"
    if len(value) > 8:
        return f"***{value[-6:]}"
    return value


def _get_credentials() -> tuple[str, str]:
    page_id = _sanitize_credential(os.environ.get("FB_PAGE_ID", ""))
    access_token = _sanitize_credential(os.environ.get("FB_ACCESS_TOKEN", ""))

    if not page_id:
        log("FB_PAGE_ID ortam degiskeni bulunamadi", "ERROR")
    if not access_token:
        log("FB_ACCESS_TOKEN ortam degiskeni bulunamadi", "ERROR")

    return page_id, access_token


def _parse_json_safe(response: requests.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_post_id(response: dict) -> str:
    post_id = str(response.get("post_id", "")).strip()
    if post_id:
        return post_id
    return str(response.get("id", "")).strip()


def _handle_api_error(result: dict, context: str) -> None:
    err = result.get("error", {}) if isinstance(result, dict) else {}
    log(
        f"Facebook API hatasi ({context}): "
        f"code={err.get('code', 0)} "
        f"subcode={err.get('error_subcode', 0)} "
        f"type={err.get('type', '')} "
        f"message={err.get('message', '')}",
        "ERROR",
    )


def _should_retry_response(result: dict, status_code: int = 0) -> bool:
    """
    Retry karari:
    - HTTP 429 / 5xx
    - Facebook gecici/rate-limit kodlari
    """
    if status_code == 429 or status_code >= 500:
        return True

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


def _post_with_retry(
    url: str,
    data: dict,
    files: dict | None = None,
    context: str = "post",
) -> dict:
    """
    HTTP/API gecici hatalarinda exponential backoff ile tekrar dener.
    """
    last_error = ""

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            started = time.time()
            response = requests.post(url, data=data, files=files, timeout=_REQUEST_TIMEOUT)
            elapsed_ms = int((time.time() - started) * 1000)

            result = _parse_json_safe(response)
            body_preview = _safe_body_preview(response.text)

            log(
                f"{context} attempt={attempt}/{_RETRY_ATTEMPTS} "
                f"status={response.status_code} elapsed_ms={elapsed_ms}",
                "INFO",
            )

            if "error" in result:
                _handle_api_error(result, f"{context} attempt={attempt}")

                if _should_retry_response(result, response.status_code):
                    last_error = f"api_error_retryable_http_{response.status_code}"
                else:
                    return result

            elif response.status_code >= 400:
                # JSON body'de error yoksa ama HTTP hata ise
                if _should_retry_response({}, response.status_code):
                    last_error = f"http_{response.status_code}"
                    log(
                        f"{context} retryable_http_error status={response.status_code} body={body_preview}",
                        "WARNING",
                    )
                else:
                    log(
                        f"{context} non_retryable_http_error status={response.status_code} body={body_preview}",
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
            log(f"{context} unexpected error (attempt {attempt}/{_TRY_ATTEMPTS}): {exc}", "WARNING")

        if attempt < _RETRY_ATTEMPTS:
            wait_seconds = _RETRY_BASE_WAIT_SECONDS * (2 ** (attempt - 1))
            log(f"{context} retry_wait={wait_seconds:.1f}s", "INFO")
            time.sleep(wait_seconds)

    log(f"{context} tum denemeler basarisiz. son_hata={last_error}", "ERROR")
    return {}


def _upload_unpublished_photo(image_path: str, access_token: str, page_id: str) -> Optional[str]:
    """
    /{page_id}/photos endpoint'ine published=false ile yukleme.
    Donus: photo_id
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
    except Exception as exc:
        log(f"upload_unpublished_photo beklenmeyen hata: {exc}", "ERROR")
        return None

    if not result:
        return None

    if "error" in result:
        _handle_api_error(result, "upload_unpublished_photo final")
        return None

    photo_id = str(result.get("id", "")).strip()
    if photo_id:
        log(f"Unpublished gorsel yuklendi: ID={_mask_id(photo_id)}")
        return photo_id

    log(f"Beklenmeyen upload yaniti: {result}", "WARNING")
    return None


def _estimate_payload_size(payload: dict) -> int:
    try:
        raw = json.dumps(payload)
        return len(raw.encode("utf-8"))
    except Exception:
        return sum(len(str(k)) + len(str(v)) for k, v in payload.items())


def _create_attached_media_payload_v1(media_ids: list[str]) -> dict:
    """
    Format 1:
      attached_media[0] = {"media_fbid":"..."}
      attached_media[1] = {"media_fbid":"..."}
    """
    payload = {}
    for idx, media_id in enumerate(media_ids):
        payload[f"attached_media[{idx}]"] = json.dumps({"media_fbid": media_id})
    return payload


def _create_attached_media_payload_v2(media_ids: list[str]) -> str:
    """
    Format 2:
      attached_media = [{"media_fbid":"..."}, ...]
    """
    return json.dumps([{"media_fbid": mid} for mid in media_ids])


def _post_feed_with_media(
    page_id: str,
    access_token: str,
    message: str,
    media_ids: list[str],
    format_version: int = 1,
) -> dict:
    feed_url = f"{_FB_BASE_URL}/{page_id}/feed"

    payload = {
        "message": message,
        "access_token": access_token,
    }

    if format_version == 1:
        payload.update(_create_attached_media_payload_v1(media_ids))
        log("[FB_API] attached_media format v1 kullaniliyor", "INFO")
    else:
        payload["attached_media"] = _create_attached_media_payload_v2(media_ids)
        log("[FB_API] attached_media format v2 kullaniliyor", "INFO")

    payload_size = _estimate_payload_size(payload)
    if payload_size > _MAX_PAYLOAD_SIZE_BYTES:
        log(
            f"[FB_API] WARNING payload buyuk: {payload_size} > {_MAX_PAYLOAD_SIZE_BYTES}",
            "WARNING",
        )

    log(f"[FB_API] payload_size={payload_size} media_count={len(media_ids)}", "INFO")
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
    payload = {
        "message": message,
        "access_token": access_token,
    }

    log(f"Gorselli post gonderiliyor. metin_uzunlugu={len(message)}")

    try:
        with open(image_path, "rb") as img_file:
            result = _post_with_retry(
                url=url,
                data=payload,
                files={"source": img_file},
                context="post_photo",
            )
    except Exception as exc:
        log(f"post_photo beklenmeyen hata: {exc}", "ERROR")
        return None

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


def post_photos(image_paths: list[str], message: str) -> Optional[str]:
    """
    Facebook'a coklu gorsel + tek aciklama metni ile post atar.

    Akis:
      1) Her gorsel /photos endpoint'ine published=false ile yuklenir
      2) /feed endpoint'ine attached_media ile tek post olusturulur
      3) Format v1 basarisiz olursa format v2 denenir
    """
    page_id, access_token = _get_credentials()
    if not page_id or not access_token:
        return None

    valid_paths = [p for p in image_paths if isinstance(p, str) and p and os.path.exists(p)]
    if not valid_paths:
        log("post_photos: gecerli gorsel yolu yok", "WARNING")
        return None

    # Dedupe by hash
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
        log("post_photos: tek gorsel kaldi, post_photo akisina geciliyor")
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

        started = time.time()
        media_id = _upload_unpublished_photo(path, access_token, page_id)
        elapsed_ms = int((time.time() - started) * 1000)

        if not media_id:
            log(f"post_photos upload_fail idx={idx} elapsed_ms={elapsed_ms}", "WARNING")
            log("Coklu gorsel akisi: upload basarisiz, islem durduruldu", "WARNING")
            return None

        log(f"post_photos upload_ok idx={idx} elapsed_ms={elapsed_ms} media_id={_mask_id(media_id)}")
        media_ids.append(media_id)

    log(f"[FB_API] Tum media yuklendi: {len(media_ids)} adet")
    log("post_photos media_ids=" + ", ".join(_mask_id(m) for m in media_ids))

    log("[FB_API] Feed publish denemesi format v1", "INFO")
    result = _post_feed_with_media(page_id, access_token, message, media_ids, format_version=1)

    if not result or "error" in result:
        if "error" in result:
            error = result.get("error", {})
            error_msg = str(error.get("message", "")).lower()
            error_code = error.get("code", 0)

            log(f"[FB_API] Format v1 fail: code={error_code}, msg={error_msg}", "WARNING")

            if "too much data" in error_msg or "invalid parameter" in error_msg or error_code == 100:
                log("[FB_API] Format v2 ile tekrar deneniyor", "INFO")
                result = _post_feed_with_media(page_id, access_token, message, media_ids, format_version=2)
            else:
                log("[FB_API] Hata format kaynakli gorunmuyor, v2 atlandi", "WARNING")
        else:
            log("[FB_API] Format v1 bos dondu, v2 deneniyor", "INFO")
            result = _post_feed_with_media(page_id, access_token, message, media_ids, format_version=2)

    if not result:
        log("[FB_API] attached_media formatlari basarisiz", "ERROR")
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
    payload = {
        "message": message,
        "access_token": access_token,
    }

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
    Facebook Photo Story paylasimi.

    Dogru Graph API akisi:
      1) /{page_id}/photos -> published=false ile upload (photo_id al)
      2) /{page_id}/photo_stories -> photo_id ile publish et
    """
    page_id, access_token = _get_credentials()
    if not page_id or not access_token:
        return None

    if not image_path or not os.path.exists(image_path):
        log(f"Facebook Story: Gorsel bulunamadi: {image_path}", "ERROR")
        return None

    try:
        file_size = os.path.getsize(image_path)
        if file_size > _FB_STORY_MAX_IMAGE_BYTES:
            log(
                f"Facebook Story: Dosya boyutu buyuk ({file_size} bytes), 10MB alti onerilir",
                "WARNING",
            )
    except Exception:
        pass

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
        _handle_api_error(result, "fb_photo_story_publish final")
        return None

    post_id = str(result.get("post_id", "")).strip()
    if post_id:
        log(f"Facebook Story BASARIYLA yayinlandi! Story Post ID={_mask_id(post_id)}")
        return post_id

    fallback_id = str(result.get("id", "")).strip()
    if fallback_id:
        log(f"Facebook Story yayinlandi (id): {_mask_id(fallback_id)}")
        return fallback_id

    if result.get("success") is True:
        log("Facebook Story: success=true dondu ama post_id/id yok.", "WARNING")
        return "story_success_no_id"

    log(f"Facebook Story: Beklenmeyen yanit: {result}", "WARNING")
    return None


# Backward-compatible aliases (agent_publisher olasi isimleri dener)
def post_multi_photo(image_paths: list[str], message: str) -> Optional[str]:
    return post_photos(image_paths, message)


def post_album(image_paths: list[str], message: str) -> Optional[str]:
    return post_photos(image_paths, message)


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
