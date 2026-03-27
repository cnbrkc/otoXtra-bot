"""
content_filter.py — Kalite Kapısı + Viral Puanlama Modülü

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
    → Kalite Kapısı (clickbait / boş / spekülatif → RED)
    → Viral Puanlama (her habere 0-100 puan)
    → Eşik Kontrolü (düşük puanlılar elenir)
    → En İyi Haber seçilir ve döner

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
# Yardımcı (private) fonksiyon
# ──────────────────────────────────────────────

def _format_articles_numbered(articles: list[dict]) -> str:
    """
    Haber listesini numaralandırılmış metin formatına çevirir.

    YZ'ye gönderilecek formatta hazırlar:
      "1. Başlık: … | Özet: …
       2. Başlık: … | Özet: …"

    Özet 300 karakterden uzunsa kırpılır (YZ token sınırını aşmasın).

    Args:
        articles: Haber dict'lerinin listesi (her birinde "title" ve "summary" beklenir).

    Returns:
        Numaralandırılmış, tek string halinde hazırlanmış haber metni.
    """
    lines: list[str] = []
    for i, article in enumerate(articles, start=1):
        title: str = article.get("title", "Başlık yok").strip()
        summary: str = article.get("summary", "Özet yok").strip()

        # Özeti kısalt — çok uzun özetler YZ token limitini gereksiz tüketir
        max_summary_len: int = 300
        if len(summary) > max_summary_len:
            summary = summary[: max_summary_len - 3] + "..."

        lines.append(f"{i}. Başlık: {title} | Özet: {summary}")

    return "\n".join(lines)


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
                    Her dict en az "sira" ve/veya "baslik" alanı içerir.
        articles:   Orijinal haber listesi (news_fetcher çıktısı).

    Returns:
        Eşleşen (ai_result, article) tuple'larının listesi.
    """
    matched: list[tuple] = []
    used_indices: set[int] = set()  # Aynı article'ın tekrar eşleşmesini önle

    for ai_result in ai_results:
        matched_article: Optional[dict] = None
        matched_index: Optional[int] = None

        # ── Strateji 1: "sira" numarası ile eşleştir ──────────────────
        sira = ai_result.get("sira")
        if sira is not None:
            try:
                index: int = int(sira) - 1  # sira 1'den başlar, Python index 0'dan
                if 0 <= index < len(articles) and index not in used_indices:
                    matched_article = articles[index]
                    matched_index = index
            except (ValueError, TypeError):
                # sira sayısal değilse diğer stratejilere devam et
                pass

        # ── Strateji 2: "baslik" ile birebir eşleştir (case-insensitive) ─
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

        # ── Strateji 3: fuzzy match (benzerlik eşiği 0.6) ─────────────
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
                        # is_similar_title beklenmeyen hata verirse devam et
                        continue

        # ── Eşleştirme sonucu ─────────────────────────────────────────
        if matched_article is not None and matched_index is not None:
            matched.append((ai_result, matched_article))
            used_indices.add(matched_index)
        else:
            kaybolan_baslik: str = ai_result.get("baslik", "(başlık yok)")
            log(f"⚠️ Eşleştirilemeyen YZ sonucu: {kaybolan_baslik}", "WARNING")

    return matched


# ──────────────────────────────────────────────
# 2) Kalite Kapısı
# ──────────────────────────────────────────────

def run_quality_gate(articles: list[dict]) -> list[dict]:
    """
    Haberleri YZ ile kalite kapısından geçirir.

    Clickbait, boş içerik, spekülatif ve düşük kaliteli haberleri eler.
    Her habere GEÇER veya RED kararı verir.

    İşleyiş:
      1. Haberleri numaralandırılmış metin olarak hazırla
      2. prompts.json'dan "quality_gate" promptunu oku
      3. Haberleri prompt sonuna ekle, YZ'ye gönder
      4. YZ yanıtını JSON olarak parse et
      5. Sonuçları orijinal haberlerle eşleştir
      6. GEÇER olanları döndür, RED olanları logla

    Hata durumunda (YZ yanıt vermezse, JSON parse edilemezse)
    güvenli tarafta kalınır ve tüm haberler geçirilir.

    Args:
        articles: Değerlendirilecek haber listesi.

    Returns:
        Kalite kapısını geçen haberlerin listesi.
    """
    if not articles:
        log("ℹ️ Kalite kapısına gelen haber yok.", "INFO")
        return []

    total_count: int = len(articles)
    log(f"🚦 Kalite kapısı başlıyor: {total_count} haber değerlendirilecek", "INFO")

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

    # Haberleri numaralandır ve promptun sonuna ekle
    numbered_text: str = _format_articles_numbered(articles)
    full_prompt: str = f"{quality_prompt}\n\n{numbered_text}"

    # ── YZ'ye gönder ──
    ai_response: str = ask_ai(full_prompt)

    if not ai_response:
        log(
            "⚠️ Kalite kapısı: YZ yanıt vermedi. "
            "Tüm haberler geçiriliyor (güvenli taraf).",
            "WARNING",
        )
        return articles

    # ── Yanıtı parse et ──
    ai_results = parse_ai_json(ai_response)

    if not ai_results or not isinstance(ai_results, list):
        log(
            "⚠️ Kalite kapısı: YZ yanıtı JSON olarak parse edilemedi. "
            "Tüm haberler geçiriliyor (güvenli taraf).",
            "WARNING",
        )
        return articles

    # ── Sonuçları orijinal haberlerle eşleştir ──
    matched_pairs: list[tuple] = match_ai_results_to_articles(ai_results, articles)

    # ── GEÇER / RED ayrımı ──
    passed: list[dict] = []
    rejected_count: int = 0

    # Eşleşen article'ların link'lerini takip et
    matched_article_links: set[str] = set()

    for ai_result, article in matched_pairs:
        karar: str = ai_result.get("karar", "").strip().upper()
        sebep: str = ai_result.get("sebep", "Sebep belirtilmedi")
        title: str = article.get("title", "Başlık yok")
        article_link: str = article.get("link", "")

        matched_article_links.add(article_link)

        # Türkçe karakter uyumu: hem "GECER" hem "GEÇER" kabul et
        if karar in ("GECER", "GEÇER"):
            passed.append(article)
        else:
            # RED veya tanınmayan karar → haber reddedildi
            log(f"❌ RED: {title} → {sebep}", "INFO")
            rejected_count += 1

    # ── YZ'nin değerlendirmediği haberler ──
    # YZ bazı haberleri yanıtta atlamış olabilir.
    # Güvenli tarafta kal: değerlendirilmemiş haberleri de geçir.
    unmatched_count: int = 0
    for article in articles:
        article_link: str = article.get("link", "")
        if article_link and article_link not in matched_article_links:
            passed.append(article)
            unmatched_count += 1
            log(
                f"ℹ️ YZ değerlendirmedi, geçiriliyor: "
                f"{article.get('title', 'Başlık yok')}",
                "INFO",
            )
        elif not article_link:
            # Link'i olmayan article — ID ile kontrol
            already_matched: bool = any(
                art is article for _, art in matched_pairs
            )
            if not already_matched:
                passed.append(article)
                unmatched_count += 1
                log(
                    f"ℹ️ YZ değerlendirmedi, geçiriliyor: "
                    f"{article.get('title', 'Başlık yok')}",
                    "INFO",
                )

    # ── Sonuç özeti ──
    log(
        f"🚦 Kalite kapısı: {total_count} haber geldi → "
        f"{len(passed)} geçti, {rejected_count} reddedildi"
        + (f", {unmatched_count} YZ tarafından değerlendirilmedi" if unmatched_count else ""),
        "INFO",
    )

    return passed


# ──────────────────────────────────────────────
# 3) Viral Puanlama
# ──────────────────────────────────────────────

def run_viral_scoring(articles: list[dict]) -> list[dict]:
    """
    Kalite kapısını geçen haberleri YZ ile viral potansiyeline göre puanlar.

    Her habere 0-100 arası puan verir. Puanlama kriterleri:
    ilgi çekicilik, paylaşılabilirlik, güncellik, özgünlük.

    İşleyiş:
      1. Haberleri numaralandırılmış metin olarak hazırla
      2. prompts.json'dan "viral_scorer" promptunu oku
      3. Haberleri prompt sonuna ekle, YZ'ye gönder
      4. YZ yanıtını JSON olarak parse et
      5. Sonuçları orijinal haberlerle eşleştir
      6. Her habere "score" alanı ekle, yüksekten düşüğe sırala

    Hata durumunda güvenli tarafta kalınır: varsayılan puan 50 verilir.

    Args:
        articles: Kalite kapısını geçmiş haber listesi.

    Returns:
        "score" alanı eklenmiş, puanı yüksekten düşüğe sıralı haber listesi.
    """
    if not articles:
        log("ℹ️ Viral puanlamaya gelen haber yok.", "INFO")
        return []

    total_count: int = len(articles)
    default_score: int = 50

    log(f"📊 Viral puanlama başlıyor: {total_count} haber puanlanacak", "INFO")

    # ── Promptu hazırla ──
    prompts_config: dict = load_config("prompts")
    scorer_prompt: str = prompts_config.get("viral_scorer", "")

    if not scorer_prompt:
        log(
            "⚠️ viral_scorer promptu prompts.json'da bulunamadı. "
            f"Tüm haberlere varsayılan puan ({default_score}) veriliyor.",
            "WARNING",
        )
        for article in articles:
            article["score"] = default_score
        return articles

    # Haberleri numaralandır ve promptun sonuna ekle
    numbered_text: str = _format_articles_numbered(articles)
    full_prompt: str = f"{scorer_prompt}\n\n{numbered_text}"

    # ── YZ'ye gönder ──
    ai_response: str = ask_ai(full_prompt)

    if not ai_response:
        log(
            f"⚠️ Viral puanlama: YZ yanıt vermedi. "
            f"Varsayılan puan ({default_score}) veriliyor.",
            "WARNING",
        )
        for article in articles:
            article["score"] = default_score
        return articles

    # ── Yanıtı parse et ──
    ai_results = parse_ai_json(ai_response)

    if not ai_results or not isinstance(ai_results, list):
        log(
            f"⚠️ Viral puanlama: YZ yanıtı JSON olarak parse edilemedi. "
            f"Varsayılan puan ({default_score}) veriliyor.",
            "WARNING",
        )
        for article in articles:
            article["score"] = default_score
        return articles

    # ── Sonuçları orijinal haberlerle eşleştir ──
    matched_pairs: list[tuple] = match_ai_results_to_articles(ai_results, articles)

    # ── Puanları ata ──
    scored_articles: list[dict] = []
    scored_article_links: set[str] = set()

    for ai_result, article in matched_pairs:
        # Puanı oku ve geçerli aralığa zorla (0-100)
        try:
            puan: int = int(ai_result.get("puan", default_score))
            puan = max(0, min(100, puan))
        except (ValueError, TypeError):
            puan = default_score

        gerekce: str = ai_result.get("gerekce", "Gerekçe belirtilmedi")
        title: str = article.get("title", "Başlık yok")

        article["score"] = puan
        scored_articles.append(article)
        scored_article_links.add(article.get("link", ""))

        log(f"📊 Puan: {puan:3d}/100 — {title} → {gerekce}", "INFO")

    # ── YZ'nin puanlamadığı haberlere varsayılan puan ver ──
    for article in articles:
        article_link: str = article.get("link", "")
        already_scored: bool = False

        if article_link:
            already_scored = article_link in scored_article_links
        else:
            already_scored = any(art is article for _, art in matched_pairs)

        if not already_scored:
            article["score"] = default_score
            scored_articles.append(article)
            log(
                f"ℹ️ YZ puanlamadı, varsayılan {default_score} veriliyor: "
                f"{article.get('title', 'Başlık yok')}",
                "INFO",
            )

    # ── Puanı yüksekten düşüğe sırala ──
    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)

    log(f"📊 Viral puanlama tamamlandı: {len(scored_articles)} haber puanlandı", "INFO")

    return scored_articles


# ──────────────────────────────────────────────
# 4) Eşik Kontrolü
# ──────────────────────────────────────────────

def apply_thresholds(scored_articles: list[dict]) -> list[dict]:
    """
    Puanlanan haberlere eşik kontrolü uygular.

    Günün durumuna göre farklı eşik kullanır:
      - Sakin gün (bugün 2'den az post yapılmış): slow_day_score (varsayılan 50)
      - Normal gün (bugün 2+ post yapılmış):     publish_score  (varsayılan 65)

    Bu sayede sakin günlerde daha düşük puanlı haberler de paylaşılabilir,
    yoğun günlerde ise sadece en kaliteli haberler paylaşılır.

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
    today_post_count: int = get_today_post_count()

    # ── Eşiği belirle ──
    if today_post_count < 2:
        # Sakin gün: daha düşük eşik uygula (daha fazla haber geçsin)
        threshold: int = slow_day_score
        log(
            f"📅 Sakin gün (bugün {today_post_count} post yapılmış). "
            f"Düşük eşik kullanılıyor: {threshold}",
            "INFO",
        )
    else:
        # Normal/yoğun gün: standart eşik uygula
        threshold = publish_score
        log(
            f"📅 Normal gün (bugün {today_post_count} post yapılmış). "
            f"Standart eşik kullanılıyor: {threshold}",
            "INFO",
        )

    # ── Eşik uygula ──
    passed: list[dict] = []

    for article in scored_articles:
        score: int = article.get("score", 0)
        title: str = article.get("title", "Başlık yok")

        if score >= threshold:
            passed.append(article)
        else:
            log(f"📉 {title} puanı {score}, eşik ({threshold}) altı — elendi", "INFO")

    # ── Sonuç özeti ──
    log(
        f"📊 Eşik kontrolü: {total_count} haber geldi, "
        f"{len(passed)} geçti (eşik: {threshold})",
        "INFO",
    )

    return passed


# ──────────────────────────────────────────────
# 5) En İyi Haber Seçimi
# ──────────────────────────────────────────────

def select_best_article(articles: list[dict]) -> Optional[dict]:
    """
    En yüksek puanlı 1 haberi seçer.

    Liste zaten sıralı gelmeli ama güvenlik için max() kullanılır.

    Args:
        articles: Puanlı ve eşik üstündeki haber listesi.

    Returns:
        En yüksek puanlı haber dict'i. Liste boşsa None döner.
    """
    if not articles:
        log("ℹ️ Seçilecek haber yok — tüm haberler elendi.", "INFO")
        return None

    # Güvenlik: listeyi tekrar kontrol et, en yüksek puanlıyı bul
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
      1. Kalite Kapısı    → clickbait / boş / spekülatif haberleri eler
      2. Viral Puanlama   → geçenleri 100 üzerinden puanlar
      3. Eşik Kontrolü    → düşük puanlıları eler
      4. En İyi Seçim     → en yüksek puanlı haberi döner

    Her adımda kaç haber kaldığı loglanır.
    Hiçbir haber kalmazsa None döner (bugün paylaşım yapılmaz).

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

    # ── ADIM 1: Kalite Kapısı ─────────────────────────────────────
    log(f"\n📌 ADIM 1: Kalite Kapısı ({len(articles)} haber)", "INFO")
    quality_passed: list[dict] = run_quality_gate(articles)

    if not quality_passed:
        log(
            "❌ Kalite kapısını geçen haber yok. "
            "Bugün paylaşım yapılmayacak.",
            "INFO",
        )
        return None

    log(f"✅ Kalite kapısından {len(quality_passed)} haber geçti", "INFO")

    # ── ADIM 2: Viral Puanlama ────────────────────────────────────
    log(f"\n📌 ADIM 2: Viral Puanlama ({len(quality_passed)} haber)", "INFO")
    scored: list[dict] = run_viral_scoring(quality_passed)

    if not scored:
        log("❌ Puanlanan haber yok.", "INFO")
        return None

    log(f"✅ {len(scored)} haber puanlandı", "INFO")

    # ── ADIM 3: Eşik Kontrolü ────────────────────────────────────
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

    # ── ADIM 4: En İyi Haberi Seç ────────────────────────────────
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
