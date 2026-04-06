"""
agents/agent_scorer.py — Viral Puanlama Ajanı (v5)

otoXtra Facebook Botu için pipeline'dan haberleri alıp
YZ ile puanlayan ve en iyi haberi seçen bağımsız ajan.

Çalışma sırası:
  1. pipeline.json'dan fetch çıktısını oku
  2. Haberleri YZ ile viral puanla (batch halinde)
  3. Eşik kontrolü uygula
  4. En yüksek puanlı haberi pipeline.json'a yaz

Bağımsız çalıştırma:
    python agents/agent_scorer.py
    python agents/agent_scorer.py --test

Diğer modüller bu ajanı şöyle çağırır:
    from agents.agent_scorer import run
    success = run()
"""

import sys
import time
from typing import Optional

from core.logger import log
from core.config_loader import load_config
from core.helpers import (
    get_posted_news,
    get_today_post_count,
    is_similar_title,
)
from core.state_manager import get_stage, set_stage, init_pipeline


# ============================================================
# SABİTLER
# ============================================================

BATCH_SIZE: int = 20
"""Her seferinde YZ'ye gönderilecek maksimum haber sayısı."""

BATCH_DELAY_SECONDS: int = 3
"""Batch'ler arası bekleme süresi (saniye)."""

UNSCORED_DEFAULT: int = 0
"""YZ'nin puanlayamadığı haberlere verilen varsayılan puan."""

CROSS_VALIDATE_THRESHOLD: float = 0.4
"""Sıra numarası ile eşleşirken başlık çapraz doğrulama eşiği."""


# ============================================================
# TEST MODU
# ============================================================

def _is_test_mode() -> bool:
    """TEST_MODE ortam değişkenini veya --test argümanını kontrol eder."""
    import os
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    if "--test" in sys.argv:
        return True
    return False


# ============================================================
# YZ MODÜLÜ — GEÇİCİ IMPORT KÖPRÜSÜ
# ============================================================
# agent_writer.py tamamlandığında bu blok oraya taşınacak.
# Şimdilik scorer kendi YZ çağrısını yapar.

def _ask_ai(prompt: str) -> str:
    """YZ'ye soru sorar. ai_processor'dan köprü."""
    try:
        # Yeni mimari: agents/agent_writer.py içindeki ask_ai
        # Henüz oluşturulmadıysa eski src/ai_processor'ı dene
        try:
            from agents.agent_writer import ask_ai
            return ask_ai(prompt)
        except ImportError:
            pass

        try:
            sys.path.insert(0, "src")
            from ai_processor import ask_ai as old_ask_ai
            return old_ask_ai(prompt)
        except ImportError:
            pass

        log("YZ modülü bulunamadı (agent_writer veya ai_processor)", "ERROR")
        return ""
    except Exception as exc:
        log(f"YZ çağrısı hatası: {exc}", "ERROR")
        return ""


def _parse_ai_json(response: str):
    """YZ cevabını JSON'a çevirir."""
    try:
        try:
            from agents.agent_writer import parse_ai_json
            return parse_ai_json(response)
        except ImportError:
            pass

        try:
            sys.path.insert(0, "src")
            from ai_processor import parse_ai_json as old_parse
            return old_parse(response)
        except ImportError:
            pass

        # Fallback: manuel parse
        import json
        import re
        cleaned = response.strip()
        match = re.search(r'\[.*\]', cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        return None
    except Exception:
        return None


# ============================================================
# 1. YARDIMCI — HABER METNİ FORMATLAMA
# ============================================================

def _format_articles_numbered(articles: list) -> str:
    """Haber listesini numaralandırılmış metin formatına çevirir.

    YZ'ye gönderilecek formatta hazırlar:
      "1. Başlık: … | Özet: …
       2. Başlık: … | Özet: …"

    Özet 300 karakterden uzunsa kırpılır.
    """
    lines = []
    for i, article in enumerate(articles, start=1):
        title = article.get("title", "Başlık yok").strip()
        summary = article.get("summary", "Özet yok").strip()

        if len(summary) > 300:
            summary = summary[:297] + "..."

        lines.append(f"{i}. Başlık: {title} | Özet: {summary}")

    return "\n".join(lines)


def _split_into_batches(articles: list) -> list:
    """Haber listesini BATCH_SIZE'lık gruplara böler."""
    return [
        articles[i: i + BATCH_SIZE]
        for i in range(0, len(articles), BATCH_SIZE)
    ]


# ============================================================
# 2. YZ SONUÇLARINI HABERLERLE EŞLEŞTİRME
# ============================================================

def _match_ai_results_to_articles(
    ai_results: list,
    articles: list,
) -> list:
    """YZ'den gelen sonuç listesini orijinal haberlerle eşleştirir.

    3 strateji sırayla denenir:
      1. "sira" numarası + çapraz başlık doğrulaması
      2. "baslik" ile birebir eşleşme
      3. Fuzzy match (benzerlik eşiği 0.6)

    Returns:
        list[tuple]: (ai_result, article) çiftleri.
    """
    matched = []
    used_indices = set()

    for ai_result in ai_results:
        matched_article = None
        matched_index = None

        # ── Strateji 1: sıra numarası + çapraz doğrulama ──
        sira = ai_result.get("sira")
        if sira is not None:
            try:
                index = int(sira) - 1
                if 0 <= index < len(articles) and index not in used_indices:
                    ai_baslik = ai_result.get("baslik", "").strip()
                    article_title = articles[index].get("title", "").strip()

                    if not ai_baslik:
                        matched_article = articles[index]
                        matched_index = index
                    elif is_similar_title(
                        ai_baslik, article_title,
                        threshold=CROSS_VALIDATE_THRESHOLD
                    ):
                        matched_article = articles[index]
                        matched_index = index
                    else:
                        log(
                            f"  ⚠️ ÇAPRAZ DOĞRULAMA: Sıra {sira} uyuşmuyor → "
                            f"başlık eşleştirmesine düşülüyor",
                            "WARNING",
                        )
            except (ValueError, TypeError):
                pass

        # ── Strateji 2: baslik birebir eşleşme ──
        if matched_article is None:
            ai_baslik_str = ai_result.get("baslik", "").strip()
            if ai_baslik_str:
                ai_baslik_lower = ai_baslik_str.lower()
                for i, article in enumerate(articles):
                    if i in used_indices:
                        continue
                    if ai_baslik_lower == article.get("title", "").strip().lower():
                        matched_article = article
                        matched_index = i
                        break

        # ── Strateji 3: fuzzy match ──
        if matched_article is None:
            ai_baslik_fuzzy = ai_result.get("baslik", "").strip()
            if ai_baslik_fuzzy:
                for i, article in enumerate(articles):
                    if i in used_indices:
                        continue
                    try:
                        if is_similar_title(
                            ai_baslik_fuzzy,
                            article.get("title", "").strip(),
                            threshold=0.6
                        ):
                            matched_article = article
                            matched_index = i
                            break
                    except Exception:
                        continue

        # ── Sonuç ──
        if matched_article is not None and matched_index is not None:
            matched.append((ai_result, matched_article))
            used_indices.add(matched_index)
        else:
            kaybolan = ai_result.get("baslik", "(başlık yok)")
            log(f"  ⚠️ Eşleştirilemeyen YZ sonucu: {kaybolan}", "WARNING")

    return matched


# ============================================================
# 3. VİRAL PUANLAMA (BATCH)
# ============================================================

def run_viral_scoring(articles: list) -> list:
    """Haberleri YZ ile viral potansiyeline göre puanlar.

    Haberler BATCH_SIZE'lık gruplara bölünerek YZ'ye gönderilir.
    Her habere 0-100 arası puan verilir.

    Returns:
        list[dict]: Puanlanmış haberler (score alanı eklenmiş),
                    yüksekten düşüğe sıralı.
    """
    if not articles:
        log("Viral puanlamaya gelen haber yok", "INFO")
        return []

    prompts_config = load_config("prompts")
    scorer_prompt = prompts_config.get("viral_scorer", "")

    if not scorer_prompt:
        log(
            f"⚠️ viral_scorer promptu bulunamadı → "
            f"tüm haberlere {UNSCORED_DEFAULT} puan veriliyor",
            "WARNING",
        )
        for article in articles:
            article["score"] = UNSCORED_DEFAULT
        return articles

    batches = _split_into_batches(articles)
    batch_count = len(batches)

    log(
        f"📊 Viral puanlama: {len(articles)} haber, "
        f"{batch_count} batch ({BATCH_SIZE}'lik gruplar)"
    )

    all_scored = []
    total_ai_scored = 0
    total_unscored = 0

    for batch_num, batch in enumerate(batches, start=1):
        log(f"  📦 Batch {batch_num}/{batch_count}: {len(batch)} haber...")

        numbered_text = _format_articles_numbered(batch)
        full_prompt = f"{scorer_prompt}\n\n{numbered_text}"

        # YZ'ye gönder
        ai_response = _ask_ai(full_prompt)

        if not ai_response:
            log(
                f"  ⚠️ Batch {batch_num}: YZ yanıt vermedi → "
                f"tüm haberlere {UNSCORED_DEFAULT} puan",
                "WARNING",
            )
            for article in batch:
                article["score"] = UNSCORED_DEFAULT
                all_scored.append(article)
            total_unscored += len(batch)
            if batch_num < batch_count:
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        # Parse et
        ai_results = _parse_ai_json(ai_response)

        if not ai_results or not isinstance(ai_results, list):
            log(
                f"  ⚠️ Batch {batch_num}: JSON parse edilemedi → "
                f"tüm haberlere {UNSCORED_DEFAULT} puan",
                "WARNING",
            )
            for article in batch:
                article["score"] = UNSCORED_DEFAULT
                all_scored.append(article)
            total_unscored += len(batch)
            if batch_num < batch_count:
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        # Eşleştir
        matched_pairs = _match_ai_results_to_articles(ai_results, batch)
        matched_ids = {id(art) for _, art in matched_pairs}

        # Puanları ata
        batch_scored = 0
        for ai_result, article in matched_pairs:
            try:
                puan = max(0, min(100, int(ai_result.get("puan", UNSCORED_DEFAULT))))
            except (ValueError, TypeError):
                puan = UNSCORED_DEFAULT

            article["score"] = puan
            all_scored.append(article)
            batch_scored += 1
            total_ai_scored += 1

            log(f"  📊 {puan:3d}/100 — {article.get('title', '')[:60]}")

        # Puanlanamayanlara 0 ver
        batch_unscored = 0
        for article in batch:
            if id(article) not in matched_ids:
                article["score"] = UNSCORED_DEFAULT
                all_scored.append(article)
                batch_unscored += 1
                total_unscored += 1

        if batch_unscored > 0:
            log(f"  ℹ️ Batch {batch_num}: {batch_unscored} haber puanlanamadı")

        log(
            f"  📦 Batch {batch_num} bitti: "
            f"{batch_scored} puanlandı, {batch_unscored} puanlanamadı"
        )

        if batch_num < batch_count:
            time.sleep(BATCH_DELAY_SECONDS)

    # Yüksekten düşüğe sırala
    all_scored.sort(key=lambda x: x.get("score", 0), reverse=True)

    log(
        f"📊 Puanlama bitti: {len(articles)} haber → "
        f"{total_ai_scored} puanlandı, {total_unscored} puanlanamadı"
    )

    return all_scored


# ============================================================
# 4. EŞİK KONTROLÜ
# ============================================================

def apply_thresholds(scored_articles: list) -> list:
    """Puanlanan haberlere eşik kontrolü uygular.

    Bugün 2'den az post yapıldıysa slow_day_score kullanılır.
    Değilse publish_score kullanılır.

    Returns:
        list[dict]: Eşiği geçen haberler (yüksekten düşüğe sıralı).
    """
    if not scored_articles:
        log("Eşik kontrolüne gelen haber yok", "INFO")
        return []

    scoring_config = load_config("scoring")
    thresholds = scoring_config.get("thresholds", {})
    publish_score = thresholds.get("publish_score", 65)
    slow_day_score = thresholds.get("slow_day_score", 50)

    posted_data = get_posted_news()
    today_post_count = get_today_post_count(posted_data)

    if today_post_count < 2:
        threshold = slow_day_score
        log(f"📅 Sakin gün ({today_post_count} post) → eşik: {threshold}")
    else:
        threshold = publish_score
        log(f"📅 Normal gün ({today_post_count} post) → eşik: {threshold}")

    passed = []
    eliminated = 0

    for article in scored_articles:
        score = article.get("score", 0)
        if score >= threshold:
            passed.append(article)
        else:
            eliminated += 1
            if score > 0:
                log(
                    f"  📉 Elendi: puan {score} < eşik {threshold} — "
                    f"{article.get('title', '')[:60]}"
                )

    log(
        f"📊 Eşik kontrolü: {len(scored_articles)} → "
        f"{len(passed)} geçti, {eliminated} elendi (eşik: {threshold})"
    )

    return passed


# ============================================================
# 5. ANA PUANLAMA FONKSİYONU
# ============================================================

def filter_and_score(articles: list) -> Optional[dict]:
    """Haberleri puanlar, eşik kontrolü yapar, en iyisini döner.

    Akış:
      1. Viral Puanlama → YZ ile 0-100 puan
      2. Eşik Kontrolü → düşük puanlıları eler
      3. En yüksek puanlı haberi döner

    Args:
        articles: Filtrelenmiş haber listesi (agent_fetcher çıktısı).

    Returns:
        dict: Seçilen haber. Uygun yoksa None.
    """
    sep = "=" * 55

    log(sep)
    log(f"PUANLAMA BAŞLIYOR: {len(articles)} haber")
    log(sep)

    if not articles:
        log("Puanlanacak haber yok", "INFO")
        return None

    # ADIM 1: Viral puanlama
    log(f"ADIM 1: Viral Puanlama ({len(articles)} haber)")
    scored = run_viral_scoring(articles)

    if not scored:
        log("Puanlanan haber yok", "WARNING")
        return None

    # ADIM 2: Eşik kontrolü
    log(f"ADIM 2: Eşik Kontrolü ({len(scored)} haber)")
    above_threshold = apply_thresholds(scored)

    if not above_threshold:
        log("Eşik üstünde haber yok — paylaşım yapılmayacak", "WARNING")
        return None

    # ADIM 3: En yüksek puanlıyı seç (liste zaten sıralı)
    best = above_threshold[0]

    log(sep)
    log(
        f"🏆 SEÇİLDİ: {best.get('title', '')[:60]} "
        f"(puan: {best.get('score', 0)})"
    )
    log(sep)

    return best


# ============================================================
# 6. AJAN GİRİŞ NOKTASI
# ============================================================

def run() -> bool:
    """Ajanı çalıştırır. orchestrator.py tarafından çağrılır.

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    log("─" * 55)
    log("agent_scorer başlıyor")
    log("─" * 55)

    # Fetch aşaması bitti mi kontrol et
    fetch_stage = get_stage("fetch")
    if fetch_stage.get("status") != "done":
        log("fetch aşaması tamamlanmamış — scorer çalıştırılamaz", "ERROR")
        set_stage("score", "error", error="fetch aşaması tamamlanmamış")
        return False

    # Fetch çıktısından haberleri al
    fetch_output = fetch_stage.get("output", {})
    articles = fetch_output.get("articles", [])

    if not articles:
        log("Fetch çıktısında haber yok", "WARNING")
        set_stage("score", "error", error="Fetch çıktısında haber yok")
        return False

    log(f"Fetch'ten {len(articles)} haber alındı")

    # Aşamayı çalışıyor işaretle
    set_stage("score", "running")

    try:
        best_article = filter_and_score(articles)

        if best_article is None:
            log("Uygun haber bulunamadı", "WARNING")
            set_stage("score", "error", error="Eşik üstünde haber yok")
            return False

        # Pipeline'a yaz
        output = {
            "selected_article": best_article,
            "score": best_article.get("score", 0),
            "title": best_article.get("title", ""),
        }
        set_stage("score", "done", output=output)

        log(
            f"agent_scorer tamamlandı → "
            f"'{best_article.get('title', '')[:50]}' "
            f"(puan: {best_article.get('score', 0)}) pipeline'a yazıldı"
        )
        return True

    except Exception as exc:
        log(f"agent_scorer kritik hata: {exc}", "ERROR")
        set_stage("score", "error", error=str(exc))
        return False


# ============================================================
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ============================================================

if __name__ == "__main__":
    log("=== agent_scorer.py modül testi başlıyor ===")

    # Test için pipeline başlat
    init_pipeline("test-scorer")

    # Önce fetcher'ı çalıştır
    log("Önce agent_fetcher çalıştırılıyor...")
    try:
        from agents.agent_fetcher import run as fetcher_run
        fetcher_success = fetcher_run()
    except ImportError:
        log("agent_fetcher import edilemedi — sahte veri kullanılıyor", "WARNING")
        fetcher_success = False

        # Sahte fetch verisi yükle
        fake_articles = [
            {
                "title": "Test: Yeni Elektrikli SUV Türkiye'de Satışa Çıktı",
                "link": "https://test.com/haber1",
                "summary": "Yeni elektrikli SUV modeli Türkiye pazarına girdi. "
                           "Fiyatlar ve teknik özellikler açıklandı.",
                "published": "2025-01-15T12:00:00+03:00",
                "image_url": "",
                "source_name": "Test Kaynak",
                "source_priority": "high",
                "can_scrape_image": False,
            },
            {
                "title": "Test: Hibrit Araçlarda ÖTV İndirimi Gündemde",
                "link": "https://test.com/haber2",
                "summary": "Hükümet hibrit araçlar için ÖTV indirimi "
                           "üzerinde çalışıyor. Karar bu ay açıklanacak.",
                "published": "2025-01-15T11:00:00+03:00",
                "image_url": "",
                "source_name": "Test Kaynak",
                "source_priority": "medium",
                "can_scrape_image": False,
            },
        ]
        set_stage("fetch", "done", output={
            "articles": fake_articles,
            "count": len(fake_articles)
        })
        fetcher_success = True

    if not fetcher_success:
        log("Fetcher başarısız oldu — test durduruluyor", "ERROR")
        sys.exit(1)

    # Scorer'ı çalıştır
    log("\nagent_scorer çalıştırılıyor...")
    success = run()

    if success:
        score_stage = get_stage("score")
        output = score_stage.get("output", {})
        selected = output.get("selected_article", {})

        log(f"\n{'─' * 50}")
        log("SONUÇ:")
        log(f"  Başlık : {selected.get('title', 'YOK')}")
        log(f"  Puan   : {selected.get('score', 0)}")
        log(f"  Kaynak : {selected.get('source_name', 'YOK')}")
        log(f"  URL    : {selected.get('link', 'YOK')[:70]}")
        log(f"{'─' * 50}")
    else:
        log("Ajan başarısız oldu", "WARNING")

    log("=== agent_scorer.py modül testi tamamlandı ===")
