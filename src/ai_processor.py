"""
ai_processor.py — Yapay Zeka Metin İşleme Modülü (v4 — OpenRouter + Retry)

Bu modül tüm YZ (yapay zeka) işlemlerini yönetir:
  - Haber değerlendirme için YZ'ye soru sorma
  - Facebook post metni üretme
  - Görsel üretim promptu oluşturma

YZ Sağlayıcı Zinciri (fallback):
  1. Google Gemini (ana)     → GEMINI_API_KEY
  2. Groq (yedek 1)         → GROQ_API_KEY
  3. OpenRouter (yedek 2)   → OPENROUTER_API_KEY    ← YENİ
  4. HuggingFace (yedek 3)  → HF_API_KEY

v4 Değişiklikler:
  - OpenRouter desteği eklendi (ücretsiz modeller)
  - Rate limit retry mantığı eklendi (429 hatalarında bekleme)
  - Gemini 429 hatasında 20sn bekleme ve tekrar deneme

v3 Değişiklikler:
  - _clean_non_turkish_chars: Korece/Japonca/Çince karakter temizleme
  - generate_post_text: Üretilen metin otomatik temizleniyor

v2 Değişiklikler:
  - parse_ai_json: Kesik JSON kurtarma (token limiti dolmuş yanıtlar)
  - parse_ai_json: Trailing comma temizleme
  - parse_ai_json: Satır satır JSON parse (fallback)

Ortam değişkenleri:
  - GEMINI_API_KEY      (Google AI Studio'dan alınır)
  - GROQ_API_KEY        (console.groq.com'dan alınır)
  - OPENROUTER_API_KEY  (openrouter.ai'dan alınır)       ← YENİ
  - HF_API_KEY          (huggingface.co'dan alınır)

NOT: google-genai paketi kullanılıyor (google-generativeai DEĞİL).
     pip install google-genai
"""

import json
import os
import re
import time                                                    # ← YENİ
from typing import Optional, Union

import requests                                                # ← YENİ

from utils import load_config, log


# ──────────────────────────────────────────────
# Karakter Temizleme
# ──────────────────────────────────────────────

def _clean_non_turkish_chars(text: str) -> str:
    """
    Türkçe dışı alfabe karakterlerini temizler.

    Groq (Llama) modeli bazen Korece, Japonca, Çince veya
    başka alfabe karakterleri üretiyor. Bu fonksiyon onları siler.

    İzin verilen karakterler:
      - Türkçe Latin alfabesi (a-z, A-Z, çğıöşüÇĞİÖŞÜ)
      - Rakamlar (0-9)
      - Noktalama işaretleri ve semboller
      - Emoji'ler (Unicode emoji blokları)
      - Boşluk ve satır sonu karakterleri

    Silinen karakterler:
      - CJK (Çince, Japonca, Korece) karakterler
      - Arapça / İbranice karakterler
      - Kiril alfabesi
      - Devanagari, Tay ve diğer alfabeler

    Args:
        text: Temizlenecek metin.

    Returns:
        Temizlenmiş metin.
    """
    if not text:
        return text

    cleaned: str = text

    # ── CJK karakterleri sil (Çince, Japonca, Korece) ──
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

    # ── Arapça / İbranice sil ──
    cleaned = re.sub(r"[\u0590-\u05ff]", "", cleaned)
    cleaned = re.sub(r"[\u0600-\u06ff]", "", cleaned)
    cleaned = re.sub(r"[\u0750-\u077f]", "", cleaned)
    cleaned = re.sub(r"[\ufb50-\ufdff]", "", cleaned)
    cleaned = re.sub(r"[\ufe70-\ufeff]", "", cleaned)

    # ── Kiril alfabesi sil ──
    cleaned = re.sub(r"[\u0400-\u04ff]", "", cleaned)
    cleaned = re.sub(r"[\u0500-\u052f]", "", cleaned)

    # ── Diğer alfabeler sil ──
    cleaned = re.sub(r"[\u0900-\u097f]", "", cleaned)
    cleaned = re.sub(r"[\u0e00-\u0e7f]", "", cleaned)
    cleaned = re.sub(r"[\u1000-\u109f]", "", cleaned)
    cleaned = re.sub(r"[\u10a0-\u10ff]", "", cleaned)

    # ── Temizlik: Birden fazla boşluğu tek boşluğa indir ──
    cleaned = re.sub(r"  +", " ", cleaned)

    # ── Temizlik: 3+ boş satırı 2'ye indir ──
    cleaned = re.sub(r"\n\s*\n\s*\n", "\n\n", cleaned)

    # ── Temizlik: Satır başı/sonu gereksiz boşluklar ──
    lines: list[str] = cleaned.split("\n")
    lines = [line.rstrip() for line in lines]
    cleaned = "\n".join(lines)

    return cleaned.strip()


# ──────────────────────────────────────────────
# YZ Sağlayıcıları
# ──────────────────────────────────────────────

def _try_gemini(prompt: str, temperature: float, max_tokens: int, model_name: str) -> Optional[str]:
    """
    Google Gemini API ile metin üretir.
    429 hatasında 20sn bekleyip bir kez daha dener.                ← YENİ
    """
    api_key: str = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        log("ℹ️ GEMINI_API_KEY bulunamadı, Gemini atlanıyor", "INFO")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        # ── İlk deneme ──
        try:                                                       # ← YENİ BLOK BAŞI
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )

            if response and response.text:
                log(f"✅ Gemini ({model_name}) yanıt verdi", "INFO")
                return response.text.strip()

            log("⚠️ Gemini boş yanıt döndü", "WARNING")
            return None

        except Exception as first_err:
            err_str = str(first_err)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                log("⚠️ Gemini rate limit (429), 20sn bekleniyor...", "WARNING")
                time.sleep(20)

                # ── Tekrar dene ──
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=temperature,
                            max_output_tokens=max_tokens,
                        ),
                    )
                    if response and response.text:
                        log(f"✅ Gemini ({model_name}) 2. denemede yanıt verdi", "INFO")
                        return response.text.strip()
                except Exception:
                    pass

                log("⚠️ Gemini 2. deneme de başarısız", "WARNING")
                return None
            else:
                log(f"⚠️ Gemini hatası: {first_err}", "WARNING")
                return None                                        # ← YENİ BLOK SONU

    except ImportError:
        log(
            "⚠️ google-genai paketi yüklü değil. "
            "pip install google-genai komutunu çalıştırın.",
            "WARNING",
        )
        return None

    except Exception as e:
        log(f"⚠️ Gemini hatası: {e}", "WARNING")
        return None


def _try_groq(prompt: str, temperature: float, max_tokens: int, model_name: str) -> Optional[str]:
    """
    Groq API ile metin üretir (yedek sağlayıcı 1).
    429 hatasında None döner (retry yapmaz çünkü günlük limit).   ← YENİ YORUM
    """
    api_key: str = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        log("ℹ️ GROQ_API_KEY bulunamadı, Groq atlanıyor", "INFO")
        return None

    try:
        from groq import Groq

        client = Groq(api_key=api_key)

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if response and response.choices:
            text: str = response.choices[0].message.content
            if text:
                log(f"✅ Groq ({model_name}) yanıt verdi", "INFO")
                return text.strip()

        log("⚠️ Groq boş yanıt döndü", "WARNING")
        return None

    except ImportError:
        log("⚠️ groq paketi yüklü değil", "WARNING")
        return None

    except Exception as e:
        log(f"⚠️ Groq hatası: {e}", "WARNING")
        return None


# ──────────────────────────────────────────────  ← YENİ BÖLÜM BAŞI
# OpenRouter API (yedek sağlayıcı 2)
# ──────────────────────────────────────────────

def _try_openrouter(prompt: str, temperature: float, max_tokens: int) -> Optional[str]:
    """
    OpenRouter API ile ücretsiz model çağrısı (yedek sağlayıcı 2).

    Ücretsiz model: meta-llama/llama-3.1-8b-instruct:free
    Limit: Cömert (günlük binlerce istek).

    Args:
        prompt:      YZ'ye gönderilecek metin.
        temperature: Yaratıcılık seviyesi.
        max_tokens:  Maksimum çıktı token sayısı.

    Returns:
        YZ yanıtı string veya None.
    """
    api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        log("ℹ️ OPENROUTER_API_KEY bulunamadı, OpenRouter atlanıyor", "INFO")
        return None

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
                "model": "meta-llama/llama-3.1-8b-instruct:free",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=60,
        )

        if response.status_code == 200:
            data = response.json()
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "").strip()
                if text:
                    log("✅ OpenRouter yanıt verdi", "INFO")
                    return text

            log("⚠️ OpenRouter boş yanıt döndü", "WARNING")
            return None

        elif response.status_code == 429:
            log("⚠️ OpenRouter rate limit", "WARNING")
            return None

        else:
            log(f"⚠️ OpenRouter hata: {response.status_code} — {response.text[:200]}", "WARNING")
            return None

    except Exception as e:
        log(f"⚠️ OpenRouter exception: {e}", "WARNING")
        return None
                                                                   # ← YENİ BÖLÜM SONU


def _try_huggingface(prompt: str, temperature: float, max_tokens: int) -> Optional[str]:
    """
    HuggingFace Inference API ile metin üretir (yedek sağlayıcı 3).
    """
    api_key: str = os.environ.get("HF_API_KEY", "")
    if not api_key:
        log("ℹ️ HF_API_KEY bulunamadı, HuggingFace atlanıyor", "INFO")
        return None

    try:
        from huggingface_hub import InferenceClient

        client = InferenceClient(token=api_key)

        response = client.chat_completion(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="mistralai/Mixtral-8x7B-Instruct-v0.1",
            max_tokens=max_tokens,
            temperature=temperature,
        )

        if response and response.choices:
            text: str = response.choices[0].message.content
            if text:
                log("✅ HuggingFace yanıt verdi", "INFO")
                return text.strip()

        log("⚠️ HuggingFace boş yanıt döndü", "WARNING")
        return None

    except ImportError:
        log("⚠️ huggingface_hub paketi yüklü değil", "WARNING")
        return None

    except Exception as e:
        log(f"⚠️ HuggingFace hatası: {e}", "WARNING")
        return None


# ──────────────────────────────────────────────
# Ana YZ Fonksiyonları (public API)
# ──────────────────────────────────────────────

def ask_ai(prompt: str) -> str:
    """
    YZ'ye prompt gönderir ve yanıt alır.

    Sağlayıcıları sırayla dener: Gemini → Groq → OpenRouter → HuggingFace.  ← DEĞİŞTİ
    İlk başarılı yanıtı döndürür.

    Args:
        prompt: YZ'ye gönderilecek metin.

    Returns:
        YZ yanıtı string. Hiçbir sağlayıcı başarılı olmazsa boş string.
    """
    settings: dict = load_config("settings")
    ai_settings: dict = settings.get("ai", {})

    temperature: float = ai_settings.get("temperature", 0.7)
    max_tokens: int = ai_settings.get("max_output_tokens", 2048)
    gemini_model: str = ai_settings.get("gemini_model", "gemini-2.0-flash")
    groq_model: str = ai_settings.get("groq_model", "llama-3.3-70b-versatile")

    prompt_preview: str = prompt[:100].replace("\n", " ")
    log(f"🤖 YZ'ye gönderiliyor: {prompt_preview}...", "INFO")

    # ── Sağlayıcı 1: Gemini ──
    result: Optional[str] = _try_gemini(prompt, temperature, max_tokens, gemini_model)
    if result:
        return result

    # ── Sağlayıcı 2: Groq ──
    log("🔄 Gemini başarısız, Groq deneniyor...", "INFO")
    result = _try_groq(prompt, temperature, max_tokens, groq_model)
    if result:
        return result

    # ── Sağlayıcı 3: OpenRouter ──                               ← YENİ
    log("🔄 Groq başarısız, OpenRouter deneniyor...", "INFO")
    result = _try_openrouter(prompt, temperature, max_tokens)
    if result:
        return result

    # ── Sağlayıcı 4: HuggingFace ──                              ← NUMARA DEĞİŞTİ
    log("🔄 OpenRouter başarısız, HuggingFace deneniyor...", "INFO")
    result = _try_huggingface(prompt, temperature, max_tokens)
    if result:
        return result

    # ── Hiçbiri başarılı olmadı ──
    log("❌ Hiçbir YZ sağlayıcısı yanıt veremedi!", "ERROR")
    return ""


# ──────────────────────────────────────────────
# JSON Parse — Kesik JSON Kurtarma
# ──────────────────────────────────────────────

def _fix_truncated_json_array(text: str) -> Optional[str]:
    """
    Token limiti dolunca yarım kalan JSON array'i düzeltmeye çalışır.

    Strateji:
      1. Son tam '}' karakterini bul
      2. Sonrasını kes (yarım kalan son element)
      3. Trailing virgülü temizle
      4. ']' ekle
      5. JSON olarak parse et
      6. Parse başarısızsa bir önceki '}' ile tekrar dene (max 20 deneme)

    Args:
        text: '[' ile başlayan ama ']' ile bitmeyen JSON string.

    Returns:
        Düzeltilmiş JSON string veya None.
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

    search_end: int = len(text)

    for _attempt in range(20):
        close_pos: int = text.rfind("}", 0, search_end)
        if close_pos <= 0:
            break

        candidate: str = text[: close_pos + 1].rstrip()

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
    """
    YZ yanıtından JSON verisini çıkarır ve parse eder.

    7 aşamalı parse stratejisi (sırasıyla denenir):
      1. Düz JSON parse
      2. Markdown code block içinden çıkar
      3. Trailing comma temizle ve tekrar dene
      4. Regex ile [ ... ] array bul
      5. Kesik JSON kurtarma (token limiti dolmuş yanıtlar)
      6. Regex ile { ... } tek object bul
      7. Satır satır JSON parse (JSON Lines format)

    Args:
        text: YZ'den gelen ham yanıt metni.

    Returns:
        Parse edilmiş JSON (list veya dict). Parse edilemezse None.
    """
    if not text:
        log("⚠️ parse_ai_json: Boş metin", "WARNING")
        return None

    cleaned: str = text.strip()

    # ── Deneme 1: Düz JSON parse ──
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # ── Deneme 2: Markdown code block içinden çıkar ──
    code_block_pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    code_match = re.search(code_block_pattern, cleaned, re.DOTALL)
    if code_match:
        block_content: str = code_match.group(1).strip()
        try:
            return json.loads(block_content)
        except json.JSONDecodeError:
            cleaned = block_content

    # ── Deneme 3: Trailing comma temizle ──
    fixed: str = re.sub(r",\s*([}\]])", r"\1", cleaned)
    if fixed != cleaned:
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # ── Deneme 4: Regex ile [ ... ] array bul ──
    array_match = re.search(r"\[.*\]", fixed, re.DOTALL)
    if array_match:
        try:
            return json.loads(array_match.group(0))
        except json.JSONDecodeError:
            pass

    # ── Deneme 5: Kesik JSON kurtarma ──
    bracket_pos: int = cleaned.find("[")
    if bracket_pos >= 0:
        partial: str = cleaned[bracket_pos:]
        recovered: Optional[str] = _fix_truncated_json_array(partial)
        if recovered is not None:
            try:
                result = json.loads(recovered)
                count: int = len(result) if isinstance(result, list) else 1
                log(
                    f"🔧 Kesik JSON kurtarıldı: {count} öğe recover edildi",
                    "INFO",
                )
                return result
            except json.JSONDecodeError:
                pass

    # ── Deneme 6: Tek { ... } object bul ──
    object_match = re.search(r"\{[^{}]*\}", cleaned)
    if object_match:
        try:
            result = json.loads(object_match.group(0))
            if isinstance(result, dict):
                return [result]
            return result
        except json.JSONDecodeError:
            pass

    # ── Deneme 7: Satır satır JSON parse (JSON Lines) ──
    lines: list[str] = cleaned.split("\n")
    line_results: list[dict] = []
    for line in lines:
        line = line.strip().rstrip(",")
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    line_results.append(obj)
            except json.JSONDecodeError:
                pass

    if line_results:
        log(
            f"🔧 Satır satır JSON parse: {len(line_results)} öğe bulundu",
            "INFO",
        )
        return line_results

    # ── Hiçbiri başarılı olmadı ──
    preview: str = cleaned[:200]
    log(
        f"⚠️ parse_ai_json: JSON parse edilemedi. İlk 200 karakter: {preview}",
        "WARNING",
    )
    return None


def generate_post_text(article: dict) -> str:
    """
    Haber bilgilerinden Facebook post metni üretir.

    Üretilen metin otomatik olarak karakter temizlemesinden geçer:
    Korece, Japonca, Çince vb. karakterler silinir.

    Args:
        article: Haber dict'i (title, summary, full_text, source_name vb.).

    Returns:
        Facebook'ta paylaşılacak post metni. Üretilemezse boş string.
    """
    prompts_config: dict = load_config("prompts")
    writer_prompt: str = prompts_config.get("post_writer", "")

    if not writer_prompt:
        log("⚠️ post_writer promptu prompts.json'da bulunamadı", "WARNING")
        return ""

    title: str = article.get("title", "")
    summary: str = article.get("summary", "")
    full_text: str = article.get("full_text", "")
    source: str = article.get("source_name", "")

    news_info_parts: list[str] = []
    news_info_parts.append(f"BAŞLIK: {title}")

    if source:
        news_info_parts.append(f"KAYNAK: {source}")

    if summary:
        news_info_parts.append(f"ÖZET: {summary[:500]}")

    if full_text:
        news_info_parts.append(f"TAM METİN: {full_text[:1500]}")

    news_info: str = "\n".join(news_info_parts)
    full_prompt: str = f"{writer_prompt}\n\n{news_info}"

    log(f"✍️ Post metni üretiliyor: {title[:80]}...", "INFO")

    post_text: str = ask_ai(full_prompt)

    if not post_text:
        log("❌ Post metni üretilemedi", "ERROR")
        return ""

    # ── Temel temizlik ──
    post_text = post_text.strip().strip('"').strip("'")

    # ── Yabancı alfabe temizliği (Korece, Japonca, Çince vb.) ──
    original_len: int = len(post_text)
    post_text = _clean_non_turkish_chars(post_text)
    cleaned_len: int = len(post_text)

    if cleaned_len < original_len:
        removed_count: int = original_len - cleaned_len
        log(
            f"🧹 Yabancı karakter temizlendi: {removed_count} karakter silindi",
            "INFO",
        )

    if len(post_text) < 30:
        log(f"⚠️ Üretilen metin çok kısa ({len(post_text)} karakter)", "WARNING")
        return ""

    log(f"✅ Post metni hazır ({len(post_text)} karakter)", "INFO")
    return post_text


def generate_image_prompt(title: str, summary: str) -> str:
    """
    Haber başlığı ve özetinden İngilizce görsel üretim promptu oluşturur.

    Üretilen prompt otomatik olarak karakter temizlemesinden geçer.

    Args:
        title:   Haber başlığı (Türkçe).
        summary: Haber özeti (Türkçe).

    Returns:
        İngilizce görsel üretim promptu. Üretilemezse fallback prompt döner.
    """
    prompts_config: dict = load_config("prompts")
    image_prompt_template: str = prompts_config.get("image_prompt_generator", "")

    if not image_prompt_template:
        log("⚠️ image_prompt_generator promptu bulunamadı", "WARNING")
        return f"Professional automotive photography, {title[:50]}, cinematic lighting, 4k"

    news_text: str = f"Başlık: {title}"
    if summary:
        news_text += f"\nÖzet: {summary[:300]}"

    full_prompt: str = f"{image_prompt_template}\n\n{news_text}"

    log("🎨 Görsel promptu üretiliyor...", "INFO")

    image_prompt: str = ask_ai(full_prompt)

    if not image_prompt:
        log("⚠️ Görsel promptu üretilemedi, varsayılan kullanılıyor", "WARNING")
        return "Professional automotive photography, modern car, cinematic lighting, 4k"

    image_prompt = image_prompt.strip().strip('"').strip("'")

    # ── Yabancı alfabe temizliği ──
    image_prompt = _clean_non_turkish_chars(image_prompt)

    if len(image_prompt) > 200:
        image_prompt = image_prompt[:200]

    log(f"✅ Görsel promptu: {image_prompt[:100]}...", "INFO")
    return image_prompt
