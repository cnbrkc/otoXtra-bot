"""
agents/agent_writer.py — İçerik Yazma Ajanı (v6)

otoXtra Facebook Botu için pipeline'dan seçilen haberi alıp
YZ ile Facebook post metni yazan bağımsız ajan.

YZ Sağlayıcı Zinciri (fallback):
  1. Google Gemini  → GEMINI_API_KEY  (gemini-2.0-flash → gemini-1.5-flash)
  2. Groq           → GROQ_API_KEY    (llama-3.3-70b → llama-3.1-8b)
  3. OpenRouter     → OPENROUTER_API_KEY (4 ücretsiz model)
  4. HuggingFace    → HF_API_KEY      (3 model)

Bağımsız çalıştırma:
    python agents/agent_writer.py
    python agents/agent_writer.py --test

Diğer modüller bu ajanı şöyle import eder:
    from agents.agent_writer import run, ask_ai, parse_ai_json
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


# ============================================================
# TEST MODU
# ============================================================

def _is_test_mode() -> bool:
    """TEST_MODE ortam değişkenini veya --test argümanını kontrol eder."""
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    if "--test" in sys.argv:
        return True
    return False


# ============================================================
# 1. KARAKTER TEMİZLEME
# ============================================================

def _clean_non_turkish_chars(text: str) -> str:
    """Türkçe dışı alfabe karakterlerini temizler.

    Groq (Llama) modeli bazen Korece, Japonca, Çince veya
    başka alfabe karakterleri üretiyor. Bu fonksiyon onları siler.

    İzin verilenler:
      - Türkçe Latin alfabesi (a-z, A-Z, çğıöşüÇĞİÖŞÜ)
      - Rakamlar, noktalama, semboller
      - Emoji'ler
      - Boşluk ve satır sonu

    Args:
        text: Temizlenecek metin.

    Returns:
        str: Temizlenmiş metin.
    """
    if not text:
        return text

    cleaned = text

    # CJK (Çince, Japonca, Korece)
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

    # Arapça / İbranice
    cleaned = re.sub(r"[\u0590-\u05ff]", "", cleaned)
    cleaned = re.sub(r"[\u0600-\u06ff]", "", cleaned)
    cleaned = re.sub(r"[\u0750-\u077f]", "", cleaned)
    cleaned = re.sub(r"[\ufb50-\ufdff]", "", cleaned)
    cleaned = re.sub(r"[\ufe70-\ufeff]", "", cleaned)

    # Kiril
    cleaned = re.sub(r"[\u0400-\u04ff]", "", cleaned)
    cleaned = re.sub(r"[\u0500-\u052f]", "", cleaned)

    # Diğer alfabeler
    cleaned = re.sub(r"[\u0900-\u097f]", "", cleaned)
    cleaned = re.sub(r"[\u0e00-\u0e7f]", "", cleaned)
    cleaned = re.sub(r"[\u1000-\u109f]", "", cleaned)
    cleaned = re.sub(r"[\u10a0-\u10ff]", "", cleaned)

    # Çoklu boşluk ve boş satır temizliği
    cleaned = re.sub(r"  +", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n\s*\n", "\n\n", cleaned)
    lines = [line.rstrip() for line in cleaned.split("\n")]
    cleaned = "\n".join(lines)

    return cleaned.strip()


# ============================================================
# 2. YZ SAĞLAYICILARI
# ============================================================

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
    cfg_temp = ai_cfg.get("temperature", 0.7)
    cfg_max_tokens = ai_cfg.get("max_output_tokens", 2048)

    chosen_model = cfg_model
    if isinstance(model_name, str) and model_name.strip():
        chosen_model = model_name.strip()

    raw_temp = temperature if temperature is not None else cfg_temp
    try:
        chosen_temp = float(raw_temp)
    except Exception:
        chosen_temp = 0.7
    if chosen_temp < 0.0:
        chosen_temp = 0.0
    if chosen_temp > 2.0:
        chosen_temp = 2.0

    raw_max = max_tokens if max_tokens is not None else cfg_max_tokens
    try:
        chosen_max_tokens = int(raw_max)
    except Exception:
        chosen_max_tokens = 2048
    if chosen_max_tokens < 1:
        chosen_max_tokens = 2048

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
    """Groq API ile metin üretir (yedek 1).

    İlk model başarısızsa llama-3.1-8b-instant'ı dener.
    Rate limit ≤60sn ise bekleyip retry yapar.
    """
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        log("GROQ_API_KEY bulunamadı, Groq atlanıyor", "INFO")
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
                        log(f"✅ Groq ({current_model}) yanıt verdi")
                        return text.strip()

                log(f"⚠️ Groq ({current_model}) boş yanıt", "WARNING")

            except Exception as groq_err:
                err_str = str(groq_err)
                if "429" in err_str or "rate_limit" in err_str:
                    wait_match = re.search(r"in\s+(\d+)m(\d+\.?\d*)s", err_str)
                    if wait_match:
                        total_wait = (
                            int(wait_match.group(1)) * 60
                            + float(wait_match.group(2))
                        )
                        if total_wait <= 60:
                            log(
                                f"⚠️ Groq ({current_model}) rate limit → "
                                f"{int(total_wait)}sn bekleniyor...",
                                "WARNING",
                            )
                            time.sleep(total_wait + 1)
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
                                        log(f"✅ Groq ({current_model}) 2. denemede yanıt verdi")
                                        return text.strip()
                            except Exception:
                                pass

                    log(
                        f"⚠️ Groq ({current_model}) rate limit — "
                        f"sonraki model deneniyor...",
                        "WARNING",
                    )
                else:
                    log(
                        f"⚠️ Groq ({current_model}) hatası: {err_str[:150]}",
                        "WARNING",
                    )
                continue

        log("⚠️ Groq: Tüm modeller başarısız", "WARNING")
        return None

    except ImportError:
        log("⚠️ groq paketi yüklü değil", "WARNING")
        return None
    except Exception as exc:
        log(f"⚠️ Groq genel hata: {exc}", "WARNING")
        return None


# OpenRouter ücretsiz model listesi (Mart 2025)
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
    """OpenRouter API'ye tek model ile istek gönderir."""
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
                log(
                    f"  ⚠️ OpenRouter ({model}) hata: "
                    f"{data['error'].get('message', '')[:150]}",
                    "WARNING",
                )
                return None
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "").strip()
                if text:
                    log(f"  ✅ OpenRouter ({model}) yanıt verdi")
                    return text
            log(f"  ⚠️ OpenRouter ({model}) boş yanıt", "WARNING")
            return None

        elif response.status_code == 404:
            log(f"  ⚠️ OpenRouter ({model}) model bulunamadı (404)", "WARNING")
        elif response.status_code == 429:
            log(f"  ⚠️ OpenRouter ({model}) rate limit (429)", "WARNING")
        else:
            log(
                f"  ⚠️ OpenRouter ({model}) hata: "
                f"{response.status_code} — {response.text[:200]}",
                "WARNING",
            )
        return None

    except requests.exceptions.Timeout:
        log(f"  ⚠️ OpenRouter ({model}) zaman aşımı", "WARNING")
        return None
    except Exception as exc:
        log(f"  ⚠️ OpenRouter ({model}) exception: {exc}", "WARNING")
        return None


def _try_openrouter(
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> Optional[str]:
    """OpenRouter API ile ücretsiz model çağrısı (yedek 2).

    Birden fazla ücretsiz modeli sırasıyla dener.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        log("OPENROUTER_API_KEY bulunamadı, OpenRouter atlanıyor", "INFO")
        return None

    log(f"🔄 OpenRouter: {len(_OPENROUTER_FREE_MODELS)} model denenecek")

    for model in _OPENROUTER_FREE_MODELS:
        result = _try_openrouter_single(prompt, temperature, max_tokens, model, api_key)
        if result:
            return result
        time.sleep(1)

    log("⚠️ OpenRouter: Tüm modeller başarısız", "WARNING")
    return None


def _try_huggingface(
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> Optional[str]:
    """HuggingFace Inference API ile metin üretir (yedek 3)."""
    api_key = os.environ.get("HF_API_KEY", "")
    if not api_key:
        log("HF_API_KEY bulunamadı, HuggingFace atlanıyor", "INFO")
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
                log(f"  🤖 HuggingFace deneniyor: {model}")
                response = client.chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if response and response.choices:
                    text = response.choices[0].message.content
                    if text:
                        log(f"  ✅ HuggingFace ({model}) yanıt verdi")
                        return text.strip()
                log(f"  ⚠️ HuggingFace ({model}) boş yanıt", "WARNING")

            except Exception as model_err:
                err_str = str(model_err)
                if "401" in err_str or "Unauthorized" in err_str:
                    log(
                        "❌ HuggingFace API KEY GEÇERSİZ! "
                        "https://huggingface.co/settings/tokens",
                        "ERROR",
                    )
                    return None
                elif "429" in err_str or "rate" in err_str.lower():
                    log(f"  ⚠️ HuggingFace ({model}) rate limit", "WARNING")
                else:
                    log(f"  ⚠️ HuggingFace ({model}) hatası: {err_str[:150]}", "WARNING")
                continue

        log("⚠️ HuggingFace: Tüm modeller başarısız", "WARNING")
        return None

    except ImportError:
        log("⚠️ huggingface_hub paketi yüklü değil", "WARNING")
        return None
    except Exception as exc:
        log(f"⚠️ HuggingFace genel hata: {exc}", "WARNING")
        return None


# ============================================================
# 3. ANA YZ FONKSİYONU (PUBLIC API)
# ============================================================

def ask_ai(prompt: str) -> str:
    """YZ'ye prompt gönderir, yanıt alır.

    Sağlayıcıları sırayla dener:
    Gemini → Groq → OpenRouter → HuggingFace

    Bu fonksiyon agent_scorer.py tarafından da import edilir.

    Args:
        prompt: YZ'ye gönderilecek metin.

    Returns:
        str: YZ yanıtı. Hiçbiri başarılı olmazsa boş string.
    """
    settings = load_config("settings")
    ai_settings = settings.get("ai", {})

    temperature = ai_settings.get("temperature", 0.7)
    max_tokens = ai_settings.get("max_output_tokens", 2048)
    gemini_model = ai_settings.get("gemini_model", "gemini-2.0-flash")
    groq_model = ai_settings.get("groq_model", "llama-3.3-70b-versatile")

    log(f"🤖 YZ'ye gönderiliyor: {prompt[:100].replace(chr(10), ' ')}...")

    # Sağlayıcı 1: Gemini
    result = _try_gemini(prompt, temperature, max_tokens, gemini_model)
    if result:
        return result

    # Sağlayıcı 2: Groq
    log("🔄 Gemini başarısız → Groq deneniyor...")
    result = _try_groq(prompt, temperature, max_tokens, groq_model)
    if result:
        return result

    # Sağlayıcı 3: OpenRouter
    log("🔄 Groq başarısız → OpenRouter deneniyor...")
    result = _try_openrouter(prompt, temperature, max_tokens)
    if result:
        return result

    # Sağlayıcı 4: HuggingFace
    log("🔄 OpenRouter başarısız → HuggingFace deneniyor...")
    result = _try_huggingface(prompt, temperature, max_tokens)
    if result:
        return result

    log("❌ Hiçbir YZ sağlayıcısı yanıt veremedi!", "ERROR")
    return ""


# ============================================================
# 4. JSON PARSE — KESİK JSON KURTARMA
# ============================================================

def _fix_truncated_json_array(text: str) -> Optional[str]:
    """Token limiti dolunca yarım kalan JSON array'i düzeltir.

    Strateji:
      1. Son tam '}' bul
      2. Sonrasını kes
      3. Trailing virgül temizle
      4. ']' ekle
      5. Parse et
      6. Başarısızsa bir önceki '}' ile tekrar dene (max 20 deneme)

    Returns:
        str: Düzeltilmiş JSON string. Başarısızsa None.
    """
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
    """YZ yanıtından JSON verisini çıkarır ve parse eder.

    7 aşamalı parse stratejisi:
      1. Düz JSON parse
      2. Markdown code block içinden çıkar
      3. Trailing comma temizle
      4. Regex ile [ ... ] array bul
      5. Kesik JSON kurtarma
      6. Regex ile { ... } tek object bul
      7. Satır satır JSON parse (JSON Lines)

    Bu fonksiyon agent_scorer.py tarafından da import edilir.

    Args:
        text: YZ'den gelen ham yanıt.

    Returns:
        list | dict: Parse edilmiş JSON. Başarısızsa None.
    """
    if not text:
        log("parse_ai_json: Boş metin", "WARNING")
        return None

    cleaned = text.strip()

    # Deneme 1: Düz JSON parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Deneme 2: Markdown code block
    code_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", cleaned, re.DOTALL)
    if code_match:
        block = code_match.group(1).strip()
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            cleaned = block

    # Deneme 3: Trailing comma temizle
    fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)
    if fixed != cleaned:
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Deneme 4: [ ... ] array bul
    array_match = re.search(r"\[.*\]", fixed, re.DOTALL)
    if array_match:
        try:
            return json.loads(array_match.group(0))
        except json.JSONDecodeError:
            pass

    # Deneme 5: Kesik JSON kurtarma
    bracket_pos = cleaned.find("[")
    if bracket_pos >= 0:
        recovered = _fix_truncated_json_array(cleaned[bracket_pos:])
        if recovered:
            try:
                result = json.loads(recovered)
                count = len(result) if isinstance(result, list) else 1
                log(f"🔧 Kesik JSON kurtarıldı: {count} öğe")
                return result
            except json.JSONDecodeError:
                pass

    # Deneme 6: { ... } tek object
    object_match = re.search(r"\{[^{}]*\}", cleaned)
    if object_match:
        try:
            result = json.loads(object_match.group(0))
            return [result] if isinstance(result, dict) else result
        except json.JSONDecodeError:
            pass

    # Deneme 7: Satır satır JSON Lines
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
        log(f"🔧 JSON Lines parse: {len(line_results)} öğe bulundu")
        return line_results

    log(
        f"⚠️ parse_ai_json: Parse edilemedi. "
        f"İlk 200 karakter: {cleaned[:200]}",
        "WARNING",
    )
    return None


# ============================================================
# 5. POST METNİ ÜRETİMİ
# ============================================================

def generate_post_text(article: dict) -> str:
    """Haber bilgilerinden Facebook post metni üretir.

    Args:
        article: Haber dict'i (title, summary, full_text, source_name vb.)

    Returns:
        str: Facebook post metni. Üretilemezse boş string.
    """
    prompts_config = load_config("prompts")
    writer_prompt = prompts_config.get("post_writer", "")

    if not writer_prompt:
        log("⚠️ post_writer promptu bulunamadı", "WARNING")
        return ""

    title = article.get("title", "")
    summary = article.get("summary", "")
    full_text = article.get("full_text", "")
    source = article.get("source_name", "")

    parts = [f"BAŞLIK: {title}"]
    if source:
        parts.append(f"KAYNAK: {source}")
    if summary:
        parts.append(f"ÖZET: {summary[:500]}")
    if full_text:
        parts.append(f"TAM METİN: {full_text[:1500]}")

    full_prompt = f"{writer_prompt}\n\n{chr(10).join(parts)}"

    log(f"✍️ Post metni üretiliyor: {title[:80]}...")

    post_text = ask_ai(full_prompt)

    if not post_text:
        log("❌ Post metni üretilemedi", "ERROR")
        return ""

    # Temel temizlik
    post_text = post_text.strip().strip('"').strip("'")

    # Yabancı karakter temizliği
    original_len = len(post_text)
    post_text = _clean_non_turkish_chars(post_text)
    cleaned_len = len(post_text)

    if cleaned_len < original_len:
        log(f"🧹 {original_len - cleaned_len} yabancı karakter temizlendi")

    if len(post_text) < 30:
        log(f"⚠️ Üretilen metin çok kısa ({len(post_text)} karakter)", "WARNING")
        return ""

    log(f"✅ Post metni hazır ({len(post_text)} karakter)")
    return post_text


# ============================================================
# 6. AJAN GİRİŞ NOKTASI
# ============================================================

def run() -> bool:
    """Ajanı çalıştırır. orchestrator.py tarafından çağrılır.

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    log("─" * 55)
    log("agent_writer başlıyor")
    log("─" * 55)

    # Score aşaması bitti mi?
    score_stage = get_stage("score")
    if score_stage.get("status") != "done":
        log("score aşaması tamamlanmamış — writer çalıştırılamaz", "ERROR")
        set_stage("write", "error", error="score aşaması tamamlanmamış")
        return False

    # Score çıktısından seçilen haberi al
    score_output = score_stage.get("output", {})
    article = score_output.get("selected_article", {})

    if not article:
        log("Score çıktısında haber yok", "WARNING")
        set_stage("write", "error", error="Score çıktısında haber yok")
        return False

    log(f"Seçilen haber: {article.get('title', '')[:60]}")

    # Aşamayı çalışıyor işaretle
    set_stage("write", "running")

    try:
        # Tam metin çek (agent_fetcher'daki scrape fonksiyonunu kullan)
        full_text = ""
        article_url = article.get("link", "")
        if article_url:
            try:
                from agents.agent_fetcher import scrape_full_article
                full_text = scrape_full_article(article_url)
                if full_text:
                    article["full_text"] = full_text
                    log(f"📄 Tam metin eklendi: {len(full_text)} karakter")
            except Exception as scrape_err:
                log(f"⚠️ Tam metin çekme hatası: {scrape_err}", "WARNING")

        # Post metni üret
        post_text = generate_post_text(article)

        if not post_text:
            log("Post metni üretilemedi", "WARNING")
            set_stage("write", "error", error="Post metni üretilemedi")
            return False

        # Pipeline'a yaz
        output = {
            "article": article,
            "post_text": post_text,
            "post_text_length": len(post_text),
        }
        set_stage("write", "done", output=output)

        log(
            f"agent_writer tamamlandı → "
            f"{len(post_text)} karakterlik metin pipeline'a yazıldı"
        )
        return True

    except Exception as exc:
        log(f"agent_writer kritik hata: {exc}", "ERROR")
        set_stage("write", "error", error=str(exc))
        return False


# ============================================================
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ============================================================

if __name__ == "__main__":
    log("=== agent_writer.py modül testi başlıyor ===")

    # Test için pipeline başlat
    init_pipeline("test-writer")

    # Önce fetcher + scorer çalıştır
    log("Önce agent_fetcher çalıştırılıyor...")
    try:
        from agents.agent_fetcher import run as fetcher_run
        fetcher_run()
    except Exception as e:
        log(f"agent_fetcher çalıştırılamadı: {e} — sahte veri kullanılıyor", "WARNING")

        # Sahte score verisi
        fake_article = {
            "title": "Test: Yeni Elektrikli SUV Türkiye'de Satışa Çıktı",
            "link": "https://test.com/haber1",
            "summary": (
                "Yeni elektrikli SUV modeli Türkiye pazarına girdi. "
                "Fiyatlar ve teknik özellikler açıklandı."
            ),
            "published": "2025-01-15T12:00:00+03:00",
            "image_url": "",
            "source_name": "Test Kaynak",
            "source_priority": "high",
            "can_scrape_image": False,
            "score": 78,
        }
        set_stage("fetch", "done", output={
            "articles": [fake_article],
            "count": 1
        })
        set_stage("score", "done", output={
            "selected_article": fake_article,
            "score": 78,
            "title": fake_article["title"],
        })

    # Eğer score aşaması yoksa scorer'ı çalıştır
    score_stage = get_stage("score")
    if score_stage.get("status") != "done":
        log("agent_scorer çalıştırılıyor...")
        try:
            from agents.agent_scorer import run as scorer_run
            scorer_run()
        except Exception as e:
            log(f"agent_scorer çalıştırılamadı: {e}", "WARNING")

    # Writer'ı çalıştır
    log("\nagent_writer çalıştırılıyor...")
    success = run()

    if success:
        write_stage = get_stage("write")
        output = write_stage.get("output", {})
        post_text = output.get("post_text", "")
        article = output.get("article", {})

        log(f"\n{'─' * 50}")
        log("SONUÇ:")
        log(f"  Haber   : {article.get('title', 'YOK')[:60]}")
        log(f"  Uzunluk : {len(post_text)} karakter")
        log(f"\n  POST METNİ:")
        log(f"  {'─' * 40}")
        for line in post_text[:500].split("\n"):
            log(f"  {line}")
        if len(post_text) > 500:
            log(f"  ... ({len(post_text) - 500} karakter daha)")
        log(f"  {'─' * 40}")
    else:
        log("Ajan başarısız oldu", "WARNING")

    log("=== agent_writer.py modül testi tamamlandı ===")
