"""
agents/agent_scorer.py — Viral Puanlama Ajanı (v5.2 — Trend Bonusu)

Değişiklikler v5.2:
  - apply_trend_bonus()  : Fetcher'dan gelen trend_bonus puanına ekler (YENİ)
  - filter_and_score()   : Akışa trend bonusu adımı eklendi
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
    get_turkey_now,
)
from core.state_manager import get_stage, set_stage, init_pipeline


# ============================================================
# SABİTLER
# ============================================================

BATCH_SIZE: int = 20
BATCH_DELAY_SECONDS: int = 3
UNSCORED_DEFAULT: int = 0
CROSS_VALIDATE_THRESHOLD: float = 0.4

FRESHNESS_TIERS = [
    (2,  +7),
    (4,  +3),
    (12,  0),
]
FRESHNESS_OLD_MALUS: int = -5


# ============================================================
# TEST MODU
# ============================================================

def _is_test_mode() -> bool:
    import os
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    if "--test" in sys.argv:
        return True
    return False


# ============================================================
# YZ MODÜLÜ — GEÇİCİ IMPORT KÖPRÜSÜ
# ============================================================

def _ask_ai(prompt: str) -> str:
    try:
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
        log("YZ modülü bulunamadı", "ERROR")
        return ""
    except Exception as exc:
        log(f"YZ çağrısı hatası: {exc}", "ERROR")
        return ""


def _parse_ai_json(response: str):
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
# 1. HABER METNİ FORMATLAMA
# ============================================================

def _format_articles_numbered(articles: list) -> str:
    lines = []
    for i, article in enumerate(articles, start=1):
        title = article.get("title", "Başlık yok").strip()
        summary = article.get("summary", "Özet yok").strip()
        if len(summary) > 300:
            summary = summary[:297] + "..."
        lines.append(f"{i}. Başlık: {title} | Özet: {summary}")
    return "\n".join(lines)


def _split_into_batches(articles: list) -> list:
    return [
        articles[i: i + BATCH_SIZE]
        for i in range(0, len(articles), BATCH_SIZE)
    ]


# ============================================================
# 2. YZ SONUÇLARINI HABERLERLE EŞLEŞTİRME
# ============================================================

def _match_ai_results_to_articles(ai_results: list, articles: list) -> list:
    matched = []
    used_indices = set()

    for ai_result in ai_results:
        matched_article = None
        matched_index = None

        # Strateji 1: sıra numarası + çapraz doğrulama
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

        # Strateji 2: baslik birebir eşleşme
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

        # Strateji 3: fuzzy match
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
    """Haberleri YZ ile viral potansiyeline göre puanlar."""
    if not articles:
        log("Viral puanlamaya gelen haber yok", "INFO")
        return []

    prompts_config = load_config("prompts")
    scorer_prompt = prompts_config.get("viral_scorer", "")

    if not scorer_prompt:
        log(f"⚠️ viral_scorer promptu bulunamadı → tüm haberlere {UNSCORED_DEFAULT} puan", "WARNING")
        for article in articles:
            article["score"] = UNSCORED_DEFAULT
        return articles

    batches = _split_into_batches(articles)
    batch_count = len(batches)

    log(f"📊 Viral puanlama: {len(articles)} haber, {batch_count} batch")

    all_scored = []
    total_ai_scored = 0
    total_unscored = 0

    for batch_num, batch in enumerate(batches, start=1):
        log(f"  📦 Batch {batch_num}/{batch_count}: {len(batch)} haber...")

        numbered_text = _format_articles_numbered(batch)
        full_prompt = f"{scorer_prompt}\n\n{numbered_text}"

        ai_response = _ask_ai(full_prompt)

        if not ai_response:
            log(f"  ⚠️ Batch {batch_num}: YZ yanıt vermedi → {UNSCORED_DEFAULT} puan", "WARNING")
            for article in batch:
                article["score"] = UNSCORED_DEFAULT
                all_scored.append(article)
            total_unscored += len(batch)
            if batch_num < batch_count:
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        ai_results = _parse_ai_json(ai_response)

        if not ai_results or not isinstance(ai_results, list):
            log(f"  ⚠️ Batch {batch_num}: JSON parse edilemedi → {UNSCORED_DEFAULT} puan", "WARNING")
            for article in batch:
                article["score"] = UNSCORED_DEFAULT
                all_scored.append(article)
            total_unscored += len(batch)
            if batch_num < batch_count:
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        matched_pairs = _match_ai_results_to_articles(ai_results, batch)
        matched_ids = {id(art) for _, art in matched_pairs}

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

        batch_unscored = 0
        for article in batch:
            if id(article) not in matched_ids:
                article["score"] = UNSCORED_DEFAULT
                all_scored.append(article)
                batch_unscored += 1
                total_unscored += 1

        if batch_unscored > 0:
            log(f"  ℹ️ Batch {batch_num}: {batch_unscored} haber puanlanamadı")

        log(f"  📦 Batch {batch_num} bitti: {batch_scored} puanlandı, {batch_unscored} puanlanamadı")

        if batch_num < batch_count:
            time.sleep(BATCH_DELAY_SECONDS)

    all_scored.sort(key=lambda x: x.get("score", 0), reverse=True)

    log(f"📊 Puanlama bitti: {len(articles)} haber → {total_ai_scored} puanlandı")

    return all_scored


# ============================================================
# 4. TAZELİK BONUSU
# ============================================================

def _calculate_freshness_bonus(published_str: str) -> int:
    if not published_str:
        return 0
    try:
        from datetime import datetime, timezone
        try:
            from dateutil import parser as dateutil_parser
            pub_dt = dateutil_parser.parse(published_str)
        except ImportError:
            cleaned = published_str.strip()
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            pub_dt = datetime.fromisoformat(cleaned)

        if pub_dt.tzinfo is None:
            from datetime import timedelta
            pub_dt = pub_dt.replace(tzinfo=timezone(timedelta(hours=3)))

        now = get_turkey_now()
        if now.tzinfo is None:
            from datetime import timedelta
            now = now.replace(tzinfo=timezone(timedelta(hours=3)))

        age_hours = (now - pub_dt).total_seconds() / 3600
        if age_hours < 0:
            return 0

        for max_hours, bonus in FRESHNESS_TIERS:
            if age_hours < max_hours:
                return bonus

        return FRESHNESS_OLD_MALUS

    except Exception as exc:
        log(f"  ⚠️ Tazelik bonusu hesaplanamadı ({published_str!r}): {exc}", "WARNING")
        return 0


def apply_freshness_bonus(scored_articles: list) -> list:
    """Puanlanmış haberlere tazelik bonusu/malusu uygular."""
    if not scored_articles:
        return []

    log(f"⏱️ Tazelik bonusu uygulanıyor ({len(scored_articles)} haber)...")

    bonus_applied = 0
    malus_applied = 0
    no_change = 0

    for article in scored_articles:
        published_str = article.get("published", "")
        bonus = _calculate_freshness_bonus(published_str)

        if bonus == 0:
            no_change += 1
            continue

        original_score = article.get("score", 0)
        new_score = max(0, min(100, original_score + bonus))
        article["score"] = new_score

        if bonus > 0:
            bonus_applied += 1
            direction = f"+{bonus}"
        else:
            malus_applied += 1
            direction = str(bonus)

        log(
            f"  ⏱️ {direction:3s} puan → "
            f"{original_score} → {new_score} — "
            f"{article.get('title', '')[:55]}"
        )

    log(f"⏱️ Tazelik: {bonus_applied} bonus, {malus_applied} malus, {no_change} değişmedi")
    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored_articles


# ============================================================
# 5. TREND BONUSU (YENİ)
# ============================================================

def apply_trend_bonus(scored_articles: list) -> list:
    """Fetcher'dan gelen trend_bonus değerini YZ puanına ekler.

    agent_fetcher._detect_trends() her habere "trend_bonus" alanı ekler.
    Bu fonksiyon o bonusu "score" alanına uygular.

    Puan 0-100 aralığında kalır (clamp uygulanır).

    Args:
        scored_articles: 'score' ve 'trend_bonus' alanları dolu liste.

    Returns:
        list: Güncellenmiş, yeniden sıralı liste.
    """
    if not scored_articles:
        return []

    trend_applied = 0
    no_trend = 0

    for article in scored_articles:
        trend_bonus = article.get("trend_bonus", 0)
        trend_count = article.get("trend_count", 1)

        if trend_bonus <= 0:
            no_trend += 1
            continue

        original_score = article.get("score", 0)
        new_score = max(0, min(100, original_score + trend_bonus))
        article["score"] = new_score
        trend_applied += 1

        log(
            f"  🔥 TREND +{trend_bonus} puan ({trend_count} kaynak) → "
            f"{original_score} → {new_score} — "
            f"{article.get('title', '')[:50]}"
        )

    if trend_applied > 0:
        log(f"🔥 Trend bonusu: {trend_applied} habere uygulandı, {no_trend} haberde trend yok")
    else:
        log("🔥 Trend bonusu: Bu turda trend haber yok")

    # Yeni puanlara göre yeniden sırala
    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored_articles


# ============================================================
# 6. EŞİK KONTROLÜ
# ============================================================

def apply_thresholds(scored_articles: list) -> list:
    """Puanlanan haberlere eşik kontrolü uygular."""
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
# 7. ANA PUANLAMA FONKSİYONU
# ============================================================

def filter_and_score(articles: list) -> Optional[dict]:
    """Haberleri puanlar, eşik kontrolü yapar, en iyisini döner.

    Akış:
      1. Viral Puanlama  → YZ ile 0-100 puan
      2. Tazelik Bonusu  → Yayın tarihine göre puan düzeltmesi
      3. Trend Bonusu    → Kaç kaynaktan geldiğine göre bonus  ← YENİ
      4. Eşik Kontrolü   → Düşük puanlıları eler
      5. En yüksek puanlı haberi döner
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

    # ADIM 2: Tazelik bonusu
    log(f"ADIM 2: Tazelik Bonusu ({len(scored)} haber)")
    scored = apply_freshness_bonus(scored)

    # ADIM 3: Trend bonusu  ← YENİ
    log(f"ADIM 3: Trend Bonusu ({len(scored)} haber)")
    scored = apply_trend_bonus(scored)

    # ADIM 4: Eşik kontrolü
    log(f"ADIM 4: Eşik Kontrolü ({len(scored)} haber)")
    above_threshold = apply_thresholds(scored)

    if not above_threshold:
        log("Eşik üstünde haber yok — paylaşım yapılmayacak", "WARNING")
        return None

    # ADIM 5: En yüksek puanlıyı seç
    best = above_threshold[0]

    log(sep)
    log(
        f"🏆 SEÇİLDİ: {best.get('title', '')[:60]} "
        f"(puan: {best.get('score', 0)}, "
        f"trend: {best.get('trend_count', 1)} kaynak)"
    )
    log(sep)

    return best


# ============================================================
# 8. AJAN GİRİŞ NOKTASI
# ============================================================

def run() -> bool:
    """Ajanı çalıştırır. orchestrator.py tarafından çağrılır."""
    log("─" * 55)
    log("agent_scorer başlıyor")
    log("─" * 55)

    fetch_stage = get_stage("fetch")
    if fetch_stage.get("status") != "done":
        log("fetch aşaması tamamlanmamış — scorer çalıştırılamaz", "ERROR")
        set_stage("score", "error", error="fetch aşaması tamamlanmamış")
        return False

    fetch_output = fetch_stage.get("output", {})
    articles = fetch_output.get("articles", [])

    if not articles:
        log("Fetch çıktısında haber yok", "WARNING")
        set_stage("score", "error", error="Fetch çıktısında haber yok")
        return False

    log(f"Fetch'ten {len(articles)} haber alındı")

    set_stage("score", "running")

    try:
        best_article = filter_and_score(articles)

        if best_article is None:
            log("Uygun haber bulunamadı", "WARNING")
            set_stage("score", "error", error="Eşik üstünde haber yok")
            return False

        output = {
            "selected_article": best_article,
            "score": best_article.get("score", 0),
            "title": best_article.get("title", ""),
            "trend_count": best_article.get("trend_count", 1),
            "trend_bonus": best_article.get("trend_bonus", 0),
        }
        set_stage("score", "done", output=output)

        log(
            f"agent_scorer tamamlandı → "
            f"'{best_article.get('title', '')[:50]}' "
            f"(puan: {best_article.get('score', 0)}, "
            f"trend: {best_article.get('trend_count', 1)} kaynak)"
        )
        return True

    except Exception as exc:
        log(f"agent_scorer kritik hata: {exc}", "ERROR")
        set_stage("score", "error", error=str(exc))
        return False


# ============================================================
# MODÜL TESTİ
# ============================================================

if __name__ == "__main__":
    log("=== agent_scorer.py modül testi başlıyor ===")

    init_pipeline("test-scorer")

    log("Önce agent_fetcher çalıştırılıyor...")
    try:
        from agents.agent_fetcher import run as fetcher_run
        fetcher_success = fetcher_run()
    except ImportError:
        log("agent_fetcher import edilemedi — sahte veri kullanılıyor", "WARNING")
        fetcher_success = False

        from datetime import datetime, timedelta, timezone
        from core.helpers import generate_topic_fingerprint

        tr_tz = timezone(timedelta(hours=3))
        now_tr = datetime.now(tr_tz)

        fake_articles = [
            {
                "title": "Tesla Model S ve Model X üretimi durdu",
                "link": "https://test.com/haber1",
                "summary": "Tesla ikonik modellerini sonlandırdı.",
                "published": (now_tr - timedelta(hours=1)).isoformat(),
                "image_url": "",
                "source_name": "Test A",
                "source_priority": "high",
                "can_scrape_image": False,
                "trend_count": 3,    # 3 kaynaktan geldi → +10 bonus
                "trend_bonus": 10,
                "topic_fingerprint": generate_topic_fingerprint(
                    "Tesla Model S ve Model X üretimi durdu"
                ),
            },
            {
                "title": "Hibrit Araçlarda ÖTV İndirimi Gündemde",
                "link": "https://test.com/haber2",
                "summary": "ÖTV indirimi bu ay açıklanacak.",
                "published": (now_tr - timedelta(hours=8)).isoformat(),
                "image_url": "",
                "source_name": "Test B",
                "source_priority": "medium",
                "can_scrape_image": False,
                "trend_count": 1,    # tek kaynak, bonus yok
                "trend_bonus": 0,
                "topic_fingerprint": generate_topic_fingerprint(
                    "Hibrit Araçlarda ÖTV İndirimi Gündemde"
                ),
            },
        ]
        set_stage("fetch", "done", output={
            "articles": fake_articles,
            "count": len(fake_articles),
        })
        fetcher_success = True

    if not fetcher_success:
        log("Fetcher başarısız — test durduruluyor", "ERROR")
        sys.exit(1)

    log("\nagent_scorer çalıştırılıyor...")
    success = run()

    if success:
        score_stage = get_stage("score")
        output = score_stage.get("output", {})
        selected = output.get("selected_article", {})

        log(f"\n{'─' * 50}")
        log("SONUÇ:")
        log(f"  Başlık      : {selected.get('title', 'YOK')}")
        log(f"  Puan        : {selected.get('score', 0)}")
        log(f"  Trend       : {selected.get('trend_count', 1)} kaynak "
            f"(+{selected.get('trend_bonus', 0)} bonus)")
        log(f"  Kaynak      : {selected.get('source_name', 'YOK')}")
        log(f"{'─' * 50}")
    else:
        log("Ajan başarısız oldu", "WARNING")

    log("=== agent_scorer.py modül testi tamamlandı ===")
