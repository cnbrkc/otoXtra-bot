"""
platforms/facebook.py - Facebook Graph API katmani

Sadece Facebook API cagrisi yapar.
Karar vermez, durum tutmaz.
"""

import os
import time
from typing import Optional

import requests

from core.logger import log


_FB_API_VERSION = "v25.0"
_FB_BASE_URL = f"https://graph.facebook.com/{_FB_API_VERSION}"
_REQUEST_TIMEOUT = 60
_RETRY_ATTEMPTS = 3
_RETRY_BASE_WAIT_SECONDS = 2.0


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
            response = requests.post(
                url,
                data=data,
                files=files,
                timeout=_REQUEST_TIMEOUT,
            )
            result = _parse_json_safe(response)

            if response.status_code >= 500:
                last_error = f"http_{response.status_code}"
            elif "error" in result:
                _handle_api_error(result, f"{context} attempt={attempt}")
                if _should_retry_response(result):
                    last_error = "retryable_api_error"
                else:
                    return result
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
            time.sleep(wait_seconds)

    log(f"{context} tum denemeler basarisiz. son_hata={last_error}", "ERROR")
    return {}


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
