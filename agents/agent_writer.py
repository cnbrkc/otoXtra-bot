"""
agents/agent_writer.py - Icerik yazma ajani (v5.3 - Turkce Karakter Zorunlulugu)

v5.3 FIXED:
  - Türkçe karakter (ç, ş, ğ, ü, ö, ı) kullanımı prompt ve fallback postlara işlendi.
  - YZ'nin ASCII karakter (c, s, g) kullanması engellendi.

v5.2 FIXED:
  - CRITICAL FIX: Facebook ve Threads uyumu icin maksimum karakter limiti 480'e dustu.
  - Threads'de kelime ortasinda kesilmeyi onlemek icin YZ promptu ve kalite kontrolu guncellendi.
  - Fallback post ozetleri otomatik olarak 330 karaktere kirpilarak toplam 480 limiti garanti edildi.

v5.1 ULTRA FIXED:
  - CRITICAL FIX: English text detection added (_detect_english_injection)
  - CRITICAL FIX: Prompt instruction filtering (wait, let's, format, etc.)
  - CRITICAL FIX: Turkish/English ratio validation (>20% English = reject)

v5.0 ULTRA:
  - Stage-aware AI calls: ask_ai(..., stage="writing")
"""

import re

from core.ai_client import ask_ai
from core.config_loader import load_config
from core.logger import log
from core.state_manager import get_stage, set_stage


_FORBIDDEN_SCRIPT_PATTERNS = [
    r"[\u2e80-\u9fff]",
    r"[\uac00-\ud7ff]",
    r"[\u0590-\u08ff]",
    r"[\u0400-\u052f]",
]

_STRIP_CHAR_RANGES = [
    r"[\u2e80-\u2eff]",
    r"[\u3000-\u303f]",
    r"[\u3040-\u309f]",
    r"[\u30a0-\u30ff]",
    r"[\u3100-\u312f]",
    r"[\u3130-\u318f]",
    r"[\u31a0-\u31bf]",
    r"[\u31f0-\u31ff]",
    r"[\u3200-\u32ff]",
    r"[\u3300-\u33ff]",
    r"[\u3400-\u4dbf]",
    r"[\u4e00-\u9fff]",
    r"[\uac00-\ud7af]",
    r"[\ud7b0-\ud7ff]",
    r"[\uf900-\ufaff]",
    r"[\u1100-\u11ff]",
    r"[\ua960-\ua97f]",
    r"[\u0590-\u05ff]",
    r"[\u0600-\u06ff]",
    r"[\u0750-\u077f]",
    r"[\ufb50-\ufdff]",
    r"[\ufe70-\ufeff]",
    r"[\u0400-\u04ff]",
    r"[\u0500-\u052f]",
    r"[\u0900-\u097f]",
    r"[\u0e00-\u0e7f]",
    r"[\u1000-\u109f]",
    r"[\u10a0-\u10ff]",
]

_FORBIDDEN_CTA = [
    "begenmeyi unutmayin",
    "paylasmayi unutmayin",
    "takip etmeyi unutmayin",
    "sayfamizi takip edin",
    "beğenmeyi unutmayın",
    "paylaşmayı unutmayın",
    "takip etmeyi unutmayın",
    "sayfamızı takip edin",
]

_HALLUCINATION_BAITS = [
    "iste o araclar",
    "iste liste",
    "detaylar soyle",
    "detaylar su sekilde",
    "işte o araçlar",
    "işte liste",
    "detaylar şöyle",
    "detaylar şu şekilde",
]

_ENGLISH_PROMPT_INSTRUCTIONS = [
    "wait",
    "let's",
    "format",
    "paragraph",
    "please",
    "rewrite",
    "body",
    "short",
    "natural",
    "look like",
    "gonna",
    "wanna",
    "should be",
    "make it",
    "write it",
]

_COMMON_ENGLISH_WORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "her", "was", "one",
    "our", "out", "day", "get", "has", "him", "his", "how", "man", "new", "now", "old",
    "see", "two", "way", "who", "boy", "did", "its", "let", "put", "say", "she", "too",
    "use", "wait", "please", "format", "paragraph", "rewrite", "body", "short", "natural",
    "look", "like", "gonna", "wanna", "should", "make", "write", "text", "post", "going",
}


def _clean_non_turkish_chars(text: str) -> str:
    if not text:
        return text

    cleaned = text
    for pattern in _STRIP_CHAR_RANGES:
        cleaned = re.sub(pattern, "", cleaned)

    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n\s*\n+", "\n\n", cleaned)
    lines = [line.rstrip() for line in cleaned.split("\n")]
    return "\n".join(lines).strip()


def _strip_wrapper_artifacts(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:text|markdown)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip().strip('"').strip("'").strip()


def _contains_forbidden_script(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in _FORBIDDEN_SCRIPT_PATTERNS)


def _detect_english_injection(post_text: str) -> tuple[bool, str]:
    if not post_text:
        return True, ""
    
    lower_text = post_text.lower()
    
    for instruction in _ENGLISH_PROMPT_INSTRUCTIONS:
        pattern = r'\b' + re.escape(instruction) + r'\b'
        if re.search(pattern, lower_text):
            log(f"[WRITER] ENGLISH INJECTION: Found prompt instruction '{instruction}'", "WARNING")
            return False, f"english_prompt_instruction:{instruction}"
    
    words = re.findall(r'\b[a-z]+\b', lower_text)
    if not words:
        return True, ""
    
    english_word_count = sum(1 for word in words if word in _COMMON_ENGLISH_WORDS)
    total_words = len(words)
    english_ratio = english_word_count / total_words if total_words > 0 else 0
    
    if english_ratio > 0.20:
        log(
            f"[WRITER] ENGLISH INJECTION: {english_word_count}/{total_words} words "
            f"({english_ratio*100:.1f}%) are English",
            "WARNING"
        )
        return False, f"english_ratio_high:{english_ratio*100:.0f}%"
    
    if re.search(r'\([^)]*\b(wait|let|format|rewrite|paragraph)\b[^)]*\)', lower_text, re.IGNORECASE):
        log("[WRITER] ENGLISH INJECTION: Found meta-commentary in parentheses", "WARNING")
        return False, "english_meta_commentary"
    
    return True, ""


def _quality_check(post_text: str) -> tuple[bool, str]:
    if not post_text:
        log("[WRITER] Quality check FAIL: bos_metin", "WARNING")
        return False, "bos_metin"
    
    if len(post_text) < 80:
        log(f"[WRITER] Quality check FAIL: cok_kisa (len={len(post_text)})", "WARNING")
        return False, "cok_kisa"
    
    # v5.2: 480 Karakter limiti (Threads uyumu)
    if len(post_text) > 480:
        log(f"[WRITER] Quality check FAIL: cok_uzun_480_limit (len={len(post_text)})", "WARNING")
        return False, "cok_uzun_480_limit"

    line_count = len([ln for ln in post_text.split("\n") if ln.strip()])
    
    if line_count < 3:
        log(f"[WRITER] Quality check FAIL: satir_az (lines={line_count})", "WARNING")
        return False, "satir_az"
    
    if line_count > 15:
        log(f"[WRITER] Quality check FAIL: satir_fazla (lines={line_count})", "WARNING")
        return False, "satir_fazla"

    if _contains_forbidden_script(post_text):
        log("[WRITER] Quality check FAIL: yabanci_alfabe", "WARNING")
        return False, "yabanci_alfabe"

    is_turkish_only, english_reason = _detect_english_injection(post_text)
    if not is_turkish_only:
        log(f"[WRITER] Quality check FAIL: {english_reason}", "WARNING")
        return False, english_reason

    lower = post_text.lower()
    
    for forbidden in _FORBIDDEN_CTA:
        if forbidden in lower:
            log(f"[WRITER] Quality check FAIL: yasak_cta ('{forbidden}')", "WARNING")
            return False, "yasak_cta"
    
    for bait in _HALLUCINATION_BAITS:
        if bait in lower:
            log(f"[WRITER] Quality check FAIL: halusinasyon_tetik ('{bait}')", "WARNING")
            return False, "halusinasyon_tetik"

    log(f"[WRITER] Quality check PASS (len={len(post_text)}, lines={line_count})", "INFO")
    return True, "ok"


def _fallback_post(article: dict) -> str:
    title = (article.get("title", "") or "").strip()
    summary = (article.get("summary", "") or "").strip()

    safe_title = title.upper()[:90] if title else "OTOMOTİVDE YENİ GELİŞME"
    
    # v5.2: Özet kısmını 480 karakter limitini aşmaması için 330'a kırptık.
    # 90 (başlık) + 50 (sabit cümle) + boşluklar = ~150 karakter. 330+150 = 480.
    body = summary[:330].strip() if summary else (
        "Güncel gelişmeyi sade şekilde aktardık. Net bilgiler geldikçe paylaşacağız."
    )

    fallback = (
        f"{safe_title}\n\n"
        f"{body}\n\n"
        "Siz bu gelişme hakkında ne düşünüyorsunuz?"
    ).strip()
    
    # Eğer hala 480'i aşarsa zorla kes
    if len(fallback) > 480:
        fallback = fallback[:477].rstrip() + "..."
        
    log(f"[WRITER] Using FALLBACK post (len={len(fallback)})", "INFO")
    return fallback


def _repair_post_with_ai(post_text: str, article: dict) -> str:
    title = article.get("title", "")
    summary = article.get("summary", "")
    source = article.get("source_name", "")

    repair_prompt = (
        "Aşağıdaki Facebook postunu düzelt.\n"
        "KRİTİK KURALLAR:\n"
        "- MUTLAKA TAMAMEN TÜRKÇE YAZ. Hiçbir İngilizce kelime kullanma.\n"
        "- TÜRKÇE KARAKTERLERİ (ç, ş, ğ, ü, ö, ı, Ç, Ş, Ğ, Ü, Ö, İ) MUTLAKA KULLAN. ASLA 'c, s, g, u, o, i' gibi İngilizce ASCII karakterlerle yazma.\n"
        "- PARANTEZ içinde açıklama/yorum yapma.\n"
        "- Meta-instruction verme ('format it like', 'rewrite as' gibi).\n"
        "- Bilgi uydurma, sadece verilen bilgileri kullan.\n"
        "- 15 satırı geçme.\n"
        "- Clickbait aşırılığına kaçma.\n"
        "- Son satırda doğal bir soru/çağrı olsun.\n"
        "- 'beğen/paylaş/takip et' gibi doğrudan CTA kullanma.\n"
        "- ÖNCELİKLİ KURAL: Toplam karakter sayısı KESİNLİKLE 480'i GEÇMEZ (Threads limiti 500). Gerekirse özeti kısalt.\n\n"
        f"Haber başlığı: {title}\n"
        f"Kaynak: {source}\n"
        f"Özet: {summary[:300]}\n\n"
        "Düzeltilecek metin:\n"
        f"{post_text}\n\n"
        "SADECE düzeltilmiş Türkçe post metnini ver. Başka hiçbir şey ekleme. Türkçe karakterleri (ç,ş,ğ,ü,ö,ı) mutlaka kullan."
    )
    
    log("[WRITER] Attempting AI repair", "INFO")
    repaired = ask_ai(repair_prompt, stage="writing_repair")
    
    if repaired:
        log(f"[WRITER] AI repair returned {len(repaired)} chars", "INFO")
    else:
        log("[WRITER] AI repair returned empty", "WARNING")
    
    return repaired or ""


def _build_writer_prompt(article: dict, writer_prompt: str) -> str:
    title = article.get("title", "")
    summary = article.get("summary", "")
    full_text = article.get("full_text", "")
    source = article.get("source_name", "")

    input_parts = [
        f"BAŞLIK: {title}",
        f"KAYNAK: {source}",
        f"ÖZET: {summary[:300]}",
    ]
    if full_text:
        input_parts.append(f"TAM_METİN: {full_text[:1000]}")

    return (
        f"{writer_prompt}\n\n"
        "KRİTİK ÇIKTI KURALLARI:\n"
        "- SADECE Türkçe post metni dön. İngilizce kelime kullanma.\n"
        "- TÜRKÇE KARAKTERLERİ (ç, ş, ğ, ü, ö, ı) MUTLAKA KULLAN. ASLA ASCII KARAKTER (c, s, g) YAZMA.\n"
        "- Parantez içinde meta-açıklama yapma.\n"
        "- 'Wait', 'let's', 'format', 'rewrite' gibi instruction verme.\n"
        "- Maksimum 15 satır\n"
        "- Boş satırlar dahil düzenli format\n"
        "- EN ÖNEMLİ KURAL: Toplam karakter sayısı KESİNLİKLE 480'i GEÇMEZ. (Threads platform limiti 500'dur). Haberin tüm önemli detayını bu 480 karakter içinde ver, gereksiz uzatma.\n"
        "- SADECE post metnini döndür, başka hiçbir şey ekleme\n\n"
        + "\n".join(input_parts)
    )


def generate_post_text(article: dict) -> str:
    log("[WRITER] Starting post generation", "INFO")
    
    prompts_config = load_config("prompts")
    writer_prompt = prompts_config.get("post_writer", "") if isinstance(prompts_config, dict) else ""
    
    if not writer_prompt:
        log("[WRITER] ERROR: post_writer promptu bulunamadi", "ERROR")
        return ""

    log("[WRITER] Calling AI for initial post generation (stage=writing)", "INFO")
    post_text = ask_ai(_build_writer_prompt(article, writer_prompt), stage="writing")
    
    if not post_text:
        log("[WRITER] AI returned empty response", "ERROR")
        return _fallback_post(article)
    
    log(f"[WRITER] AI returned {len(post_text)} chars (raw)", "INFO")
    post_text = _clean_non_turkish_chars(_strip_wrapper_artifacts(post_text))
    log(f"[WRITER] After cleaning: {len(post_text)} chars", "INFO")

    ok, reason = _quality_check(post_text)
    
    if ok:
        log("[WRITER] Initial post passed quality check", "INFO")
        return post_text if len(post_text) >= 30 else _fallback_post(article)

    log(f"[WRITER] Initial post FAILED quality: {reason}", "WARNING")
    
    if "english" in reason.lower():
        log("[WRITER] English injection detected, skipping repair, using fallback", "WARNING")
        return _fallback_post(article)
    
    log("[WRITER] Attempting AI repair", "INFO")

    repaired = _repair_post_with_ai(post_text, article)
    repaired = _clean_non_turkish_chars(_strip_wrapper_artifacts(repaired))
    
    log(f"[WRITER] Repaired post: {len(repaired)} chars", "INFO")
    
    ok2, reason2 = _quality_check(repaired)

    if ok2:
        log("[WRITER] Repaired post passed quality check", "INFO")
        return repaired if len(repaired) >= 30 else _fallback_post(article)

    log(f"[WRITER] Repaired post FAILED quality: {reason2}", "WARNING")
    log("[WRITER] Using fallback post", "INFO")
    return _fallback_post(article)


def _set_write_skipped(skip_reason: str) -> bool:
    output = {
        "article": None,
        "post_text": "",
        "post_text_length": 0,
        "skipped": True,
        "skip_reason": skip_reason,
    }
    set_stage("write", "done", output=output)
    log(f"[WRITER] Skipped: {skip_reason}", "INFO")
    return True


def _try_attach_full_text(article: dict) -> None:
    article_url = article.get("link", "")
    if not article_url:
        log("[WRITER] No article URL for full text scraping", "INFO")
        return
    
    try:
        log(f"[WRITER] Attempting full text scrape: {article_url[:80]}", "INFO")
        from agents.agent_fetcher import scrape_full_article

        full_text = scrape_full_article(article_url)
        
        if full_text:
            log(f"[WRITER] Full text scraped: {len(full_text)} chars", "INFO")
            article["full_text"] = full_text
        else:
            log("[WRITER] Full text scraping returned empty", "WARNING")
            
    except Exception as scrape_err:
        log(f"[WRITER] Full text scraping error: {scrape_err}", "WARNING")


def run() -> bool:
    log("[WRITER] ===== AGENT_WRITER STARTING =====", "INFO")

    score_stage = get_stage("score")
    if score_stage.get("status") != "done":
        log("[WRITER] Score stage not done, cannot proceed", "ERROR")
        set_stage("write", "error", error="score asamasi tamamlanmamis")
        return False

    score_output = score_stage.get("output", {})
    
    if bool(score_output.get("skipped", False)):
        skip_reason = (score_output.get("skip_reason", "") or "score skipped").strip()
        log(f"[WRITER] Score stage was skipped: {skip_reason}", "INFO")
        return _set_write_skipped(skip_reason)

    article = score_output.get("selected_article", {})
    
    if not article:
        log("[WRITER] No article in score output", "ERROR")
        set_stage("write", "error", error="Score cikisinda haber yok")
        return False

    log(f"[WRITER] Received article: {article.get('title', '')[:80]}", "INFO")
    set_stage("write", "running")

    try:
        _try_attach_full_text(article)
        post_text = generate_post_text(article)

        if not post_text:
            log("[WRITER] Post generation completely failed", "ERROR")
            set_stage("write", "error", error="Post metni uretilemedi")
            return False

        output = {
            "article": article,
            "post_text": post_text,
            "post_text_length": len(post_text),
            "skipped": False,
        }
        
        set_stage("write", "done", output=output)
        log(f"[WRITER] Success: Generated {len(post_text)} char post", "INFO")
        return True

    except Exception as exc:
        log(f"[WRITER] Critical exception: {exc}", "ERROR")
        import traceback
        log(f"[WRITER] Traceback: {traceback.format_exc()}", "ERROR")
        set_stage("write", "error", error=str(exc))
        return False


if __name__ == "__main__":
    from core.state_manager import init_pipeline

    log("=== agent_writer.py modul testi basliyor ===")
    init_pipeline("test-writer")

    fake_article = {
        "title": "Test: Yeni Elektrikli SUV Türkiye'de Satışa Çıktı",
        "link": "https://test.com/haber1",
        "summary": "Yeni elektrikli SUV modeli Türkiye pazarına girdi. Fiyatlar ve teknik özellikler açıklandı.",
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
