"""
core/ai_client.py
AI cagri katmani.
- Provider secimi burada toplanir (Gemini, Groq, OpenRouter, HuggingFace)
- Retry/backoff uygulanir
- JSON parse fallback'i saglanir
"""

import json
import os
import re
import time
from typing import Any, Dict, Optional

import requests

from core.logger import log


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load_ai_config() -> Dict[str, Any]:
    try:
        from core.config_loader import load_config

        settings = load_config("settings")
        if isinstance(settings, dict):
            ai_cfg = settings.get("ai", {})
            if isinstance(ai_cfg, dict):
                return ai_cfg
    except Exception as exc:
        log(f"ai_client._load_ai_config fallback: {exc}", "WARNING")
    return {}


def _get_retry_config() -> tuple[int, float]:
    cfg = _load_ai_config()
    attempts = _safe_int(cfg.get("retry_attempts", 3), 3)
    base_wait = _safe_float(cfg.get("retry_base_wait_seconds", 1.5), 1.5)

    if attempts < 1:
        attempts = 3
    if base_wait <= 0:
        base_wait = 1.5

    return attempts, base_wait


def _provider_order() -> list[str]:
    cfg = _load_ai_config()
    order = cfg.get("provider_order")
    if isinstance(order, list):
        cleaned = [str(x).strip().lower() for x in order if str(x).strip()]
        if cleaned:
            return cleaned
    return ["gemini", "groq", "openrouter", "huggingface"]


def _is_enabled(cfg: Dict[str, Any], key: str, default: bool = True) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _try_gemini(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    if not _is_enabled(cfg, "enable_gemini", True):
        return None

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    model_name = str(cfg.get("gemini_model", "gemini-2.5-flash-lite")).strip()
    temperature = _safe_float(cfg.get("temperature", 0.65), 0.65)
    max_tokens = _safe_int(cfg.get("max_output_tokens", 1400), 1400)

    temperature = max(0.0, min(2.0, temperature))
    if max_tokens < 1:
        max_tokens = 1400

    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        log(f"Gemini import hatasi: {exc}", "WARNING")
        return None

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        text = (getattr(response, "text", "") or "").strip()
        return text or None
    except Exception as exc:
        log(f"Gemini hatasi: {exc}", "WARNING")
        return None


def _try_groq(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    if not _is_enabled(cfg, "enable_groq", True):
        return None

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None

    model_name = str(cfg.get("groq_model", "llama-3.1-8b-instant")).strip()
    temperature = _safe_float(cfg.get("temperature", 0.65), 0.65)
    max_tokens = _safe_int(cfg.get("max_output_tokens", 1400), 1400)

    try:
        from groq import Groq
    except Exception as exc:
        log(f"Groq import hatasi: {exc}", "WARNING")
        return None

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = ""
        if response and response.choices:
            text = (response.choices[0].message.content or "").strip()
        return text or None
    except Exception as exc:
        log(f"Groq hatasi: {exc}", "WARNING")
        return None


def _try_openrouter(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    if not _is_enabled(cfg, "enable_openrouter", True):
        return None

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None

    model_name = str(cfg.get("openrouter_model", "openai/gpt-4o-mini")).strip()
    temperature = _safe_float(cfg.get("temperature", 0.65), 0.65)
    max_tokens = _safe_int(cfg.get("max_output_tokens", 1400), 1400)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=45,
        )
        if response.status_code >= 400:
            log(f"OpenRouter HTTP {response.status_code}: {response.text[:300]}", "WARNING")
            return None

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return None
        message = choices[0].get("message", {})
        text = (message.get("content", "") or "").strip()
        return text or None
    except Exception as exc:
        log(f"OpenRouter hatasi: {exc}", "WARNING")
        return None


def _try_huggingface(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    if not _is_enabled(cfg, "enable_huggingface", True):
        return None

    api_key = os.getenv("HF_API_KEY", "").strip()
    if not api_key:
        return None

    model_name = str(cfg.get("hf_model", "mistralai/Mistral-7B-Instruct-v0.2")).strip()
    max_tokens = _safe_int(cfg.get("max_output_tokens", 600), 600)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "max_new_tokens": max_tokens,
            "return_full_text": False,
        },
    }

    try:
        response = requests.post(
            f"https://api-inference.huggingface.co/models/{model_name}",
            headers=headers,
            json=payload,
            timeout=60,
        )
        if response.status_code >= 400:
            log(f"HuggingFace HTTP {response.status_code}: {response.text[:300]}", "WARNING")
            return None

        data = response.json()
        if isinstance(data, list) and data:
            text = (data[0].get("generated_text", "") or "").strip()
            return text or None
        return None
    except Exception as exc:
        log(f"HuggingFace hatasi: {exc}", "WARNING")
        return None


def ask_ai(prompt: str) -> str:
    """
    Prompt alir, provider sirasiyla dener, retry/backoff uygular.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        log("ai_client.ask_ai: bos prompt", "WARNING")
        return ""

    attempts, base_wait = _get_retry_config()
    cfg = _load_ai_config()
    providers = _provider_order()

    provider_map = {
        "gemini": _try_gemini,
        "groq": _try_groq,
        "openrouter": _try_openrouter,
        "huggingface": _try_huggingface,
    }

    last_error = None

    for attempt in range(1, attempts + 1):
        for provider in providers:
            fn = provider_map.get(provider)
            if fn is None:
                continue

            try:
                result = fn(prompt, cfg)
                if isinstance(result, str) and result.strip():
                    log(f"ai_client.ask_ai basarili provider={provider}", "INFO")
                    return result.strip()
            except Exception as exc:
                last_error = f"{provider}: {exc}"
                log(f"ai_client.ask_ai provider hata {provider}: {exc}", "WARNING")

        if attempt < attempts:
            wait_seconds = base_wait * (2 ** (attempt - 1))
            time.sleep(wait_seconds)

    log(f"ai_client.ask_ai tum denemeler bitti. son_hata={last_error}", "ERROR")
    return ""


def parse_ai_json(response: str) -> Any:
    """
    AI yanitini JSON'a cevirir.
    1) Direkt json.loads
    2) Regex ile [] veya {} blok ayiklayip json.loads
    """
    cleaned = (response or "").strip()
    if not cleaned:
        return None

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    try:
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
