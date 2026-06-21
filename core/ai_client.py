"""
core/ai_client.py - Ultra Multi-Provider AI Stack (v5.3 FIXED)

v5.3 FIXED:
  - CRITICAL FIX: Gemini model listesi gercek/gecerli model adlariyla guncellendi.
    gemini-3.5-flash gibi var olmayan modeller kaldirildi.
  - CRITICAL FIX: parse_ai_json() - Gemini thinking/reasoning metninin ardindan
    gelen JSON blogu artik doğru sekilde ayiklaniyor.
    (Ornek: "...hesaplama metni...\n[{\"sira\":1,...}]")
  - Gemini thinking modu (gemini-2.5-*) icin JSON output config eklendi.

v5.2 FINAL:
  - 503 UNAVAILABLE -> skip (no retry) FIX
  - 500 INTERNAL_ERROR -> skip (no retry) FIX

v5.1 FINAL:
  - GEMINI MODEL FIX
  - API v1beta yerine v1 API kullaniliyor

v5.0:
  - GEMINI STACK: 5 model cascade
  - GROQ STACK: 3 model cascade
  - EMERGENCY STACK: OpenRouter + HuggingFace
  - ERROR CLASSIFICATION: timeout, rate_limit, quota, token_limit
  - SMART RETRY: timeout=1x retry, quota=skip
  - EXPONENTIAL BACKOFF: 3-10s
"""

import json
import os
import random
import re
import time
from typing import Any, Dict, Optional

import requests

from core.logger import log


# ========== GEMINI STACK (v5.3: GERCEK MODEL ADLARI) ==========
# Kaynak: https://ai.google.dev/gemini-api/docs/models
GEMINI_MODELS = [
    "gemini-2.5-flash",        # En guncel Flash - thinking destekli
    "gemini-2.5-flash-lite",   # Hafif/hizli Flash
    "gemini-2.0-flash",        # Stabil Flash
    "gemini-2.0-flash-lite",   # Stabil hafif Flash
    "gemini-1.5-flash",        # Fallback - cok stabil
]

# ========== GROQ STACK ==========
GROQ_MODELS = [
    "llama-3.3-70b-versatile",   # Groq primary (en guclu)
    "llama-3.1-70b-versatile",   # Groq fallback 1
    "llama-3.1-8b-instant",      # Groq fallback 2 (hizli)
]

_BOOL_TRUE_VALUES = ("1", "true", "yes", "on")


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


def _get_env_str(name: str) -> str:
    return (os.getenv(name, "") or "").strip()


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


def _get_retry_config() -> tuple[int, float, float]:
    """Returns: (max_attempts, base_wait, max_wait)"""
    cfg = _load_ai_config()
    attempts = _safe_int(cfg.get("retry_attempts", 2), 2)
    base_wait = _safe_float(cfg.get("retry_base_wait_seconds", 3.0), 3.0)
    max_wait = _safe_float(cfg.get("retry_max_wait_seconds", 10.0), 10.0)

    if attempts < 1:
        attempts = 2
    if base_wait <= 0:
        base_wait = 3.0
    if max_wait < base_wait:
        max_wait = base_wait

    return attempts, base_wait, max_wait


def _is_enabled(cfg: Dict[str, Any], key: str, default: bool = True) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _BOOL_TRUE_VALUES
    return bool(value)


def _classify_error(error_text: str) -> str:
    lower = (error_text or "").lower()

    if "timeout" in lower or "timed out" in lower:
        return "timeout"
    if "rate limit" in lower or "429" in lower:
        return "rate_limit"
    if "quota" in lower or "exceeded" in lower:
        return "quota_exceeded"
    if "token" in lower and ("limit" in lower or "too long" in lower):
        return "token_limit"
    if "404" in lower or "not found" in lower or "not_found" in lower:
        return "not_found"
    if "503" in lower or "unavailable" in lower:
        return "unavailable"
    if "500" in lower or "internal" in lower:
        return "internal_error"

    return "unknown"


def _should_retry(error_type: str, attempt: int, max_attempts: int) -> bool:
    if attempt >= max_attempts:
        return False

    if error_type in ("rate_limit", "quota_exceeded", "token_limit", "not_found", "unavailable", "internal_error"):
        return False

    return True


def _exponential_backoff_wait(attempt: int, base_wait: float, max_wait: float) -> None:
    wait = min(base_wait * (2 ** (attempt - 1)), max_wait)
    jitter = random.uniform(0, wait * 0.3)
    total_wait = wait + jitter
    log(f"Backoff wait: {total_wait:.2f}s (attempt={attempt})", "INFO")
    time.sleep(total_wait)


def _post_json(url: str, headers: dict, payload: dict, timeout: int) -> Optional[dict]:
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if response.status_code >= 400:
            log(f"HTTP {response.status_code}: {response.text[:300]}", "WARNING")
            return None
        return response.json()
    except Exception as exc:
        log(f"HTTP request hatasi: {exc}", "WARNING")
        return None


# ========== GEMINI PROVIDER (v5.3 FIXED) ==========

def _is_thinking_model(model_name: str) -> bool:
    """gemini-2.5-* modelleri thinking modunu destekler."""
    return "2.5" in model_name


def _try_gemini_single_model(prompt: str, model_name: str, cfg: Dict[str, Any]) -> Optional[str]:
    """Try a single Gemini model."""
    api_key = _get_env_str("GEMINI_API_KEY")
    if not api_key:
        return None

    temperature = _safe_float(cfg.get("temperature", 0.65), 0.65)
    max_tokens = _safe_int(cfg.get("max_output_tokens", 1400), 1400)

    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        log(f"Gemini import hatasi: {exc}", "WARNING")
        return None

    try:
        client = genai.Client(api_key=api_key)

        # v5.3 FIX: thinking modellerinde JSON output zorla, thinking budget 0 yap
        # Bu sayede "hesaplama metni + JSON" yerine direkt JSON doner
        if _is_thinking_model(model_name):
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=0,  # Thinking'i devre disi birak
                    ),
                ),
            )
        else:
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
        error_type = _classify_error(str(exc))
        log(f"Gemini {model_name} error ({error_type}): {exc}", "WARNING")
        raise


def _try_gemini_stack(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    """Try all Gemini models in cascade."""
    if not _is_enabled(cfg, "enable_gemini", True):
        return None

    max_attempts, base_wait, max_wait = _get_retry_config()

    for model_idx, model_name in enumerate(GEMINI_MODELS, start=1):
        log(f"Gemini Stack [{model_idx}/{len(GEMINI_MODELS)}]: {model_name}", "INFO")

        for attempt in range(1, max_attempts + 1):
            try:
                result = _try_gemini_single_model(prompt, model_name, cfg)
                if result:
                    log(f"✅ Gemini success: {model_name} (attempt {attempt})", "INFO")
                    return result
            except Exception as exc:
                error_type = _classify_error(str(exc))

                if _should_retry(error_type, attempt, max_attempts):
                    log(f"Gemini {model_name} retry {attempt}/{max_attempts} ({error_type})", "WARNING")
                    _exponential_backoff_wait(attempt, base_wait, max_wait)
                    continue
                else:
                    log(f"Gemini {model_name} skip to next model ({error_type})", "WARNING")
                    break

        log(f"Gemini {model_name} failed, trying next model...", "WARNING")

    log("All Gemini models failed", "WARNING")
    return None


# ========== GROQ PROVIDER ==========
def _try_groq_single_model(prompt: str, model_name: str, cfg: Dict[str, Any]) -> Optional[str]:
    api_key = _get_env_str("GROQ_API_KEY")
    if not api_key:
        return None

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
        error_type = _classify_error(str(exc))
        log(f"Groq {model_name} error ({error_type}): {exc}", "WARNING")
        raise


def _try_groq_stack(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    if not _is_enabled(cfg, "enable_groq", True):
        return None

    max_attempts, base_wait, max_wait = _get_retry_config()

    for model_idx, model_name in enumerate(GROQ_MODELS, start=1):
        log(f"Groq Stack [{model_idx}/{len(GROQ_MODELS)}]: {model_name}", "INFO")

        for attempt in range(1, max_attempts + 1):
            try:
                result = _try_groq_single_model(prompt, model_name, cfg)
                if result:
                    log(f"✅ Groq success: {model_name} (attempt {attempt})", "INFO")
                    return result
            except Exception as exc:
                error_type = _classify_error(str(exc))

                if _should_retry(error_type, attempt, max_attempts):
                    log(f"Groq {model_name} retry {attempt}/{max_attempts} ({error_type})", "WARNING")
                    _exponential_backoff_wait(attempt, base_wait, max_wait)
                    continue
                else:
                    log(f"Groq {model_name} skip to next model ({error_type})", "WARNING")
                    break

        log(f"Groq {model_name} failed, trying next model...", "WARNING")

    log("All Groq models failed", "WARNING")
    return None


# ========== EMERGENCY STACK ==========
def _try_openrouter(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    if not _is_enabled(cfg, "enable_openrouter", True):
        return None

    api_key = _get_env_str("OPENROUTER_API_KEY")
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

    data = _post_json(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        payload=payload,
        timeout=45,
    )
    if not data:
        return None

    choices = data.get("choices", [])
    if not choices:
        return None

    message = choices[0].get("message", {})
    text = (message.get("content", "") or "").strip()
    return text or None


def _try_huggingface(prompt: str, cfg: Dict[str, Any]) -> Optional[str]:
    if not _is_enabled(cfg, "enable_huggingface", True):
        return None

    api_key = _get_env_str("HF_API_KEY")
    if not api_key:
        return None

    model_name = str(cfg.get("hf_model", "mistralai/Mistral-7B-Instruct-v0.2")).strip()
    max_tokens = _safe_int(cfg.get("max_output_tokens", 1400), 1400)

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

    data = _post_json(
        f"https://api-inference.huggingface.co/models/{model_name}",
        headers=headers,
        payload=payload,
        timeout=60,
    )
    if not data:
        return None

    if isinstance(data, list) and data:
        text = (data[0].get("generated_text", "") or "").strip()
        return text or None
    return None


# ========== MAIN AI CLIENT ==========
def ask_ai(prompt: str, stage: str = "generic") -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        log("ai_client.ask_ai: bos prompt", "WARNING")
        return ""

    cfg = _load_ai_config()

    log(f"=== AI Request Start (stage={stage}) ===", "INFO")

    log("Trying GEMINI STACK...", "INFO")
    result = _try_gemini_stack(prompt, cfg)
    if result:
        log("✅ GEMINI STACK SUCCESS", "INFO")
        return result

    log("Trying GROQ STACK...", "INFO")
    result = _try_groq_stack(prompt, cfg)
    if result:
        log("✅ GROQ STACK SUCCESS", "INFO")
        return result

    log("Trying EMERGENCY: OpenRouter...", "INFO")
    result = _try_openrouter(prompt, cfg)
    if result:
        log("✅ OPENROUTER SUCCESS", "INFO")
        return result

    log("Trying EMERGENCY: HuggingFace...", "INFO")
    result = _try_huggingface(prompt, cfg)
    if result:
        log("✅ HUGGINGFACE SUCCESS", "INFO")
        return result

    log("❌ ALL PROVIDERS FAILED", "ERROR")
    return ""


# ========== JSON PARSER (v5.3 ENHANCED) ==========

def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
    if fenced:
        return "\n".join([x.strip() for x in fenced if x.strip()]).strip()

    return cleaned


def _extract_balanced_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    stack: list[str] = []
    start_idx = None

    for idx, ch in enumerate(text):
        if ch in "{[":
            if start_idx is None:
                start_idx = idx
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                continue
            opener = stack[-1]
            if (opener == "{" and ch == "}") or (opener == "[" and ch == "]"):
                stack.pop()
                if not stack and start_idx is not None:
                    snippet = text[start_idx: idx + 1].strip()
                    if snippet:
                        candidates.append(snippet)
                    start_idx = None
            else:
                stack = []
                start_idx = None

    return sorted(set(candidates), key=len, reverse=True)


def _try_raw_decode_stream(text: str):
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)

    while idx < length:
        while idx < length and text[idx].isspace():
            idx += 1
        if idx >= length:
            break
        try:
            obj, _end_idx = decoder.raw_decode(text, idx)
            return obj
        except Exception:
            idx += 1
    return None


def _extract_json_after_thinking(text: str) -> Optional[str]:
    """
    v5.3 FIX: Gemini thinking/reasoning modellerinde metin + JSON karisik gelir.
    Ornek:
      "... guncellik`: 14 (max 15)\n    *   Sum: 94...\n\n[\n  {\"sira\": 1, ...}\n]"

    Bu fonksiyon son JSON blogunu bulur - thinking metni oncesindeki kirli
    kismi atar, JSON'u dondurur.

    Strateji:
      1. En son '[' veya '{' karakterinden baslayan blogu bul
      2. Balanced bracket check ile gecerli JSON'u ayikla
    """
    if not text:
        return None

    # Son JSON array [ veya object { baslangicini bul
    # Oncelik: array (scorer her zaman array dondurur)
    last_array_start = text.rfind("[")
    last_obj_start = text.rfind("{")

    start = max(last_array_start, last_obj_start)
    if start == -1:
        return None

    # Bu noktadan sona kadar dengeyi kontrol et
    snippet = text[start:]
    depth = 0
    opener_char = snippet[0]
    closer_char = "]" if opener_char == "[" else "}"

    for i, ch in enumerate(snippet):
        if ch == opener_char:
            depth += 1
        elif ch == closer_char:
            depth -= 1
            if depth == 0:
                return snippet[: i + 1].strip()

    return None


def parse_ai_json(response: str) -> Any:
    """
    AI yanitini JSON'a cevirir.

    v5.3 EK ADIM: Gemini thinking modellerinin "metin + JSON" karisik
    ciktisini handle etmek icin _extract_json_after_thinking() eklendi.

    Siralama:
    1) Direkt json.loads
    2) Code-fence temizleyip tekrar dener
    3) Dengeli {} / [] bloklarini ayiklayip tek tek dener
    4) v5.3 YENI: Son JSON blogunu thinking metninin ardindan ayikla
    5) raw_decode stream fallback
    """
    cleaned = (response or "").strip()
    if not cleaned:
        return None

    # 1) Direkt
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2) Code-fence temizle
    no_fence = _strip_code_fences(cleaned)
    if no_fence and no_fence != cleaned:
        try:
            return json.loads(no_fence)
        except Exception:
            pass

    source_for_scan = no_fence if no_fence else cleaned

    # 3) Balanced JSON bloklari tara (buyukten kucuge)
    candidates = _extract_balanced_json_candidates(source_for_scan)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            # Bos dict/list kabul etme, anlamli icerik olmali
            if parsed or parsed == 0:
                return parsed
        except Exception:
            continue

    # 4) v5.3 YENI: Thinking metni + JSON karisimi - son JSON blogunu bul
    thinking_extracted = _extract_json_after_thinking(source_for_scan)
    if thinking_extracted and thinking_extracted != source_for_scan:
        try:
            parsed = json.loads(thinking_extracted)
            if parsed or parsed == 0:
                log("parse_ai_json: thinking sonrasi JSON bulundu", "INFO")
                return parsed
        except Exception:
            pass

        # Ayiklanan parcayi da balanced scan'e sok
        sub_candidates = _extract_balanced_json_candidates(thinking_extracted)
        for candidate in sub_candidates:
            try:
                parsed = json.loads(candidate)
                if parsed or parsed == 0:
                    log("parse_ai_json: thinking sonrasi balanced JSON bulundu", "INFO")
                    return parsed
            except Exception:
                continue

    # 5) raw_decode stream fallback
    try:
        parsed = _try_raw_decode_stream(source_for_scan)
        if parsed is not None:
            return parsed
    except Exception as exc:
        log(f"ai_client.parse_ai_json raw_decode error: {exc}", "WARNING")

    log("ai_client.parse_ai_json fallback: parse edilemedi", "WARNING")
    return None
