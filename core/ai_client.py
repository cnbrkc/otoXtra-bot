"""
core/ai_client.py
AI cagrilarini tek noktadan yoneten katman.
- Retry/backoff ile gecici hatalara dayaniklilik ekler
- JSON parse icin writer parser + fallback parser kullanir
"""

import json
import re
import time
from typing import Any

from core.logger import log


def _get_retry_config() -> tuple[int, float]:
    """
    Ayarlardan retry degerini alir.
    Ayar yoksa guvenli default kullanir.
    """
    default_attempts = 3
    default_base_wait = 1.5

    try:
        from core.config_loader import load_config

        settings = load_config("settings")
        ai_cfg = settings.get("ai", {}) if isinstance(settings, dict) else {}

        attempts = int(ai_cfg.get("retry_attempts", default_attempts))
        base_wait = float(ai_cfg.get("retry_base_wait_seconds", default_base_wait))

        if attempts < 1:
            attempts = default_attempts
        if base_wait <= 0:
            base_wait = default_base_wait

        return attempts, base_wait
    except Exception as exc:
        log(f"ai_client._get_retry_config fallback: {exc}", "WARNING")
        return default_attempts, default_base_wait


def ask_ai(prompt: str) -> str:
    """
    AI yanitini writer katmani uzerinden alir.
    Gecici hatalarda otomatik retry yapar.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        log("ai_client.ask_ai: bos prompt", "WARNING")
        return ""

    attempts, base_wait = _get_retry_config()
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            from agents.agent_writer import ask_ai as writer_ask_ai

            result = writer_ask_ai(prompt)
            if isinstance(result, str) and result.strip():
                return result

            last_error = "empty_response"
            log(
                f"ai_client.ask_ai bos yanit alindi (deneme {attempt}/{attempts})",
                "WARNING",
            )
        except Exception as exc:
            last_error = str(exc)
            log(
                f"ai_client.ask_ai hata (deneme {attempt}/{attempts}): {exc}",
                "ERROR",
            )

        if attempt < attempts:
            wait_seconds = base_wait * (2 ** (attempt - 1))
            time.sleep(wait_seconds)

    log(f"ai_client.ask_ai tum denemeler bitti. son_hata={last_error}", "ERROR")
    return ""


def parse_ai_json(response: str) -> Any:
    """
    AI yanitini JSON'a cevirir.
    Siralama:
    1) agents.agent_writer.parse_ai_json
    2) Regex ile [] veya {} blok cikarip json.loads
    """
    try:
        from agents.agent_writer import parse_ai_json as writer_parse

        parsed = writer_parse(response)
        if parsed is not None:
            return parsed
    except Exception as exc:
        log(f"ai_client.parse_ai_json writer parser error: {exc}", "WARNING")

    try:
        cleaned = (response or "").strip()
        if not cleaned:
            return None

        array_match = re.search(r"\[[\s\S]*\]", cleaned)
        if array_match:
            return json.loads(array_match.group())

        object_match = re.search(r"\{[\s\S]*\}", cleaned)
        if object_match:
            return json.loads(object_match.group())

        return None
    except Exception as exc:
        log(f"ai_client.parse_ai_json fallback error: {exc}", "WARNING")
        return None
