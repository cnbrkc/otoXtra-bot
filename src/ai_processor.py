"""
ai_processor.py — Yapay Zeka Metin İşleme Modülü

Bu modül tüm YZ (yapay zeka) işlemlerini yönetir:
  - Haber değerlendirme için YZ'ye soru sorma
  - Facebook post metni üretme
  - Görsel üretim promptu oluşturma

YZ Sağlayıcı Zinciri (fallback):
  1. Google Gemini (ana)     → GEMINI_API_KEY
  2. Groq (yedek 1)         → GROQ_API_KEY
  3. HuggingFace (yedek 2)  → HF_API_KEY

Eğer bir sağlayıcı başarısız olursa otomatik olarak sonrakine geçer.

Kullandığı modüller:
  - utils.py → load_config(), log(), get_project_root()

Ortam değişkenleri:
  - GEMINI_API_KEY  (Google AI Studio'dan alınır)
  - GROQ_API_KEY    (console.groq.com'dan alınır)
  - HF_API_KEY      (huggingface.co'dan alınır)

NOT: google-genai paketi kullanılıyor (google-generativeai DEĞİL).
     pip install google-genai
"""

import json
import os
import re
from typing import Optional, Union

from utils import load_config, log


# ──────────────────────────────────────────────
# YZ Sağlayıcıları
# ──────────────────────────────────────────────

def _try_gemini(prompt: str, temperature: float, max_tokens: int, model_name: str) -> Optional[str]:
    """
    Google Gemini API ile metin üretir.

    Yeni google-genai paketi kullanılır (eski google-generativeai DEĞİL).

    Args:
        prompt:      YZ'ye gönderilecek metin.
        temperature: Yaratıcılık seviyesi (0.0 - 1.0).
        max_tokens:  Maksimum çıktı token sayısı.
        model_name:  Kullanılacak Gemini modeli.

    Returns:
        YZ yanıtı string veya None.
    """
    api_key: str = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        log("ℹ️ GEMINI_API_KEY bulunamadı, Gemini atlanıyor", "INFO")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

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

    Args:
        prompt:      YZ'ye gönderilecek metin.
        temperature: Yaratıcılık seviyesi.
        max_tokens:  Maksimum çıktı token sayısı.
        model_name:  Kullanılacak Groq modeli.

    Returns:
        YZ yanıtı string veya None.
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


def _try_huggingface(prompt: str, temperature: float, max_tokens: int) -> Optional[str]:
    """
    HuggingFace Inference API ile metin üretir (yedek sağlayıcı 2).

    Args:
        prompt:      YZ'ye gönderilecek metin.
        temperature: Yaratıcılık seviyesi.
        max_tokens:  Maksimum çıktı token sayısı.

    Returns:
        YZ yanıtı string veya None.
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

    Sağlayıcıları sırayla dener: Gemini → Groq → HuggingFace.
    İlk başarılı yanıtı döndürür.

    Bu fonksiyon content_filter.py tarafından kalite kapısı
    ve viral puanlama için çağrılır.

    Args:
        prompt: YZ'ye gönderilecek metin.

    Returns:
        YZ yanıtı string. Hiçbir sağlayıcı başarılı olmazsa boş string.
    """
    # Ayarları oku
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

    # ── Sağlayıcı 3: HuggingFace ──
    log("🔄 Groq başarısız, HuggingFace deneniyor...", "INFO")
    result = _try_huggingface(prompt, temperature, max_tokens)
    if result:
        return result

    # ── Hiçbiri başarılı olmadı ──
    log("❌ Hiçbir YZ sağlayıcısı yanıt veremedi!", "ERROR")
    return ""


def parse_ai_json(text: str) -> Optional[Union[list, dict]]:
    """
    YZ yanıtından JSON verisini çıkarır ve parse eder.

    YZ bazen JSON'u markdown code block içinde döner:
      ```json
      [{"key": "value"}]
      ```

    Bu fonksiyon tüm bu formatları destekler:
    1. Düz JSON
    2. Markdown code block içinde JSON
    3. Metin içinde gömülü JSON (ilk [ veya { ile başlayan kısım)

    Args:
        text: YZ'den gelen ham yanıt metni.

    Returns:
        Parse edilmiş JSON (list veya dict). Parse edilemezse None.
    """
    if not text:
        log("⚠️ parse_ai_json: Boş metin", "WARNING")
        return None

    # Temizlik
    cleaned: str = text.strip()

    # ── Deneme 1: Direkt JSON parse ──
    try:
        result = json.loads(cleaned)
        return result
    except json.JSONDecodeError:
        pass

    # ── Deneme 2: Markdown code block içinden çıkar ──
    # ```json ... ``` veya ``` ... ```
    code_block_pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    code_match = re.search(code_block_pattern, cleaned, re.DOTALL)
    if code_match:
        try:
            result = json.loads(code_match.group(1).strip())
            return result
        except json.JSONDecodeError:
            pass

    # ── Deneme 3: İlk [ ... ] veya { ... } bloğunu bul ──
    # JSON array
    array_match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if array_match:
        try:
            result = json.loads(array_match.group(0))
            return result
        except json.JSONDecodeError:
            pass

    # JSON object
    object_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if object_match:
        try:
            result = json.loads(object_match.group(0))
            # Eğer tek bir dict ise ve list bekleniyorsa, listeye çevir
            if isinstance(result, dict):
                return [result]
            return result
        except json.JSONDecodeError:
            pass

    log(f"⚠️ parse_ai_json: JSON parse edilemedi. İlk 200 karakter: {cleaned[:200]}", "WARNING")
    return None


def generate_post_text(article: dict) -> str:
    """
    Haber bilgilerinden Facebook post metni üretir.

    prompts.json'daki "post_writer" promptunu kullanır.
    Haberin başlığı, özeti ve (varsa) tam metnini YZ'ye gönderir.

    Args:
        article: Haber dict'i (title, summary, full_text, source_name vb.).

    Returns:
        Facebook'ta paylaşılacak post metni. Üretilemezse boş string.
    """
    # Promptu oku
    prompts_config: dict = load_config("prompts")
    writer_prompt: str = prompts_config.get("post_writer", "")

    if not writer_prompt:
        log("⚠️ post_writer promptu prompts.json'da bulunamadı", "WARNING")
        return ""

    # Haber bilgilerini hazırla
    title: str = article.get("title", "")
    summary: str = article.get("summary", "")
    full_text: str = article.get("full_text", "")
    source: str = article.get("source_name", "")

    # Haber bilgilerini prompt'a ekle
    news_info_parts: list[str] = []
    news_info_parts.append(f"BAŞLIK: {title}")

    if source:
        news_info_parts.append(f"KAYNAK: {source}")

    if summary:
        news_info_parts.append(f"ÖZET: {summary[:500]}")

    if full_text:
        # Tam metni kısalt (YZ token limitini aşmasın)
        news_info_parts.append(f"TAM METİN: {full_text[:1500]}")

    news_info: str = "\n".join(news_info_parts)
    full_prompt: str = f"{writer_prompt}\n\n{news_info}"

    log(f"✍️ Post metni üretiliyor: {title[:80]}...", "INFO")

    # YZ'ye gönder
    post_text: str = ask_ai(full_prompt)

    if not post_text:
        log("❌ Post metni üretilemedi", "ERROR")
        return ""

    # Temizlik: YZ bazen tırnak veya ekstra boşluk ekler
    post_text = post_text.strip().strip('"').strip("'")

    # Çok kısa mı kontrol et
    if len(post_text) < 30:
        log(f"⚠️ Üretilen metin çok kısa ({len(post_text)} karakter)", "WARNING")
        return ""

    log(f"✅ Post metni hazır ({len(post_text)} karakter)", "INFO")
    return post_text


def generate_image_prompt(title: str, summary: str) -> str:
    """
    Haber başlığı ve özetinden İngilizce görsel üretim promptu oluşturur.

    Pollinations.ai ve HuggingFace görsel üretim servisleri İngilizce
    prompt ile daha iyi çalışır.

    Args:
        title:   Haber başlığı (Türkçe).
        summary: Haber özeti (Türkçe).

    Returns:
        İngilizce görsel üretim promptu. Üretilemezse boş string.
    """
    # Promptu oku
    prompts_config: dict = load_config("prompts")
    image_prompt_template: str = prompts_config.get("image_prompt_generator", "")

    if not image_prompt_template:
        log("⚠️ image_prompt_generator promptu bulunamadı", "WARNING")
        # Basit fallback prompt oluştur
        return f"Professional automotive photography, {title[:50]}, cinematic lighting, 4k"

    # Haber bilgisini ekle
    news_text: str = f"Başlık: {title}"
    if summary:
        news_text += f"\nÖzet: {summary[:300]}"

    full_prompt: str = f"{image_prompt_template}\n\n{news_text}"

    log("🎨 Görsel promptu üretiliyor...", "INFO")

    # YZ'ye gönder
    image_prompt: str = ask_ai(full_prompt)

    if not image_prompt:
        log("⚠️ Görsel promptu üretilemedi, varsayılan kullanılıyor", "WARNING")
        return f"Professional automotive photography, modern car, cinematic lighting, 4k"

    # Temizlik
    image_prompt = image_prompt.strip().strip('"').strip("'")

    # Çok uzunsa kısalt (görsel API'ler uzun promptları sevmez)
    if len(image_prompt) > 200:
        image_prompt = image_prompt[:200]

    log(f"✅ Görsel promptu: {image_prompt[:100]}...", "INFO")
    return image_prompt
