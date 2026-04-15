"""
platforms/telegram.py
Basit Telegram mesaj gonderim katmani.
"""

import os
from typing import Optional

import requests

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
