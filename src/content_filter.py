"""
content_filter.py — Viral Puanlama Modülü (v4 — Tek Aşamalı Hızlı Puanlama)

Bu modül haberleri YZ (yapay zeka) ile tek aşamada değerlendirir:

  AŞAMA 1 — Viral Puanlama (Viral Scoring):
    Haberleri 100 üzerinden puanlar.
    Puanlama kriterleri: ilgi çekicilik, paylaşılabilirlik,
    güncellik, özgünlük, Facebook etkileşim potansiyeli.

Akış:
  Ham haberler (Zaten güvenilir kaynaklardan çekilmiş)
    → Viral Puanlama (her habere 0-100 puan)               [BATCH]
    → Eşik Kontrolü (düşük puanlılar elenir)
    → En İyi Haber seçilir ve döner

v4 Değişiklikler:
  - "Kalite Kapısı" (Quality Gate) tamamen KILDIRILDI.
  - Güvenilir kaynaklar kullanıldığı için YZ sorgu sayısı yarıya indirildi.
  - Haberler doğrudan Viral Puanlamaya girer.

v3 Değişiklikler:
  - match_ai_results_to_articles() güçlendirildi:
    Sıra numarası ile eşleşirken başlık ÇAPRAZ DOĞRULAMASI yapılır.

Kullandığı modüller:
  - ai_processor.py  → ask_ai(), parse_ai_json()
  - utils.py         → load_config(), log(), get_today_post_count(),
                        get_posted_news(), is_similar_title()

Kullandığı config dosyaları:
  - config/prompts.json  → "viral_scorer" promptu
  - config/scoring.json  → "thresholds" (publish_score, slow_day_score)
"""

import time
from typing import Optional

from ai_processor import ask_ai, parse_ai_json
from utils import (
    load_config,
    log,
    get_today_post_count,
    get_posted_news,
    is_similar_title,
)


# ──────────────────────────────────────────────
# Sabitler
# ──────────────────────────────────────────────

BATCH_SIZE: int = 20
"""Her seferinde YZ'ye gönderilecek maksimum haber sayısı.
20 haber ≈ 1500 output token → 2048 max_tokens limitinin altında kalır."""

BATCH_DELAY_SECONDS: int = 3
"""Batch'ler arası bekleme süresi (saniye). Groq rate limit: 30 req/dk."""

UNSCORED_DEFAULT: int = 0
"""YZ'nin puanlayamadığı haberlere verilen varsayılan puan.
0 = eşiğin altında kalır, paylaşılmaz."""

CROSS_VALIDATE_THRESHOLD: float = 0.4
"""Sıra numarası ile eşleşirken başlık çapraz doğrulama eşiği.
Bu değerin altında benzerlik varsa sıra numarası güvenilmez kabul edilir."""


# ──────────────────────────────────────────────
# Yardımcı (private) fonksiyonlar
# ──────────────────────────────────────────────

def _format_articles_numbered(articles: list[dict]) -> str:
    """
    Haber listesini numaralandırılmış metin formatına çevirir.

    YZ'ye gönderilecek formatta hazırlar:
      "1. Başlık: … | Özet: …
       2. Başlık: … | Özet: …"

    Özet 300 karakterden uzunsa kırpılır (YZ token sınırını aşmasın).
    """
    lines: list[str] = []
    for i, article in enumerate(articles, start=1):
        title: str = article.get("title", "Başlık yok").strip()
        summary: str = article.get("summary", "Özet yok").strip()

        max_summary_len: int = 300
        if len(summary) > max_summary_len:
            summary = summary[: max_summary_len - 3] + "..."

        lines.append(f"{i}. Başlık: {title} | Özet: {summary}")

    return "\n".join(lines)


def _split_into_batches(articles: list[dict]) -> list[list[dict]]:
    """
    Haber listesini BATCH_SIZE'lık gruplara böler.
    """
    return [
        articles[i: i + BATCH_SIZE]
        for i in range(0, len(articles), BATCH_SIZE)
    ]


# ──────────────────────────────────────────────
# 1) YZ Sonuçlarını Haberlerle Eşleştirme
# ──────────────────────────────────────────────

def match_ai_results_to_articles(
    ai_results: list[dict],
    articles: list[dict],
) -> list[tuple]:
    """
    YZ'den gelen sonuç listesini orijinal haber article'larıyla eşleştirir.
    """
    matched: list[tuple] = []
    used_indices: set[int] = set()

    for ai_result in ai_results:
        matched_article: Optional[dict] = None
        matched_index: Optional[int] = None

        # ── Strateji 1: "sira" numarası ile eşleştir + çapraz doğrulama ──
        sira = ai_result.get("sira")
        if sira is not None:
            try:
                index: int = int(sira) - 1
                if 0 <= index < len(articles) and index not in used_indices:
                    ai_baslik: str = ai_result.get("baslik", "").strip()
                    article_title: str = articles[index].get("title", "").strip()

                    if not ai_baslik:
                        matched_article = articles[index]
                        matched_index = index
                    elif is_similar_title(ai_baslik, article_title, threshold=CROSS_VALIDATE_THRESHOLD):
                        matched_article = articles[index]
                        matched_index = index
                    else:
                        log(
                            f"  ⚠️ ÇAPRAZ DOĞRULAMA: Sıra {sira} ile başlık uyuşmuyor → "
                            f"sıra atlanıyor, başlık eşleştirmesine düşülüyor | "
                            f"YZ: '{ai_baslik[:50]}' vs Gerçek: '{article_title[:50]}'",
                            "WARNING",
                        )
            except (ValueError, TypeError):
                pass

        # ── Strateji 2: "baslik" ile birebir eşleştir ──
        if matched_article is None:
            ai_baslik_str: str = ai_result.get("baslik", "").strip()
            if ai_baslik_str:
                ai_baslik_lower: str = ai_baslik_str.lower()
                for i, article in enumerate(articles):
                    if i in used_indices:
                        continue
                    article_title_str: str = article.get("title", "").strip()
                    if ai_baslik_lower == article_title_str.lower():
                        matched_article = article
                        matched_index = i
                        break

        # ── Strateji 3: fuzzy match (benzerlik eşiği 0.6) ──
        if matched_article is None:
            ai_baslik_fuzzy: str = ai_result.get("baslik", "").strip()
            if ai_baslik_fuzzy:
                for i, article in enumerate(articles):
                    if i in used_indices:
                        continue
                    article_title_fuzzy: str = article.get("title", "").strip()
                    try:
                        if is_similar_title(ai_baslik_fuzzy, article_title_fuzzy, threshold=0.6):
                            matched_article = article
                            matched_index = i
                            break
                    except Exception:
                        continue

        # ── Eşleştirme sonucu ──
        if matched_article is not None and matched_index is not None:
            matched.append((ai_result, matched_article))
            used_indices.add(matched_index)
        else:
            kaybolan: str = ai_result.get("baslik", "(başlık yok)")
            log(f"⚠️ Eşleştirilemeyen YZ sonucu: {kaybolan}", "WARNING")

    return matched


# ──────────────────────────────────────────────
# 2) Viral Puanlama (Batch)
# ──────────────────────────────────────────────

def run_viral_scoring(articles: list[dict]) -> list[dict]:
    """
    Haberleri YZ ile viral potansiyeline göre puanlar (BATCH halinde).

    Her habere 0-100 arası puan verir. Puanlama kriterleri:
    ilgi çekicilik, paylaşılabilirlik, güncellik, özgünlük.

    Haberler BATCH_SIZE'lık gruplara bölünerek YZ'ye gönderilir.
    """
    if not articles:
        log("ℹ️ Viral puanlamaya gelen haber yok.", "INFO")
        return []

    total_count: int = len(articles)

    # ── Promptu hazırla ──
    prompts_config: dict = load_config("prompts")
    scorer_prompt: str = prompts_config.get("viral_scorer", "")

    if not scorer_prompt:
        log(
            "⚠️ viral_scorer promptu prompts.json'da bulunamadı. "
            f"Tüm haberlere varsayılan puan ({UNSCORED_DEFAULT}) veriliyor.",
            "WARNING",
        )
        for article in articles:
            article["score"] = UNSCORED_DEFAULT
        return articles

    # ── Batch'lere böl ──
    batches: list[list[dict]] = _split_into_batches(articles)
    batch_count: int = len(batches)

    log(
        f"📊 Viral puanlama başlıyor: {total_count} haber, "
        f"{batch_count} batch ({BATCH_SIZE}'lik gruplar)",
        "INFO",
    )

    all_scored: list[dict] = []
    total_ai_scored: int = 0
    total_unscored: int = 0

    for batch_num, batch in enumerate(batches, start=1):
        log(
            f"  📦 Batch {batch_num}/{batch_count}: {len(batch)} haber puanlanıyor...",
            "INFO",
        )

        numbered_text: str = _format_articles_numbered(batch)
        full_prompt: str = f"{scorer_prompt}\n\n{numbered_text}"

        # ── YZ'ye gönder ──
        ai_response: str = ask_ai(full_prompt)

        if not ai_response:
            log(
                f"  ⚠️ Batch {batch_num}: YZ yanıt vermedi → "
                f"tüm haberlere {UNSCORED_DEFAULT} puan veriliyor",
                "WARNING",
            )
            for article in batch:
                article["score"] = UNSCORED_DEFAULT
                all_scored.append(article)
            total_unscored += len(batch)

            if batch_num < batch_count:
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        # ── Yanıtı parse et ──
        ai_results = parse_ai_json(ai_response)

        if not ai_results or not isinstance(ai_results, list):
            log(
                f"  ⚠️ Batch {batch_num}: JSON parse edilemedi → "
                f"tüm haberlere {UNSCORED_DEFAULT} puan veriliyor",
                "WARNING",
            )
            for article in batch:
                article["score"] = UNSCORED_DEFAULT
                all_scored.append(article)
            total_unscored += len(batch)

            if batch_num < batch_count:
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        # ── Sonuçları eşleştir ──
        matched_pairs: list[tuple] = match_ai_results_to_articles(
            ai_results, batch
        )

        matched_ids: set[int] = {id(art) for _, art in matched_pairs}

        # ── Puanları ata ──
        batch_scored: int = 0

        for ai_result, article in matched_pairs:
            try:
                puan: int = int(ai_result.get("puan", UNSCORED_DEFAULT))
                puan = max(0, min(100, puan))
            except (ValueError, TypeError):
                puan = UNSCORED_DEFAULT

            gerekce: str = ai_result.get("gerekce", "Gerekçe belirtilmedi")
            title: str = article.get("title", "Başlık yok")

            article["score"] = puan
            all_scored.append(article)
            batch_scored += 1
            total_ai_scored += 1

            log(f"  📊 Puan: {puan:3d}/100 — {title} → {gerekce}", "INFO")

        # ── YZ'nin puanlamadığı haberlere 0 puan ──
        batch_unscored: int = 0
        for article in batch:
            if id(article) not in matched_ids:
                article["score"] = UNSCORED_DEFAULT
                all_scored.append(article)
                batch_unscored += 1
                total_unscored += 1

        if batch_unscored > 0:
            log(
                f"  ℹ️ Batch {batch_num}: {batch_unscored} haber YZ tarafından "
                f"puanlanamadı → {UNSCORED_DEFAULT} puan verildi",
                "INFO",
            )

        log(
            f"  📦 Batch {batch_num} sonuç: "
            f"{batch_scored} puanlandı, {batch_unscored} puanlanamadı",
            "INFO",
        )

        # ── Rate limit koruması ──
        if batch_num < batch_count:
            time.sleep(BATCH_DELAY_SECONDS)

    # ── Puanı yüksekten düşüğe sırala ──
    all_scored.sort(key=lambda x: x.get("score", 0), reverse=True)

    # ── Genel sonuç özeti ──
    log(
        f"📊 Viral puanlama tamamlandı: {total_count} haber → "
        f"{total_ai_scored} YZ puanladı, {total_unscored} puanlanamadı ({UNSCORED_DEFAULT} verildi)",
        "INFO",
    )

    # Top 5'i logla
    top_n: int = min(5, len(all_scored))
    if top_n > 0:
        log(f"🏅 En yüksek puanlı {top_n} haber:", "INFO")
        for i, art in enumerate(all_scored[:top_n], start=1):
            log(
                f"  {i}. [{art.get('score', 0):3d}] {art.get('title', 'Başlık yok')}",
                "INFO",
            )

    return all_scored


# ──────────────────────────────────────────────
# 3) Eşik Kontrolü
# ──────────────────────────────────────────────

def apply_thresholds(scored_articles: list[dict]) -> list[dict]:
    """
    Puanlanan haberlere eşik kontrolü uygular.
    """
    if not scored_articles:
        log("ℹ️ Eşik kontrolüne gelen haber yok.", "INFO")
        return []

    total_count: int = len(scored_articles)

    scoring_config: dict = load_config("scoring")
    thresholds: dict = scoring_config.get("thresholds", {})
    publish_score: int = thresholds.get("publish_score", 65)
    slow_day_score: int = thresholds.get("slow_day_score", 50)

    posted_data: dict = get_posted_news()
    today_post_count: int = get_today_post_count(posted_data)

    if today_post_count < 2:
        threshold: int = slow_day_score
        log(
            f"📅 Sakin gün (bugün {today_post_count} post yapılmış). "
            f"Düşük eşik kullanılıyor: {threshold}",
            "INFO",
        )
    else:
        threshold = publish_score
        log(
            f"📅 Normal gün (bugün {today_post_count} post yapılmış). "
            f"Standart eşik kullanılıyor: {threshold}",
            "INFO",
        )

    passed: list[dict] = []
    eliminated: int = 0

    for article in scored_articles:
        score: int = article.get("score", 0)
        title: str = article.get("title", "Başlık yok")

        if score >= threshold:
            passed.append(article)
        else:
            eliminated += 1
            if score > 0:
                log(
                    f"📉 {title} — puan {score}, eşik {threshold} → elendi",
                    "INFO",
                )

    log(
        f"📊 Eşik kontrolü: {total_count} haber → "
        f"{len(passed)} geçti, {eliminated} elendi (eşik: {threshold})",
        "INFO",
    )

    return passed


# ──────────────────────────────────────────────
# 4) En İyi Haber Seçimi
# ──────────────────────────────────────────────

def select_best_article(articles: list[dict]) -> Optional[dict]:
    """
    En yüksek puanlı 1 haberi seçer.
    """
    if not articles:
        log("ℹ️ Seçilecek haber yok — tüm haberler elendi.", "INFO")
        return None

    best: dict = max(articles, key=lambda x: x.get("score", 0))

    best_title: str = best.get("title", "Başlık yok")
    best_score: int = best.get("score", 0)

    log(f"🏆 SEÇİLDİ: {best_title} (puan: {best_score})", "INFO")

    return best


# ──────────────────────────────────────────────
# 5) Ana Fonksiyon — Tüm Adımları Sırayla Çalıştır
# ──────────────────────────────────────────────

def filter_and_score(articles: list[dict]) -> Optional[dict]:
    """
    ANA FONKSİYON — Filtreleme (kaldırıldı) ve puanlama adımlarını sırayla çalıştırır.

    Akış:
      1. Viral Puanlama   → haberleri 100 üzerinden puanlar              [BATCH]
      2. Eşik Kontrolü    → düşük puanlıları eler
      3. En İyi Seçim     → en yüksek puanlı haberi döner
    """
    separator: str = "=" * 60

    log(separator, "INFO")
    log(f"🔍 Puanlama başlıyor: {len(articles)} haber", "INFO")
    log(separator, "INFO")

    if not articles:
        log("ℹ️ Puanlanacak haber yok.", "INFO")
        return None

    # ── ADIM 1: Viral Puanlama (Kalite kapısı atlandı) ──
    log(f"\n📌 ADIM 1: Viral Puanlama ({len(articles)} haber)", "INFO")
    scored: list[dict] = run_viral_scoring(articles)

    if not scored:
        log("❌ Puanlanan haber yok.", "INFO")
        return None

    log(f"✅ {len(scored)} haber puanlandı", "INFO")

    # ── ADIM 2: Eşik Kontrolü ──
    log(f"\n📌 ADIM 2: Eşik Kontrolü ({len(scored)} haber)", "INFO")
    above_threshold: list[dict] = apply_thresholds(scored)

    if not above_threshold:
        log(
            "❌ Eşik üstünde haber yok. "
            "Bugün kaliteli haber bulunamadı, paylaşım yapılmayacak.",
            "INFO",
        )
        return None

    log(f"✅ {len(above_threshold)} haber eşik üstünde", "INFO")

    # ── ADIM 3: En İyi Haberi Seç ──
    log(f"\n📌 ADIM 3: En İyi Haber Seçimi", "INFO")
    best: Optional[dict] = select_best_article(above_threshold)

    # ── Sonuç özeti ──
    log(f"\n{separator}", "INFO")
    if best:
        log(
            f"🎯 SONUÇ: '{best.get('title', 'Başlık yok')}' "
            f"puanı {best.get('score', 0)} ile seçildi",
            "INFO",
        )
    else:
        log("🎯 SONUÇ: Paylaşılacak uygun haber bulunamadı", "INFO")
    log(separator, "INFO")

    return best
