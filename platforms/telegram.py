"""
platforms/telegram.py
Basit Telegram mesaj gonderim katmani.
"""

import os
from typing import Optional

import requests

from core.config_loader import get_project_root, load_json, save_json
from core.logger import log


def _get_env(name: str) -> str:
    return (os.environ.get(name, "") or "").strip()


def send_message(text: str) -> bool:
    """
    Telegram'a duz metin mesaj gonderir.
    Hata olursa False doner, exception disari firlatmaz.
    """
    if not text or not text.strip():
        log("telegram.send_message: bos mesaj, atlandi", "WARNING")
        return False

    bot_token = _get_env("TELEGRAM_BOT_TOKEN")
    chat_id = _get_env("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        log("telegram.send_message: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID eksik", "WARNING")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text.strip(),
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code >= 400:
            log(
                f"telegram.send_message HTTP {response.status_code}: {response.text[:300]}",
                "WARNING",
            )
            return False

        data = response.json()
        if not data.get("ok", False):
            log(f"telegram.send_message API error: {str(data)[:300]}", "WARNING")
            return False

        log("telegram.send_message basarili", "INFO")
        return True

    except Exception as exc:
        log(f"telegram.send_message hata: {exc}", "WARNING")
        return False


def _state_file_path() -> str:
    return os.path.join(get_project_root(), "data", "telegram_updates_state.json")


def _load_state() -> dict:
    path = _state_file_path()
    data = load_json(path)
    if not data:
        data = {"last_update_id": 0}
    if not isinstance(data, dict):
        return {"last_update_id": 0}
    last_update_id = data.get("last_update_id", 0)
    try:
        last_update_id = int(last_update_id)
    except Exception:
        last_update_id = 0
    return {"last_update_id": max(0, last_update_id)}


def _save_state(last_update_id: int) -> None:
    path = _state_file_path()
    save_json(path, {"last_update_id": int(last_update_id)})


def _telegram_api_get(method: str, params: Optional[dict] = None, timeout: int = 20) -> Optional[dict]:
    bot_token = _get_env("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return None
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    try:
        response = requests.get(url, params=params or {}, timeout=timeout)
        if response.status_code >= 400:
            log(f"telegram.{method} HTTP {response.status_code}: {response.text[:250]}", "WARNING")
            return None
        data = response.json()
        if not isinstance(data, dict) or not data.get("ok", False):
            log(f"telegram.{method} API error: {str(data)[:300]}", "WARNING")
            return None
        return data
    except Exception as exc:
        log(f"telegram.{method} hata: {exc}", "WARNING")
        return None


def _extract_image_file_id(message: dict) -> str:
    photos = message.get("photo", [])
    if isinstance(photos, list) and photos:
        for photo in reversed(photos):
            if isinstance(photo, dict) and photo.get("file_id"):
                return str(photo.get("file_id"))

    document = message.get("document", {})
    if isinstance(document, dict):
        mime = str(document.get("mime_type", "")).lower()
        if mime.startswith("image/") and document.get("file_id"):
            return str(document.get("file_id"))

    return ""


def _extract_message_candidate(update: dict, expected_chat_id: str) -> Optional[dict]:
    message = update.get("message", {})
    if not isinstance(message, dict):
        return None

    message_chat_id = str(((message.get("chat") or {}).get("id", ""))).strip()
    if message_chat_id != expected_chat_id:
        return None

    file_id = _extract_image_file_id(message)
    if not file_id:
        return None

    return {
        "update_id": int(update.get("update_id", 0) or 0),
        "message_id": int(message.get("message_id", 0) or 0),
        "file_id": file_id,
        "caption": (message.get("caption") or "").strip(),
        "media_group_id": str(message.get("media_group_id", "") or "").strip(),
    }


def _build_grouped_candidates(updates: list, chat_id: str) -> list[dict]:
    groups_by_key: dict[str, dict] = {}
    ordered_keys: list[str] = []

    for update in updates:
        if not isinstance(update, dict):
            continue

        candidate = _extract_message_candidate(update, chat_id)
        if not candidate:
            continue

        media_group_id = candidate.get("media_group_id", "")
        group_key = media_group_id if media_group_id else f"single:{candidate['update_id']}"

        if group_key not in groups_by_key:
            groups_by_key[group_key] = {
                "group_key": group_key,
                "media_group_id": media_group_id,
                "items": [],
                "caption": "",
                "max_update_id": 0,
            }
            ordered_keys.append(group_key)

        group = groups_by_key[group_key]
        group["items"].append(candidate)
        group["max_update_id"] = max(int(group.get("max_update_id", 0)), int(candidate["update_id"]))
        if not group.get("caption") and candidate.get("caption"):
            group["caption"] = candidate["caption"]

    return [groups_by_key[key] for key in ordered_keys]


def _download_telegram_file(file_id: str, update_id: int, message_id: int) -> str:
    bot_token = _get_env("TELEGRAM_BOT_TOKEN")
    if not bot_token or not file_id:
        return ""

    file_result = _telegram_api_get("getFile", {"file_id": file_id})
    if not file_result:
        return ""

    file_path = (((file_result.get("result") or {}).get("file_path")) or "").strip()
    if not file_path:
        return ""

    project_root = get_project_root()
    media_dir = os.path.join(project_root, "data", "telegram_media")
    os.makedirs(media_dir, exist_ok=True)

    _, ext = os.path.splitext(file_path)
    safe_ext = ext if ext.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
    local_path = os.path.join(media_dir, f"tg_{update_id}_{message_id}{safe_ext}")

    file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    try:
        with requests.get(file_url, stream=True, timeout=30) as response:
            if response.status_code >= 400:
                log(f"telegram.file download HTTP {response.status_code}", "WARNING")
                return ""
            with open(local_path, "wb") as out:
                for chunk in response.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        out.write(chunk)
        return local_path
    except Exception as exc:
        log(f"telegram.file download hata: {exc}", "WARNING")
        return ""


def consume_pending_shareable_content() -> Optional[dict]:
    """
    Telegram sohbetinden gelen (gorsel + aciklama) ilk mesaji alir, indirir
    ve bot pipeline'inda haber benzeri islenecek payload dondurur.
    Bulunamazsa None dondurur.
    """
    bot_token = _get_env("TELEGRAM_BOT_TOKEN")
    chat_id = _get_env("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return None

    state = _load_state()
    last_update_id = int(state.get("last_update_id", 0))

    updates_result = _telegram_api_get(
        "getUpdates",
        {
            "offset": last_update_id + 1,
            "limit": 50,
            "allowed_updates": ["message"],
        },
        timeout=25,
    )
    if not updates_result:
        return None

    updates = updates_result.get("result", [])
    if not isinstance(updates, list) or not updates:
        return None

    newest_id = last_update_id
    for update in updates:
        if not isinstance(update, dict):
            continue
        newest_id = max(newest_id, int(update.get("update_id", 0) or 0))

    selected_payload: Optional[dict] = None
    groups = _build_grouped_candidates(updates, chat_id)
    for group in groups:
        caption = (group.get("caption", "") or "").strip()
        if not caption:
            continue

        image_paths: list[str] = []
        seen_file_ids: set[str] = set()

        for item in group.get("items", []):
            file_id = str(item.get("file_id", "") or "").strip()
            if not file_id or file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)

            local_path = _download_telegram_file(
                file_id=file_id,
                update_id=int(item.get("update_id", 0) or 0),
                message_id=int(item.get("message_id", 0) or 0),
            )
            if local_path:
                image_paths.append(local_path)

        if not image_paths:
            continue

        primary_update_id = int(group.get("max_update_id", 0) or 0)
        selected_payload = {
            "article": {
                "title": caption.split("\n")[0][:90] or "Telegram Gonderisi",
                "summary": caption,
                "link": "",
                "source_name": "Telegram",
                "topic_fingerprint": "",
                "manual_priority": True,
            },
            "post_text": caption,
            "image_path": image_paths[0],
            "image_paths": image_paths,
            "image_source": "telegram",
            "image_count": len(image_paths),
            "telegram_update_id": primary_update_id,
        }
        break

    _save_state(newest_id)
    return selected_payload
