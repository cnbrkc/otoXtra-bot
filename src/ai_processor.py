"""
src/ai_processor.py — YZ API Yönetimi

otoXtra Facebook Botu için tüm yapay zeka API çağrılarını yönetir.
Metin üretimi için 3 provider zinciri vardır:
    Gemini (ana) → Groq (yedek1) → HuggingFace (yedek2)

Geçiş mantığı:
  - Rate limit hatası → HEMEN yedek provider'a geç
  - Diğer hatalar → 1 kez tekrar dene → sonra yedek'e geç
  - Tüm provider'lar başarısızsa → boş string döner

İçerdiği fonksiyonlar:
  - call_gemini()          : Google Gemini API çağrısı
  - call_groq()            : Groq API çağrısı
  - call_huggingface()     : HuggingFace Inference API çağrısı
  - ask_ai()               : Ana fonksiyon — provider zincirini yönetir
  - generate_post_text()   : Haber → Facebook post metni üretir
  - generate_image_prompt(): Haber → görsel üretim promptu üretir
  - parse_ai_json()        : YZ yanıtındaki JSON'u parse eder

Kullandığı config dosyaları:
  - config/settings.json  → ai_providers ayarları (model adları, sıra)
  - config/prompts.json   → prompt şablonları

Ortam değişkenleri (GitHub Secrets'tan gelir):
  - GEMINI_API_KEY
  - GROQ_API_KEY
  - HF_API_KEY

Diğer modüller bu dosyayı şöyle import eder:
    from ai_processor import ask_ai, generate_post_text, parse_ai_json

YANLIŞ kullanım (YAPMA):
    from src.ai_processor import ask_ai
"""

import os
import json
import re
import random
import time

from utils import load_config, log


# ════════════════════════════════════════════════════════════
# KOŞULLU KÜTÜPHANE IMPORT'LARI
# Bu kütüphaneler yüklü değilse ilgili provider devre dışı kalır.
# Hata değil — sadece o provider kullanılamaz.
# ════════════════════════════════════════════════════════════

_GEMINI_AVAILABLE: bool = False
try:
    import google.generativeai as genai
    _GEMINI_AVAILABLE = True
except ImportError:
    pass

_GROQ_AVAILABLE: bool = False
try:
    from groq import Groq as _GroqClient
    _GROQ_AVAILABLE = True
except ImportError:
    pass

_HF_AVAILABLE: bool = False
try:
    from huggingface_hub import InferenceClient as _HFClient
    _HF_AVAILABLE = True
except ImportError:
    pass


# ════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ════════════════════════════════════════════════════════════

def _is_rate_limit_error(error: Exception) -> bool:
    """Hatanın rate limit / kota hatası olup olmadığını kontrol eder.

    HTTP 429, quota exceeded, too many requests gibi hata
    mesajlarını yakalar.

    Args:
        error: Yakalanan exception nesnesi.

    Returns:
        bool: Rate limit hatasıysa True.
    """
    error_str = str(error).lower()
    rate_indicators = [
        "429",
        "rate limit",
        "rate_limit",
        "ratelimit",
        "quota",
        "too many requests",
        "resource_exhausted",
        "resource exhausted",
        "tokens per minute",
        "requests per minute",
    ]
    return any(indicator in error_str for indicator in rate_indicators)


def _get_ai_settings() -> dict:
    """settings.json'dan ai_providers bölümünü okur.

    Returns:
        dict: AI provider ayarları. Boşsa varsayılan değerler kullanılır.
    """
    settings = load_config("settings")
    return settings.get("ai_providers", {})


# ════════════════════════════════════════════════════════════
# 1. GEMINI API
# ════════════════════════════════════════════════════════════

def call_gemini(prompt: str, _retry: bool = True) -> str | None:
    """Google Gemini API ile metin üretir.

    Model: gemini-2.0-flash (varsayılan), bulunamazsa gemini-1.5-flash.
    Rate limit: 15 RPM (ücretsiz plan).
    Timeout: 30 saniye.

    Args:
        prompt: YZ'ye gönderilecek prompt metni.
        _retry: Hata durumunda tekrar deneme hakkı (dahili kullanım).

    Returns:
        str | None: Üretilen metin. Başarısızsa None.
    """
    if not _GEMINI_AVAILABLE:
        log("Gemini: google-generativeai kütüphanesi yüklü değil", "WARNING")
        return None

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        log("Gemini: GEMINI_API_KEY ortam değişkeni bulunamadı", "WARNING")
        return None

    # Ayarlardan model adını oku
    ai_cfg = _get_ai_settings()
    primary_model = ai_cfg.get("gemini_model", "gemini-2.0-flash")
    fallback_model = "gemini-1.5-flash"

    # Denenecek modeller (birincil → yedek)
    models_to_try = [primary_model]
    if primary_model != fallback_model:
        models_to_try.append(fallback_model)

    # API key'i yapılandır
    genai.configure(api_key=api_key)

    for current_model in models_to_try:
        try:
            model = genai.GenerativeModel(current_model)
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=2048
                ),
                request_options={"timeout": 30},
            )

            # Yanıt kontrolü
            if response and response.text:
                text = response.text.strip()
                if text:
                    log(
                        f"Gemini ({current_model}) başarılı "
                        f"— {len(text)} karakter"
                    )
                    return text

            log(f"Gemini ({current_model}) boş yanıt döndü", "WARNING")

        except Exception as exc:
            # Rate limit → hemen None döner, yedek provider'a geçilir
            if _is_rate_limit_error(exc):
                log(
                    f"Gemini rate limit hatası — "
                    f"hemen yedek provider'a geçiliyor",
                    "WARNING",
                )
                return None

            error_str = str(exc).lower()

            # Model bulunamadı → sonraki modeli dene
            if "not found" in error_str or "404" in error_str:
                log(
                    f"Gemini model bulunamadı: {current_model}, "
                    f"diğer model deneniyor...",
                    "WARNING",
                )
                continue

            # Diğer hata → 1 kez tekrar dene
            log(f"Gemini ({current_model}) hatası: {exc}", "ERROR")

            if _retry:
                log("Gemini 1 kez daha deneniyor...", "WARNING")
                time.sleep(2)
                return call_gemini(prompt, _retry=False)

            return None

    return None


# ════════════════════════════════════════════════════════════
# 2. GROQ API
# ════════════════════════════════════════════════════════════

def call_groq(prompt: str, _retry: bool = True) -> str | None:
    """Groq API ile metin üretir.

    Model: llama-3.3-70b-versatile.
    Rate limit: 30 RPM (ücretsiz plan).
    Timeout: 30 saniye.
    chat.completions.create kullanır.

    Args:
        prompt: YZ'ye gönderilecek prompt metni.
        _retry: Hata durumunda tekrar deneme hakkı (dahili kullanım).

    Returns:
        str | None: Üretilen metin. Başarısızsa None.
    """
    if not _GROQ_AVAILABLE:
        log("Groq: groq kütüphanesi yüklü değil", "WARNING")
        return None

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        log("Groq: GROQ_API_KEY ortam değişkeni bulunamadı", "WARNING")
        return None

    ai_cfg = _get_ai_settings()
    model_name = ai_cfg.get("groq_model", "llama-3.3-70b-versatile")

    try:
        client = _GroqClient(api_key=api_key, timeout=30.0)

        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
            max_tokens=2048,
        )

        if response and response.choices:
            text = response.choices[0].message.content
            if text and text.strip():
                text = text.strip()
                log(
                    f"Groq ({model_name}) başarılı "
                    f"— {len(text)} karakter"
                )
                return text

        log(f"Groq ({model_name}) boş yanıt döndü", "WARNING")
        return None

    except Exception as exc:
        # Rate limit → hemen yedek provider'a geç
        if _is_rate_limit_error(exc):
            log(
                "Groq rate limit hatası — "
                "hemen yedek provider'a geçiliyor",
                "WARNING",
            )
            return None

        log(f"Groq ({model_name}) hatası: {exc}", "ERROR")

        # Diğer hata → 1 kez tekrar dene
        if _retry:
            log("Groq 1 kez daha deneniyor...", "WARNING")
            time.sleep(2)
            return call_groq(prompt, _retry=False)

        return None


# ════════════════════════════════════════════════════════════
# 3. HUGGINGFACE INFERENCE API
# ════════════════════════════════════════════════════════════

def call_huggingface(prompt: str, _retry: bool = True) -> str | None:
    """HuggingFace Inference API ile metin üretir.

    Model: mistralai/Mistral-7B-Instruct-v0.3.
    İki yöntem sırayla denenir:
      1. text_generation (eski API)
      2. chat_completion (yeni API)
    Timeout: 30 saniye.

    Args:
        prompt: YZ'ye gönderilecek prompt metni.
        _retry: Hata durumunda tekrar deneme hakkı (dahili kullanım).

    Returns:
        str | None: Üretilen metin. Başarısızsa None.
    """
    if not _HF_AVAILABLE:
        log(
            "HuggingFace: huggingface_hub kütüphanesi yüklü değil",
            "WARNING",
        )
        return None

    api_key = os.environ.get("HF_API_KEY", "")
    if not api_key:
        log("HuggingFace: HF_API_KEY ortam değişkeni bulunamadı", "WARNING")
        return None

    ai_cfg = _get_ai_settings()
    model_name = ai_cfg.get(
        "huggingface_model", "mistralai/Mistral-7B-Instruct-v0.3"
    )

    try:
        client = _HFClient(token=api_key, timeout=30)
    except Exception as exc:
        log(f"HuggingFace client oluşturma hatası: {exc}", "ERROR")
        return None

    # ── YÖNTEM 1: text_generation ──
    try:
        result = client.text_generation(
            prompt,
            model=model_name,
            max_new_tokens=1500,
        )
        if result and result.strip():
            text = result.strip()
            log(
                f"HuggingFace text_generation ({model_name}) başarılı "
                f"— {len(text)} karakter"
            )
            return text

    except Exception as exc1:
        if _is_rate_limit_error(exc1):
            log(
                "HuggingFace rate limit hatası — "
                "hemen yedek provider'a geçiliyor",
                "WARNING",
            )
            return None
        log(
            f"HuggingFace text_generation hatası: {exc1} "
            f"— chat_completion deneniyor",
            "WARNING",
        )

    # ── YÖNTEM 2: chat_completion (yeni API) ──
    try:
        response = client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
            max_tokens=1500,
        )
        if response and response.choices:
            text = response.choices[0].message.content
            if text and text.strip():
                text = text.strip()
                log(
                    f"HuggingFace chat_completion ({model_name}) başarılı "
                    f"— {len(text)} karakter"
                )
                return text

        log(f"HuggingFace ({model_name}) boş yanıt döndü", "WARNING")

    except Exception as exc2:
        if _is_rate_limit_error(exc2):
            log(
                "HuggingFace rate limit hatası — "
                "hemen yedek provider'a geçiliyor",
                "WARNING",
            )
            return None
        log(f"HuggingFace chat_completion hatası: {exc2}", "ERROR")

    # Her iki yöntem de başarısız → 1 kez tekrar dene
    if _retry:
        log("HuggingFace 1 kez daha deneniyor...", "WARNING")
        time.sleep(2)
        return call_huggingface(prompt, _retry=False)

    return None


# ════════════════════════════════════════════════════════════
# 4. ANA FONKSİYON — PROVIDER ZİNCİRİ
# ════════════════════════════════════════════════════════════

def ask_ai(prompt: str, provider_type: str = "text") -> str:
    """Provider zincirini yöneterek YZ'den metin üretir.

    settings.json'daki ai_providers.text_generation sırasına göre
    provider'ları dener. İlk başarılı yanıtı döner.

    Zincir: primary → fallback_1 → fallback_2
    Varsayılan: gemini → groq → huggingface

    Args:
        prompt: YZ'ye gönderilecek prompt metni.
        provider_type: Provider tipi (şu an sadece "text" desteklenir).

    Returns:
        str: Üretilen metin. Tüm provider'lar başarısızsa boş string "".
    """
    ai_cfg = _get_ai_settings()
    chain_config = ai_cfg.get("text_generation", {})

    # Provider sırasını oluştur
    chain: list[str] = []
    for key in ["primary", "fallback_1", "fallback_2"]:
        provider = chain_config.get(key, "")
        if provider:
            chain.append(provider)

    # Config'te yoksa varsayılan zincir
    if not chain:
        chain = ["gemini", "groq", "huggingface"]

    # Provider adı → fonksiyon eşlemesi
    provider_map: dict = {
        "gemini": call_gemini,
        "groq": call_groq,
        "huggingface": call_huggingface,
    }

    log(f"YZ provider zinciri: {' → '.join(chain)}")

    for i, provider_name in enumerate(chain):
        func = provider_map.get(provider_name)
        if not func:
            log(f"Bilinmeyen YZ provider: {provider_name}", "WARNING")
            continue

        log(f"YZ provider deneniyor: {provider_name}")
        result = func(prompt)

        # Başarılı yanıt
        if result and result.strip():
            return result.strip()

        # Başarısız → sonraki provider bilgisi
        if i < len(chain) - 1:
            log(
                f"{provider_name} başarısız, "
                f"{chain[i + 1]} deneniyor...",
                "WARNING",
            )
        else:
            log(f"{provider_name} başarısız (son provider)", "WARNING")

    log("TÜM YZ PROVIDER'LAR BAŞARISIZ!", "ERROR")
    return ""


# ════════════════════════════════════════════════════════════
# 5. FACEBOOK POST METNİ ÜRET
# ════════════════════════════════════════════════════════════

def generate_post_text(article: dict) -> str:
    """Haber bilgilerinden Facebook post metni üretir.

    prompts.json'daki post_writer şablonunu kullanır.
    Rastgele bir yazım tarzı seçer (style_variations).
    İngilizce haberlerde Türkçe'ye çeviri notu ekler.

    Args:
        article: Haber bilgileri dict'i.
            Beklenen anahtarlar: title, summary, full_text (opsiyonel),
            language, category

    Returns:
        str: Facebook post metni. Üretilemezse boş string "".
    """
    prompts_cfg = load_config("prompts")
    writer_cfg = prompts_cfg.get("post_writer", {})

    if not writer_cfg:
        log("prompts.json'da post_writer bölümü bulunamadı", "ERROR")
        return ""

    system_prompt: str = writer_cfg.get("system", "")
    user_template: str = writer_cfg.get("user_template", "")
    style_variations: list = writer_cfg.get("style_variations", [])

    # ── Haber bilgilerini al ──
    title: str = article.get("title", "Başlık yok")
    summary: str = article.get("summary", "")
    full_text: str = article.get("full_text", "")
    language: str = article.get("language", "tr")
    category: str = article.get("category", "diger")

    # ── User template'i formatla ──
    try:
        user_text = user_template.format(
            title=title,
            summary=summary if summary else "Özet mevcut değil",
            category=category,
        )
    except (KeyError, IndexError, ValueError) as exc:
        log(f"Post writer template format hatası: {exc}", "WARNING")
        user_text = (
            f"Aşağıdaki haber için Facebook postu yaz.\n\n"
            f"BAŞLIK: {title}\n"
            f"ÖZET: {summary}\n"
            f"KATEGORİ: {category}\n\n"
            f"Sadece post metnini yaz, başka hiçbir şey ekleme."
        )

    # ── Tam metin varsa ekle (max 2000 karakter) ──
    if full_text:
        trimmed = full_text[:2000]
        user_text += f"\n\nHABER TAM METNİ:\n{trimmed}"

    # ── İngilizce haberse çeviri notu ekle ──
    if language and language.lower() == "en":
        user_text += (
            "\n\nNOT: Bu haber İngilizce. "
            "Tamamen Türkçe'ye çevirerek yaz."
        )

    # ── Rastgele yazım tarzı seç ──
    style_note = ""
    if style_variations:
        chosen_style = random.choice(style_variations)
        style_note = f"\n\nBU POST İÇİN YAZIM TARZI: {chosen_style}"

    # ── Final promptu birleştir ──
    full_prompt = f"{system_prompt}{style_note}\n\n{user_text}"

    # ── YZ'ye sor ──
    result = ask_ai(full_prompt)

    # Boşsa 1 kez daha dene
    if not result:
        log("Post metni boş geldi, 1 kez daha deneniyor...", "WARNING")
        result = ask_ai(full_prompt)

    if not result:
        log("Post metni üretilemedi", "ERROR")
        return ""

    # ── Sonuç kontrolü ──
    result = result.strip()

    # 15 satırdan uzunsa kısalt
    lines = result.split("\n")
    if len(lines) > 15:
        original_count = len(lines)
        result = "\n".join(lines[:15])
        log(
            f"Post metni 15 satıra kısaltıldı "
            f"(orijinal: {original_count} satır)"
        )

    log(f"Post metni üretildi — {len(result)} karakter")
    return result


# ════════════════════════════════════════════════════════════
# 6. GÖRSEL PROMPT'U ÜRET
# ════════════════════════════════════════════════════════════

def generate_image_prompt(article: dict) -> str:
    """Haber bilgilerinden İngilizce görsel üretim promptu oluşturur.

    prompts.json'daki image_prompt_generator şablonunu kullanır.
    YZ başarısız olursa fallback_prompts listesinden rastgele biri seçilir.

    Args:
        article: Haber bilgileri dict'i.
            Beklenen anahtarlar: title, summary, category

    Returns:
        str: İngilizce görsel üretim promptu. Üretilemezse boş string "".
    """
    prompts_cfg = load_config("prompts")
    img_cfg = prompts_cfg.get("image_prompt_generator", {})

    if not img_cfg:
        log("prompts.json'da image_prompt_generator bulunamadı", "WARNING")
        return ""

    system_prompt: str = img_cfg.get("system", "")
    user_template: str = img_cfg.get("user_template", "")
    fallback_prompts: list = img_cfg.get("fallback_prompts", [])

    # Haber bilgileri
    title: str = article.get("title", "")
    summary: str = article.get("summary", "")
    category: str = article.get("category", "diger")

    # User template'i formatla
    try:
        user_text = user_template.format(
            title=title,
            summary=summary if summary else "No summary available",
            category=category,
        )
    except (KeyError, IndexError, ValueError) as exc:
        log(f"Image prompt template format hatası: {exc}", "WARNING")
        user_text = (
            f"Write an English image generation prompt for this news:\n"
            f"TITLE: {title}\nSUMMARY: {summary}"
        )

    # Final promptu birleştir
    full_prompt = f"{system_prompt}\n\n{user_text}"

    # YZ'ye sor
    result = ask_ai(full_prompt)

    # Başarısızsa fallback prompt kullan
    if not result and fallback_prompts:
        result = random.choice(fallback_prompts)
        log(
            "Görsel promptu YZ ile üretilemedi, "
            "fallback prompt kullanılıyor"
        )

    if result:
        result = result.strip()
        log(f"Görsel promptu üretildi — {len(result)} karakter")

    return result if result else ""


# ════════════════════════════════════════════════════════════
# 7. YZ YANITINDAN JSON PARSE ET
# ════════════════════════════════════════════════════════════

def parse_ai_json(response: str) -> list | dict | None:
    """YZ yanıtındaki JSON verisini parse eder.

    YZ'ler bazen JSON'u markdown code block içinde döner.
    Bu fonksiyon şu formatları temizler:
      - ```json ... ```
      - ``` ... ```
      - Serbest metin içindeki JSON objesi/dizisi

    Args:
        response: YZ'den gelen ham yanıt metni.

    Returns:
        list | dict | None: Parse edilmiş JSON verisi.
            Parse edilemezse None.
    """
    if not response or not response.strip():
        log("parse_ai_json: Boş yanıt", "WARNING")
        return None

    cleaned = response.strip()

    # ── Adım 1: ```json ... ``` bloğunu çıkar ──
    json_block = re.search(
        r"```json\s*(.*?)\s*```", cleaned, re.DOTALL
    )
    if json_block:
        cleaned = json_block.group(1).strip()
    else:
        # ── Adım 2: ``` ... ``` bloğunu çıkar ──
        code_block = re.search(
            r"```\s*(.*?)\s*```", cleaned, re.DOTALL
        )
        if code_block:
            cleaned = code_block.group(1).strip()

    # ── Adım 3: Eğer hâlâ JSON'la başlamıyorsa, bul ──
    if not cleaned.startswith("{") and not cleaned.startswith("["):
        # JSON objesi ara
        obj_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if obj_match:
            cleaned = obj_match.group(1)
        else:
            # JSON dizisi ara
            arr_match = re.search(r"(\[.*\])", cleaned, re.DOTALL)
            if arr_match:
                cleaned = arr_match.group(1)

    # ── Adım 4: Parse et ──
    try:
        parsed = json.loads(cleaned)
        return parsed
    except json.JSONDecodeError as exc:
        log(f"YZ JSON parse hatası: {exc}", "WARNING")
        log(
            f"Parse edilemeyen yanıt (ilk 300 karakter): "
            f"{cleaned[:300]}",
            "WARNING",
        )
        return None


# ════════════════════════════════════════════════════════════
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log("=" * 55)
    log("ai_processor.py MODÜL TESTİ")
    log("=" * 55)

    # ── Kütüphane durumu ──
    log(f"Gemini kütüphanesi   : {'✓ Yüklü' if _GEMINI_AVAILABLE else '✗ Yüklü değil'}")
    log(f"Groq kütüphanesi     : {'✓ Yüklü' if _GROQ_AVAILABLE else '✗ Yüklü değil'}")
    log(f"HuggingFace kütüphanesi: {'✓ Yüklü' if _HF_AVAILABLE else '✗ Yüklü değil'}")

    # ── API key durumu ──
    log(f"GEMINI_API_KEY : {'✓ Var' if os.environ.get('GEMINI_API_KEY') else '✗ Yok'}")
    log(f"GROQ_API_KEY   : {'✓ Var' if os.environ.get('GROQ_API_KEY') else '✗ Yok'}")
    log(f"HF_API_KEY     : {'✓ Var' if os.environ.get('HF_API_KEY') else '✗ Yok'}")

    # ── JSON parse testi (API gerektirmez) ──
    log("\n--- JSON Parse Testi ---")

    test_cases = [
        ('Normal JSON', '{"score": 8, "valid": true}'),
        ('Code block', '```json\n{"score": 7}\n```'),
        ('Metin + JSON', 'İşte sonuç: {"score": 9, "ok": true} budur.'),
        ('Bozuk JSON', '{score: nope}'),
    ]

    for name, test_input in test_cases:
        result = parse_ai_json(test_input)
        status = "✓" if result is not None else "✗"
        log(f"  {status} {name}: {result}")

    # ── YZ çağrı testi (API key varsa) ──
    has_any_key = any([
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GROQ_API_KEY"),
        os.environ.get("HF_API_KEY"),
    ])

    if has_any_key:
        log("\n--- YZ Çağrı Testi ---")
        test_prompt = (
            "Sen bir test botusun. Sadece şu cümleyi yaz, "
            "başka hiçbir şey ekleme: 'Test başarılı!'"
        )
        result = ask_ai(test_prompt)
        if result:
            log(f"YZ yanıtı: {result[:200]}")
        else:
            log("YZ yanıtı alınamadı", "WARNING")

        log("\n--- Post Metni Testi ---")
        test_article = {
            "title": "Yeni Togg T10F İlk Kez Görüntülendi",
            "summary": "Togg'un ikinci modeli T10F, test sürüşleri "
                       "sırasında ilk kez görüntülendi.",
            "category": "yeni_model_tanitim",
            "language": "tr",
        }
        post_text = generate_post_text(test_article)
        if post_text:
            log(f"Üretilen post:\n{post_text}")
        else:
            log("Post metni üretilemedi", "WARNING")
    else:
        log(
            "\nYZ çağrı testi atlanıyor "
            "(hiçbir API key ayarlanmamış)",
            "WARNING",
        )

    log("\n" + "=" * 55)
    log("ai_processor.py MODÜL TESTİ TAMAMLANDI")
    log("=" * 55)
