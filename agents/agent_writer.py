"""
agents/agent_writer.py - Icerik yazma ajani
"""

import json
import os
import re
import sys
import time
from typing import Optional, Union

import requests

from core.logger import log
from core.config_loader import load_config
from core.state_manager import get_stage, set_stage, init_pipeline


def _is_test_mode() -> bool:
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    return "--test" in sys.argv


def _clean_non_turkish_chars(text: str) -> str:
    if not text:
        return text

    cleaned = text

    cleaned = re.sub(r"[\u2e80-\u2eff]", "", cleaned)
    cleaned = re.sub(r"[\u3000-\u303f]", "", cleaned)
    cleaned = re.sub(r"[\u3040-\u309f]", "", cleaned)
    cleaned = re.sub(r"[\u30a0-\u30ff]", "", cleaned)
    cleaned = re.sub(r"[\u3100-\u312f]", "", cleaned)
    cleaned = re.sub(r"[\u3130-\u318f]", "", cleaned)
    cleaned = re.sub(r"[\u31a0-\u31bf]", "", cleaned)
    cleaned = re.sub(r"[\u31f0-\u31ff]", "", cleaned)
    cleaned = re.sub(r"[\u3200-\u32ff]", "", cleaned)
    cleaned = re.sub(r"[\u3300-\u33ff]", "", cleaned)
    cleaned = re.sub(r"[\u3400-\u4dbf]", "", cleaned)
    cleaned = re.sub(r"[\u4e00-\u9fff]", "", cleaned)
    cleaned = re.sub(r"[\uac00-\ud7af]", "", cleaned)
    cleaned = re.sub(r"[\ud7b0-\ud7ff]", "", cleaned)
    cleaned = re.sub(r"[\uf900-\ufaff]", "", cleaned)
    cleaned = re.sub(r"[\u1100-\u11ff]", "", cleaned)
    cleaned = re.sub(r"[\ua960-\ua97f]", "", cleaned)

    cleaned = re.sub(r"[\u0590-\u05ff]", "", cleaned)
    cleaned = re.sub(r"[\u0600-\u06ff]", "", cleaned)
    cleaned = re.sub(r"[\u0750-\u077f]", "", cleaned)
    cleaned = re.sub(r"[\ufb50-\ufdff]", "", cleaned)
    cleaned = re.sub(r"[\ufe70-\ufeff]", "", cleaned)

    cleaned = re.sub(r"[\u0400-\u04ff]", "", cleaned)
    cleaned = re.sub(r"[\u0500-\u052f]", "", cleaned)

    cleaned = re.sub(r"[\u0900-\u097f]", "", cleaned)
    cleaned = re.sub(r"[\u0e00-\u0e7f]", "", cleaned)
    cleaned = re.sub(r"[\u1000-\u109f]", "", cleaned)
    cleaned = re.sub(r"[\u10a0-\u10ff]", "", cleaned)

    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n\s*\n+", "\n\n", cleaned)
    lines = [line.rstrip() for line in cleaned.split("\n")]
    return "\n".join(lines).strip()


def _strip_wrapper_artifacts(text: str) -> str:
    cleaned = (text or "").strip()

    cleaned = re.sub(r"^```(?:text|markdown)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    cleaned = cleaned.strip().strip('"').strip("'").strip()
    return cleaned


def _contains_forbidden_script(text: str) -> bool:
    patterns = [
        r"[\u2e80-\u9fff]",
        r"[\uac00-\ud7ff]",
        r"[\u0590-\u08ff]",
        r"[\u0400-\u052f]",
    ]
    return any(re.search(p, text) for p in patterns)


def _quality_check(post_text: str) -> tuple[bool, str]:
    if not post_text:
        return False, "bos_metin"

    if len(post_text) < 80:
        return False, "cok_kisa"

    if len(post_text) > 1800:
        return False, "cok_uzun"

    line_count = len([ln for ln in post_text.split("\n") if ln.strip()])
    if line_count < 3:
        return False, "satir_az"
    if line_count > 15:
        return False, "satir_fazla"

    if _contains_forbidden_script(post_text):
        return False, "yabanci_alfabe"

    lower = post_text.lower()
    forbidden_cta = [
        "begenmeyi unutmayin",
        "paylasmayi unutmayin",
        "takip etmeyi unutmayin",
        "sayfamizi takip edin",
    ]
    if any(x in lower for x in forbidden_cta):
        return False, "yasak_cta"

    hallucination_baits = [
        "iste o araclar",
        "iste liste",
        "detaylar soyle",
        "detaylar su sekilde",
    ]
    if any(x in lower for x in hallucination_baits):
        return False, "halusinasyon_tetik"

    return True, "ok"


def _fallback_post(article: dict) -> str:
    title = (article.get("title", "") or "").strip()
    summary = (article.get("summary", "") or "").strip()

    safe_title = title.upper()[:90] if title else "OTOMOTIVDE YENI GELISME"
    if summary:
        body = summary[:420].strip()
    else:
        body = "Guncel gelismeyi sade sekilde aktardik. Net bilgiler geldikce paylasacagiz."

    return (
        f"🚗 {safe_title}\n\n"
        f"{body}\n\n"
        "Siz bu gelisme hakkinda ne dusunuyorsunuz?"
    ).strip()


def _repair_post_with_ai(post_text: str, article: dict) -> str:
    title = article.get("title", "")
    summary = article.get("summary", "")
    source = article.get("source_name", "")

    repair_prompt = (
        "Asagidaki Facebook postunu duzelt.\n"
        "Kurallar:\n"
        "- Tamamen Turkce yaz.\n"
        "- Bilgi uydurma.\n"
        "- 15 satiri gecme.\n"
        "- Clickbait asiriligina kacma.\n"
        "- Son satirda dogal bir soru/cagri olsun.\n"
        "- 'begen/paylas/takip et' gibi dogrudan CTA kullanma.\n\n"
        f"Haber basligi: {title}\n"
        f"Kaynak: {source}\n"
        f"Ozet: {summary[:500]}\n\n"
        "Duzeltilecek metin:\n"
        f"{post_text}\n\n"
        "Sadece duzeltilmis post metnini ver."
    )

    return ask_ai(repair_prompt) or ""


def _try_gemini(
    prompt: str,
    model_name: str = None,
    temperature: float = None,
    max_tokens: int = None,
):
    settings = load_config("settings")
    ai_cfg = settings.get("ai", {}) if isinstance(settings, dict) else {}

    if not ai_cfg.get("enable_gemini", True):
        log("Gemini ayarlardan kapali, atlaniyor", "INFO")
        return None

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        log("Gemini API key bulunamadi", "WARNING")
        return None

    cfg_model = ai_cfg.get("gemini_model", "gemini-2.5-flash-lite")
    cfg_temp = ai_cfg.get("temperature", 0.65)
    cfg_max_tokens = ai_cfg.get("max_output_tokens", 1400)

    chosen_model = model_name.strip() if isinstance(model_name, str) and model_name.strip() else cfg_model

    try:
        chosen_temp = float(temperature if temperature is not None else cfg_temp)
    except Exception:
        chosen_temp = 0.65
    chosen_temp = max(0.0, min(2.0, chosen_temp))

    try:
        chosen_max_tokens = int(max_tokens if max_tokens is not None else cfg_max_tokens)
    except Exception:
        chosen_max_tokens = 1400
    if chosen_max_tokens < 1:
        chosen_max_tokens = 1400

    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        log(f"Gemini import hatasi: {exc}", "WARNING")
        return None

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=chosen_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=chosen_temp,
                max_output_tokens=chosen_max_tokens,
            ),
        )

        text = getattr(response, "text", "") or ""
        text = text.strip()
        if not text:
            log(f"Gemini ({chosen_model}) bos yanit verdi", "WARNING")
            return None

        log(f"Gemini ({chosen_model}) yanit verdi", "INFO")
        return text

    except Exception as exc:
        log(f"Gemini ({chosen_model}) hatasi: {exc}", "WARNING")
        return None


def _try_groq(
    prompt: str,
    temperature: float,
    max_tokens: int,
    model_name: str,
) -> Optional[str]:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        log("GROQ_API_KEY bulunamadi, Groq atlaniyor", "INFO")
        return None

    models_to_try = [model_name]
    if "llama-3.3-70b" in model_name:
        models_to_try.append("llama-3.1-8b-instant")

    try:
        from groq import Groq

        client = Groq(api_key=api_key)

        for current_model in models_to_try:
            try:
                response = client.chat.completions.create(
                    model=current_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if response and response.choices:
                    text = response.choices[0].message.content
                    if text:
                        log(f"Groq ({current_model}) yanit verdi")
                        return text.strip()
            except Exception as groq_err:
                err_str = str(groq_err)
                if "429" in err_str or "rate_limit" in err_str:
                    wait_match = re.search(r"in\s+(\d+)m(\d+\.?\d*)s", err_str)
                    if wait_match:
                        total_wait = int(wait_match.group(1)) * 60 + float(wait_match.group(2))
                        if total_wait <= 60:
                            log(f"Groq rate limit -> {int(total_wait)}sn bekleniyor", "WARNING")
                            time.sleep(total_wait + 1)
                log(f"Groq ({current_model}) hatasi: {err_str[:150]}", "WARNING")
                continue

        return None

    except ImportError:
        log("groq paketi yuklu degil", "WARNING")
        return None
    except Exception as exc:
        log(f"Groq genel hata: {exc}", "WARNING")
        return None


_OPENROUTER_FREE_MODELS = [
    "deepseek/deepseek-r1-0528:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "qwen/qwen3-14b:free",
]


def _try_openrouter_single(
    prompt: str,
    temperature: float,
    max_tokens: int,
    model: str,
    api_key: str,
) -> Optional[str]:
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/otoXtra-bot",
                "X-Title": "otoXtra Bot",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=90,
        )

        if response.status_code == 200:
            data = response.json()
            if "error" in data:
                return None
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "").strip()
                if text:
                    return text
        return None
    except Exception:
        return None


def _try_openrouter(
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> Optional[str]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        log("OPENROUTER_API_KEY bulunamadi, OpenRouter atlaniyor", "INFO")
        return None

    for model in _OPENROUTER_FREE_MODELS:
        result = _try_openrouter_single(prompt, temperature, max_tokens, model, api_key)
        if result:
            log(f"OpenRouter ({model}) yanit verdi")
            return result
        time.sleep(1)

    return None


def _try_huggingface(
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> Optional[str]:
    api_key = os.environ.get("HF_API_KEY", "")
    if not api_key:
        log("HF_API_KEY bulunamadi, HuggingFace atlaniyor", "INFO")
        return None

    hf_models = [
        "mistralai/Mistral-7B-Instruct-v0.3",
        "microsoft/Phi-3-mini-4k-instruct",
        "HuggingFaceH4/zephyr-7b-beta",
    ]

    try:
        from huggingface_hub import InferenceClient

        client = InferenceClient(token=api_key)

        for model in hf_models:
            try:
                response = client.chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if response and response.choices:
                    text = response.choices[0].message.content
                    if text:
                        log(f"HuggingFace ({model}) yanit verdi")
                        return text.strip()
            except Exception:
                continue

        return None

    except Exception as exc:
        log(f"HuggingFace genel hata: {exc}", "WARNING")
        return None


def ask_ai(prompt: str) -> str:
    settings = load_config("settings")
    ai_settings = settings.get("ai", {}) if isinstance(settings, dict) else {}

    temperature = float(ai_settings.get("temperature", 0.65))
    max_tokens = int(ai_settings.get("max_output_tokens", 1400))
    gemini_model = ai_settings.get("gemini_model", "gemini-2.5-flash-lite")
    groq_model = ai_settings.get("groq_model", "llama-3.3-70b-versatile")

    result = _try_gemini(
        prompt,
        model_name=gemini_model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if result:
        return result

    result = _try_groq(prompt, temperature, max_tokens, groq_model)
    if result:
        return result

    result = _try_openrouter(prompt, temperature, max_tokens)
    if result:
        return result

    result = _try_huggingface(prompt, temperature, max_tokens)
    if result:
        return result

    log("Hicbir YZ saglayicisi yanit veremedi", "ERROR")
    return ""


def _fix_truncated_json_array(text: str) -> Optional[str]:
    text = text.strip()
    if not text.startswith("["):
        return None

    if text.endswith("]"):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

    search_end = len(text)
    for _ in range(20):
        close_pos = text.rfind("}", 0, search_end)
        if close_pos <= 0:
            break

        candidate = text[: close_pos + 1].rstrip()
        if candidate.endswith(","):
            candidate = candidate[:-1].rstrip()
        candidate += "\n]"

        try:
            result = json.loads(candidate)
            if isinstance(result, list) and len(result) > 0:
                return candidate
        except json.JSONDecodeError:
            pass

        search_end = close_pos

    return None


def parse_ai_json(text: str) -> Optional[Union[list, dict]]:
    if not text:
        log("parse_ai_json: Bos metin", "WARNING")
        return None

    cleaned = text.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    code_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", cleaned, re.DOTALL)
    if code_match:
        block = code_match.group(1).strip()
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            cleaned = block

    fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)
    if fixed != cleaned:
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    array_match = re.search(r"\[.*\]", fixed, re.DOTALL)
    if array_match:
        try:
            return json.loads(array_match.group(0))
        except json.JSONDecodeError:
            pass

    bracket_pos = cleaned.find("[")
    if bracket_pos >= 0:
        recovered = _fix_truncated_json_array(cleaned[bracket_pos:])
        if recovered:
            try:
                return json.loads(recovered)
            except json.JSONDecodeError:
                pass

    object_match = re.search(r"\{[^{}]*\}", cleaned)
    if object_match:
        try:
            result = json.loads(object_match.group(0))
            return [result] if isinstance(result, dict) else result
        except json.JSONDecodeError:
            pass

    line_results = []
    for line in cleaned.split("\n"):
        line = line.strip().rstrip(",")
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    line_results.append(obj)
            except json.JSONDecodeError:
                pass

    if line_results:
        return line_results

    log(f"parse_ai_json parse edilemedi: {cleaned[:200]}", "WARNING")
    return None


def generate_post_text(article: dict) -> str:
    prompts_config = load_config("prompts")
    writer_prompt = prompts_config.get("post_writer", "") if isinstance(prompts_config, dict) else ""

    if not writer_prompt:
        log("post_writer promptu bulunamadi", "WARNING")
        return ""

    title = article.get("title", "")
    summary = article.get("summary", "")
    full_text = article.get("full_text", "")
    source = article.get("source_name", "")

    input_parts = [
        f"BASLIK: {title}",
        f"KAYNAK: {source}",
        f"OZET: {summary[:500]}",
    ]
    if full_text:
        input_parts.append(f"TAM_METIN: {full_text[:1500]}")

    full_prompt = (
        f"{writer_prompt}\n\n"
        "Cikti sinirlari:\n"
        "- Maksimum 15 satir\n"
        "- Bos satirlar dahil duzenli format\n"
        "- Sadece post metnini dondur\n\n"
        + "\n".join(input_parts)
    )

    post_text = ask_ai(full_prompt)
    post_text = _strip_wrapper_artifacts(post_text)
    post_text = _clean_non_turkish_chars(post_text)

    ok, reason = _quality_check(post_text)
    if not ok:
        log(f"Ilk yazi kalite kontrolden gecemedi: {reason}", "WARNING")
        repaired = _repair_post_with_ai(post_text, article)
        repaired = _strip_wrapper_artifacts(repaired)
        repaired = _clean_non_turkish_chars(repaired)
        ok2, reason2 = _quality_check(repaired)
        if ok2:
            post_text = repaired
        else:
            log(f"Duzeltilmis yazi da gecemedi: {reason2}. Fallback kullaniliyor.", "WARNING")
            post_text = _fallback_post(article)

    if len(post_text) < 30:
        return _fallback_post(article)

    return post_text


def run() -> bool:
    log("-" * 55)
    log("agent_writer basliyor")
    log("-" * 55)

    score_stage = get_stage("score")
    if score_stage.get("status") != "done":
        log("score asamasi tamamlanmamis", "ERROR")
        set_stage("write", "error", error="score asamasi tamamlanmamis")
        return False

    score_output = score_stage.get("output", {})
    article = score_output.get("selected_article", {})

    if not article:
        log("Score cikisinda haber yok", "WARNING")
        set_stage("write", "error", error="Score cikisinda haber yok")
        return False

    set_stage("write", "running")

    try:
        article_url = article.get("link", "")
        if article_url:
            try:
                from agents.agent_fetcher import scrape_full_article

                full_text = scrape_full_article(article_url)
                if full_text:
                    article["full_text"] = full_text
            except Exception as scrape_err:
                log(f"Tam metin cekme hatasi: {scrape_err}", "WARNING")

        post_text = generate_post_text(article)

        if not post_text:
            set_stage("write", "error", error="Post metni uretilemedi")
            return False

        output = {
            "article": article,
            "post_text": post_text,
            "post_text_length": len(post_text),
        }
        set_stage("write", "done", output=output)
        log(f"agent_writer tamamlandi -> {len(post_text)} karakter")
        return True

    except Exception as exc:
        log(f"agent_writer kritik hata: {exc}", "ERROR")
        set_stage("write", "error", error=str(exc))
        return False


if __name__ == "__main__":
    log("=== agent_writer.py modul testi basliyor ===")
    init_pipeline("test-writer")

    fake_article = {
        "title": "Test: Yeni Elektrikli SUV Turkiye'de Satisa Cikti",
        "link": "https://test.com/haber1",
        "summary": "Yeni elektrikli SUV modeli Turkiye pazarina girdi. Fiyatlar ve teknik ozellikler aciklandi.",
        "published": "2025-01-15T12:00:00+03:00",
        "image_url": "",
        "source_name": "Test Kaynak",
        "source_priority": "high",
        "can_scrape_image": False,
        "score": 78,
    }

    set_stage("score", "done", output={"selected_article": fake_article, "score": 78})
    success = run()
    log(f"writer test success: {success}")
    log("=== agent_writer.py modul testi tamamlandi ===")
