"""
core/ai_client.py
AI cagrilarini tek noktadan yoneten katman.
"""

import json
import re
from typing import Any

from core.logger import log


def ask_ai(prompt: str) -> str:
    """AI yanitini writer katmani uzerinden alir."""
    try:
        from agents.agent_writer import ask_ai as writer_ask_ai
        return writer_ask_ai(prompt) or ""
    except Exception as exc:
        log(f"ai_client.ask_ai error: {exc}", "ERROR")
        return ""


def parse_ai_json(response: str) -> Any:
    """AI yanitini JSON'a cevirir. Once writer parser, sonra fallback."""
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

        match = re.search(r"\[[\s\S]*\]", cleaned)
        if match:
            return json.loads(match.group())

        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            return json.loads(match.group())

        return None
    except Exception as exc:
        log(f"ai_client.parse_ai_json fallback error: {exc}", "WARNING")
        return None
