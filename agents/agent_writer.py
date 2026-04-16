"""
agents/agent_writer.py - Icerik yazma ajani
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
]

_HALLUCINATION_BAITS = [
    "iste o araclar",
    "iste liste",
    "detaylar soyle",
    "detaylar su sekilde",
]


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
    if any(x in lower for x in _FORBIDDEN_CTA):
        return False, "yasak_cta"
    if any(x in lower for x in _HALLUCINATION_BAITS):
        return False, "halusinasyon_tetik"

    return True, "ok"


def _fallback_post(article: dict) -> str:
    title = (article.get("title", "") or "").strip()
    summary = (article.get("summary", "") or "").strip()

    safe_title = title.upper()[:90] if title else "OTOMOTIVDE YENI GELISME"
    body = summary[:420].strip() if summary else (
        "Guncel gelismeyi sade sekilde aktardik. Net bilgiler geldikce paylasacagiz."
    )

    return (
        f"{safe_title}\n\n"
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


def _build_writer_prompt(article: dict, writer_prompt: str) -> str:
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

    return (
        f"{writer_prompt}\n\n"
        "Cikti sinirlari:\n"
        "- Maksimum 15 satir\n"
        "- Bos satirlar dahil duzenli format\n"
        "- Sadece post metnini dondur\n\n"
        + "\n".join(input_parts)
    )


def generate_post_text(article: dict) -> str:
    prompts_config = load_config("prompts")
    writer_prompt = prompts_config.get("post_writer", "") if isinstance(prompts_config, dict) else ""
    if not writer_prompt:
        log("post_writer promptu bulunamadi", "WARNING")
        return ""

    post_text = ask_ai(_build_writer_prompt(article, writer_prompt))
    post_text = _clean_non_turkish_chars(_strip_wrapper_artifacts(post_text))

    ok, reason = _quality_check(post_text)
    if ok:
        return post_text if len(post_text) >= 30 else _fallback_post(article)

    log(f"Ilk yazi kalite kontrolden gecemedi: {reason}", "WARNING")

    repaired = _repair_post_with_ai(post_text, article)
    repaired = _clean_non_turkish_chars(_strip_wrapper_artifacts(repaired))
    ok2, reason2 = _quality_check(repaired)

    if ok2:
        return repaired if len(repaired) >= 30 else _fallback_post(article)

    log(f"Duzeltilmis yazi da gecemedi: {reason2}. Fallback kullaniliyor.", "WARNING")
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
    log(f"writer skipped: {skip_reason}", "INFO")
    return True


def _try_attach_full_text(article: dict) -> None:
    article_url = article.get("link", "")
    if not article_url:
        return
    try:
        from agents.agent_fetcher import scrape_full_article

        full_text = scrape_full_article(article_url)
        if full_text:
            article["full_text"] = full_text
    except Exception as scrape_err:
        log(f"Tam metin cekme hatasi: {scrape_err}", "WARNING")


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
    if bool(score_output.get("skipped", False)):
        skip_reason = (score_output.get("skip_reason", "") or "score skipped").strip()
        return _set_write_skipped(skip_reason)

    article = score_output.get("selected_article", {})
    if not article:
        log("Score cikisinda haber yok", "WARNING")
        set_stage("write", "error", error="Score cikisinda haber yok")
        return False

    set_stage("write", "running")

    try:
        _try_attach_full_text(article)
        post_text = generate_post_text(article)

        if not post_text:
            set_stage("write", "error", error="Post metni uretilemedi")
            return False

        output = {
            "article": article,
            "post_text": post_text,
            "post_text_length": len(post_text),
            "skipped": False,
        }
        set_stage("write", "done", output=output)
        log(f"agent_writer tamamlandi -> {len(post_text)} karakter")
        return True

    except Exception as exc:
        log(f"agent_writer kritik hata: {exc}", "ERROR")
        set_stage("write", "error", error=str(exc))
        return False


if __name__ == "__main__":
    from core.state_manager import init_pipeline

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
