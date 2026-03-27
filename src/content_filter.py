"""
content_filter.py — Kalite Kapısı + Viral Puanlama Modülü (v2 — Batch İşleme)

Bu modül haberleri YZ (yapay zeka) ile iki aşamada değerlendirir:

  AŞAMA 1 — Kalite Kapısı (Quality Gate):
    Clickbait, boş içerik, spekülatif ve düşük kaliteli haberleri eler.
    Her habere GEÇER veya RED kararı verir.

  AŞAMA 2 — Viral Puanlama (Viral Scoring):
    Kalite kapısını geçen haberleri 100 üzerinden puanlar.
    Puanlama kriterleri: ilgi çekicilik, paylaşılabilirlik,
    güncellik, özgünlük, Facebook etkileşim potansiyeli.

Akış:
  Ham haberler
    → Kalite Kapısı (clickbait / boş / spekülatif → RED)  [BATCH]
    → Viral Puanlama (her habere 0-100 puan)               [BATCH]
    → Eşik Kontrolü (düşük puanlılar elenir)
    → En İyi Haber seçilir ve döner

v2 Değişiklikler:
  - Haberler BATCH_SIZE'lık gruplar halinde YZ'ye gönderilir (token limiti aşılmaz)
  - Puanlanamayan haberlere varsayılan 0 puan verilir (50 DEĞİL)
  - Batch'ler arası rate limit gecikmesi eklendi

YZ Yanıtı Eşleştirme Stratejisi:
  Haberler YZ'ye numaralı gönderilir, yanıt "sira" + "baslik" içerir.
  1. Önce "sira" (numara) ile eşleştir
  2. Sıra yoksa / uyuşmazsa → "baslik" ile birebir eşleştir
  3. Birebir bulunamazsa → fuzzy match (benzerlik ≥ 0.6)
  4. Hiçbiri tutmazsa → atla, logla

Kullandığı modüller:
  - ai_processor.py  → ask_ai(), parse_ai_json()
  - utils.py         → load_config(), log(), get_today_post_count(),
                        get_posted_news(), is_similar_title()

Kullandığı config dosyaları:
  - config/prompts.json  → "quality_gate" ve "viral_scorer" promptları
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
0 = eşiğin altında kalır, paylaşılmaz. (Eski değer 50'ydi → hatalıydı)"""


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

    Args:
        articles: Haber dict'lerinin listesi.

    Returns:
        Numaralandırılmış, tek string halinde hazırlanmış haber metni.
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

    Args:
        articles: Bölünecek haber listesi.

    Returns:
        Her biri en fazla BATCH_SIZE elemanlı alt listelerin listesi.
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

    Eşleştirme stratejisi (sırasıyla denenir):
      1. "sira" (index numarası) ile eşleştir
      2. "baslik" alanı ile birebir (case-insensitive) eşleştir
      3. is_similar_title() ile fuzzy eşleştir (benzerlik eşiği 0.6)
      4. Hiçbiri tutmazsa → sonucu atla, uyarı logla

    Aynı article'ın birden fazla YZ sonucuyla eşleşmesi engellenir.

    Args:
        ai_results: YZ'den gelen sonuç listesi.
        articles:   Orijinal haber listesi.

    Returns:
        Eşleşen (ai_result, article) tuple'larının listesi.
    """
    matched: list[tuple] = []
    used_indices: set[int] = set()

    for ai_result in ai_results:
        matched_article: Optional[dict] = None
        matched_index: Optional[int] = None

        # ── Strateji 1: "sira" numarası ile eşleştir ──
        sira = ai_result.get("sira")
        if sira is not None:
            try:
                index: int = int(sira) - 1
                if 0 <= index < len(articles) and index not in used_indices:
                    matched_article = articles[index]
                    matched_index = index
            except (ValueError, TypeError):
                pass

        # ── Strateji 2: "baslik" ile birebir eşleştir ──
        if matched_article is None:
            ai_baslik: str = ai_result.get("baslik", "").strip()
            if ai_baslik:
                ai_baslik_lower: str = ai_baslik.lower()
                for i, article in enumerate(articles):
                    if i in used_indices:
                        continue
                    article_title: str = article.get("title", "").strip()
                    if ai_baslik_lower == article_title.lower():
                        matched_article = article
                        matched_index = i
                        break

        # ── Strateji 3: fuzzy match (benzerlik eşiği 0.6) ──
        if matched_article is None:
            ai_baslik = ai_result.get("baslik", "").strip()
            if ai_baslik:
                for i, article in enumerate(articles):
                    if i in used_indices:
                        continue
                    article_title = article.get("title", "").strip()
                    try:
                        if is_similar_title(ai_baslik, article_title, threshold=0.6):
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
# 2) Kalite Kapısı (Batch)
# ──────────────────────────────────────────────

def run_quality_gate(articles: list[dict]) -> list[dict]:
    """
    Haberleri YZ ile kalite kapısından geçirir (BATCH halinde).

    Clickbait, boş içerik, spekülatif ve düşük kaliteli haberleri eler.
    Her habere GEÇER veya RED kararı verir.

    Haberler BATCH_SIZE'lık gruplara bölünerek YZ'ye gönderilir.
    Böylece token limiti aşılmaz ve her batch tam değerlendirilir.

    Hata durumunda (YZ yanıt vermezse, JSON parse edilemezse)
    o batch'teki haberler güvenli tarafta kalınarak geçirilir.

    Args:
        articles: Değerlendirilecek haber listesi.

    Returns:
        Kalite kapısını geçen haberlerin listesi.
    """
    if not articles:
        log("ℹ️ Kalite kapısına gelen haber yok.", "INFO")
        return []

    total_count: int = len(articles)

    # ── Promptu hazırla ──
    prompts_config: dict = load_config("prompts")
    quality_prompt: str = prompts_config.get("quality_gate", "")

    if not quality_prompt:
        log(
            "⚠️ quality_gate promptu prompts.json'da bulunamadı. "
            "Tüm haberler geçiriliyor (güvenli taraf).",
            "WARNING",
        )
        return articles

    # ── Batch'lere böl ──
    batches: list[list[dict]] = _split_into_batches(articles)
    batch_count: int = len(batches)

    log(
        f"🚦 Kalite kapısı başlıyor: {total_count} haber, "
        f"{batch_count} batch ({BATCH_SIZE}'lik gruplar)",
        "INFO",
    )

    all_passed: list[dict] = []
    total_rejected: int = 0
    total_unmatched: int = 0

    for batch_num, batch in enumerate(batches, start=1):
        log(
            f"  📦 Batch {batch_num}/{batch_count}: {len(batch)} haber değerlendiriliyor...",
            "INFO",
        )

        # Haberleri numaralandır (her batch 1'den başlar)
        numbered_text: str = _format_articles_numbered(batch)
        full_prompt: str = f"{quality_prompt}\n\n{numbered_text}"

        # ── YZ'ye gönder ──
        ai_response: str = ask_ai(full_prompt)

        if not ai_response:
            log(
                f"  ⚠️ Batch {batch_num}: YZ yanıt vermedi → batch geçiriliyor",
                "WARNING",
            )
            all_passed.extend(batch)
            total_unmatched += len(batch)

            if batch_num < batch_count:
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        # ── Yanıtı parse et ──
        ai_results = parse_ai_json(ai_response)

        if not ai_results or not isinstance(ai_results, list):
            log(
                f"  ⚠️ Batch {batch_num}: JSON parse edilemedi → batch geçiriliyor",
                "WARNING",
            )
            all_passed.extend(batch)
            total_unmatched += len(batch)

            if batch_num < batch_count:
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        # ── Sonuçları eşleştir ──
        matched_pairs: list[tuple] = match_ai_results_to_articles(
            ai_results, batch
        )

        # Eşleşen article'ların id'lerini topla
        matched_ids: set[int] = {id(art) for _, art in matched_pairs}

        # ── GEÇER / RED ayrımı ──
        batch_passed: int = 0
        batch_rejected: int = 0

        for ai_result, article in matched_pairs:
            karar: str = ai_result.get("karar", "").strip().upper()
            sebep: str = ai_result.get("sebep", "Sebep belirtilmedi")
            title: str = article.get("title", "Başlık yok")

            if karar in ("GECER", "GEÇER"):
                all_passed.append(article)
                batch_passed += 1
            else:
                log(f"  ❌ RED: {title} → {sebep}", "INFO")
                batch_rejected += 1
                total_rejected += 1

        # ── YZ'nin değerlendirmediği haberler → geçir ──
        batch_unmatched: int = 0
        for article in batch:
            if id(article) not in matched_ids:
                all_passed.append(article)
                batch_unmatched += 1
                total_unmatched += 1

        log(
            f"  📦 Batch {batch_num} sonuç: "
            f"{batch_passed} GEÇER, {batch_rejected} RED"
            + (f", {batch_unmatched} değerlendirilmedi" if batch_unmatched else ""),
            "INFO",
        )

        # ── Rate limit koruması ──
        if batch_num < batch_count:
            time.sleep(BATCH_DELAY_SECONDS)

    # ── Genel sonuç özeti ──
    log(
        f"🚦 Kalite kapısı tamamlandı: {total_count} haber → "
        f"{len(all_passed)} geçti, {total_rejected} reddedildi"
        + (f", {total_unmatched} değerlendirilmedi" if total_unmatched else ""),
        "INFO",
    )

    return all_passed


# ──────────────────────────────────────────────
# 3) Viral Puanlama (Batch)
# ──────────────────────────────────────────────

def run_viral_scoring(articles: list[dict]) -> list[dict]:
    """
    Kalite kapısını geçen haberleri YZ ile viral potansiyeline göre puanlar (BATCH halinde).

    Her habere 0-100 arası puan verir. Puanlama kriterleri:
    ilgi çekicilik, paylaşılabilirlik, güncellik, özgünlük.

    Haberler BATCH_SIZE'lık gruplara bölünerek YZ'ye gönderilir.

    ÖNEMLİ: YZ'nin puanlayamadığı haberlere 0 puan verilir (eşik altında kalır).
    Eski davranış 50 puan veriyordu → puanlanmamış haberler geçiyordu. YANLIŞ.

    Args:
        articles: Kalite kapısını geçmiş haber listesi.

    Returns:
        "score" alanı eklenmiş, puanı yüksekten düşüğe sıralı haber listesi.
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

        # Haberleri numaralandır (her batch 1'den başlar)
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
# 4) Eşik Kontrolü
# ──────────────────────────────────────────────

def apply_thresholds(scored_articles: list[dict]) -> list[dict]:
    """
    Puanlanan haberlere eşik kontrolü uygular.

    Günün durumuna göre farklı eşik kullanır:
      - Sakin gün (bugün 2'den az post yapılmış): slow_day_score (varsayılan 50)
      - Normal gün (bugün 2+ post yapılmış):     publish_score  (varsayılan 65)

    Args:
        scored_articles: "score" alanı eklenmiş, puanlanmış haber listesi.

    Returns:
        Eşik üstündeki haberlerin listesi (puan sırası korunur).
    """
    if not scored_articles:
        log("ℹ️ Eşik kontrolüne gelen haber yok.", "INFO")
        return []

    total_count: int = len(scored_articles)

    # ── Eşikleri config'den oku ──
    scoring_config: dict = load_config("scoring")
    thresholds: dict = scoring_config.get("thresholds", {})
    publish_score: int = thresholds.get("publish_score", 65)
    slow_day_score: int = thresholds.get("slow_day_score", 50)

    # ── Bugün kaç post yapıldığını kontrol et ──
    posted_data: dict = get_posted_news()
    today_post_count: int = get_today_post_count(posted_data)

    # ── Eşiği belirle ──
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

    # ── Eşik uygula ──
    passed: list[dict] = []
    eliminated: int = 0

    for article in scored_articles:
        score: int = article.get("score", 0)
        title: str = article.get("title", "Başlık yok")

        if score >= threshold:
            passed.append(article)
        else:
            eliminated += 1
            # Sadece eşiğe yakın olanları logla (spam önleme)
            if score > 0:
                log(
                    f"📉 {title} — puan {score}, eşik {threshold} → elendi",
                    "INFO",
                )

    # ── Sonuç özeti ──
    log(
        f"📊 Eşik kontrolü: {total_count} haber → "
        f"{len(passed)} geçti, {eliminated} elendi (eşik: {threshold})",
        "INFO",
    )

    return passed


# ──────────────────────────────────────────────
# 5) En İyi Haber Seçimi
# ──────────────────────────────────────────────

def select_best_article(articles: list[dict]) -> Optional[dict]:
    """
    En yüksek puanlı 1 haberi seçer.

    Args:
        articles: Puanlı ve eşik üstündeki haber listesi.

    Returns:
        En yüksek puanlı haber dict'i. Liste boşsa None döner.
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
# 6) Ana Fonksiyon — Tüm Adımları Sırayla Çalıştır
# ──────────────────────────────────────────────

def filter_and_score(articles: list[dict]) -> Optional[dict]:
    """
    ANA FONKSİYON — Tüm filtreleme ve puanlama adımlarını sırayla çalıştırır.

    Akış:
      1. Kalite Kapısı    → clickbait / boş / spekülatif haberleri eler  [BATCH]
      2. Viral Puanlama   → geçenleri 100 üzerinden puanlar              [BATCH]
      3. Eşik Kontrolü    → düşük puanlıları eler
      4. En İyi Seçim     → en yüksek puanlı haberi döner

    Args:
        articles: Ham haber listesi (news_fetcher çıktısı).

    Returns:
        En iyi haber dict'i (score dahil) veya uygun haber yoksa None.
    """
    separator: str = "=" * 60

    log(separator, "INFO")
    log(f"🔍 Filtreleme ve puanlama başlıyor: {len(articles)} haber", "INFO")
    log(separator, "INFO")

    if not articles:
        log("ℹ️ Filtrelenecek haber yok.", "INFO")
        return None

    # ── ADIM 1: Kalite Kapısı ──
    log(f"\n📌 ADIM 1: Kalite Kapısı ({len(articles)} haber)", "INFO")
    quality_passed: list[dict] = run_quality_gate(articles)

    if not quality_passed:
        log(
            "❌ Kalite kapısını geçen haber yok. Bugün paylaşım yapılmayacak.",
            "INFO",
        )
        return None

    log(f"✅ Kalite kapısından {len(quality_passed)} haber geçti", "INFO")

    # ── ADIM 2: Viral Puanlama ──
    log(f"\n📌 ADIM 2: Viral Puanlama ({len(quality_passed)} haber)", "INFO")
    scored: list[dict] = run_viral_scoring(quality_passed)

    if not scored:
        log("❌ Puanlanan haber yok.", "INFO")
        return None

    log(f"✅ {len(scored)} haber puanlandı", "INFO")

    # ── ADIM 3: Eşik Kontrolü ──
    log(f"\n📌 ADIM 3: Eşik Kontrolü ({len(scored)} haber)", "INFO")
    above_threshold: list[dict] = apply_thresholds(scored)

    if not above_threshold:
        log(
            "❌ Eşik üstünde haber yok. "
            "Bugün kaliteli haber bulunamadı, paylaşım yapılmayacak.",
            "INFO",
        )
        return None

    log(f"✅ {len(above_threshold)} haber eşik üstünde", "INFO")

    # ── ADIM 4: En İyi Haberi Seç ──
    log(f"\n📌 ADIM 4: En İyi Haber Seçimi", "INFO")
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
