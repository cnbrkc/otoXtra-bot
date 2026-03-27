"""
main.py — Ana Orkestrasyon Modülü

Bu dosya otoXtra botunun ana kontrolcüsüdür.
GitHub Actions tarafından "python src/main.py" komutuyla çalıştırılır.

TEST MODU:
  Manuel çalıştırmada (Run workflow butonu) otomatik aktif olur.
  - Rastgele gecikme ATLANIR (0 saniye bekleme)
  - Rastgele atlama ATLANIR (her seferinde çalışır)
  - Minimum aralık kontrolü ATLANIR
  - Böylece test 30 saniyede biter, 7 dakika beklemezsin

Kullandığı modüller:
  - news_fetcher.py    → fetch_and_filter_news(), get_article_full_text()
  - content_filter.py  → filter_and_score()
  - ai_processor.py    → generate_post_text()
  - image_handler.py   → prepare_image()
  - facebook_poster.py → publish()
  - utils.py           → load_config(), log(), get_today_post_count(),
                          get_posted_news(), random_delay(),
                          get_turkey_now(), get_today_str()
"""

import os
import random
from datetime import datetime, timedelta
from typing import Optional

from news_fetcher import fetch_and_filter_news, get_article_full_text
from content_filter import filter_and_score
from ai_processor import generate_post_text
from image_handler import prepare_image
from facebook_poster import publish
from utils import (
    load_config,
    log,
    get_today_post_count,
    get_posted_news,
    random_delay,
    get_turkey_now,
    get_today_str,
)


def is_test_mode() -> bool:
    """
    Test modunun aktif olup olmadığını kontrol eder.

    TEST_MODE ortam değişkeni "true" ise test modu aktiftir.
    Bu değişken bot.yml'de otomatik ayarlanır:
      - "Run workflow" butonu → TEST_MODE=true
      - Zamanlanmış çalışma   → TEST_MODE=false

    Returns:
        True: Test modu aktif (hızlı çalış).
        False: Normal mod (anti-bot gecikmeleri uygula).
    """
    return os.environ.get("TEST_MODE", "false").lower() == "true"


def _check_daily_limit(settings: dict, posted_data: dict) -> bool:
    """
    Günlük paylaşım limitinin dolup dolmadığını kontrol eder.

    Args:
        settings:    settings.json içeriği.
        posted_data: posted_news.json içeriği.

    Returns:
        True: Devam edilebilir (limit dolmamış).
        False: Limit dolmuş, çıkılmalı.
    """
    today_count: int = get_today_post_count(posted_data)
    max_daily: int = settings.get("posting", {}).get("max_daily_posts", 7)

    log(f"📊 Bugün {today_count}/{max_daily} post yapıldı", "INFO")

    if today_count >= max_daily:
        log(
            f"🛑 Günlük limit doldu ({today_count}/{max_daily}). Çıkılıyor.",
            "INFO",
        )
        return False

    return True


def _check_random_skip(settings: dict) -> bool:
    """
    Anti-bot rastgele atlama kontrolü yapar.

    TEST MODUNDA: Her zaman devam eder (atlamaz).

    Args:
        settings: settings.json içeriği.

    Returns:
        True: Devam edilebilir.
        False: Bu sefer atlanacak.
    """
    if is_test_mode():
        log("🧪 TEST MODU: Rastgele atlama devre dışı", "INFO")
        return True

    skip_probability: int = settings.get("posting", {}).get(
        "skip_probability_percent", 10
    )

    dice_roll: int = random.randint(0, 99)

    if dice_roll < skip_probability:
        log(
            f"🎲 Rastgele atlama aktif (zar: {dice_roll}, "
            f"eşik: {skip_probability}%). Bu sefer paylaşılmıyor.",
            "INFO",
        )
        return False

    log(
        f"🎲 Rastgele atlama: devam (zar: {dice_roll}, "
        f"eşik: {skip_probability}%)",
        "INFO",
    )
    return True


def _check_min_interval(settings: dict, posted_data: dict) -> bool:
    """
    Son paylaşımdan yeterli süre geçip geçmediğini kontrol eder.

    TEST MODUNDA: Her zaman devam eder (beklemez).

    Args:
        settings:    settings.json içeriği.
        posted_data: posted_news.json içeriği.

    Returns:
        True: Devam edilebilir.
        False: Henüz erken.
    """
    if is_test_mode():
        log("🧪 TEST MODU: Minimum aralık kontrolü devre dışı", "INFO")
        return True

    min_interval_hours: float = settings.get("posting", {}).get(
        "min_post_interval_hours", 2
    )

    posts: list = posted_data.get("posts", [])

    if not posts:
        log("ℹ️ Daha önce paylaşım yapılmamış, devam ediliyor", "INFO")
        return True

    last_post: dict = posts[-1]
    last_posted_at_str: str = last_post.get("posted_at", "")

    if not last_posted_at_str:
        log("ℹ️ Son paylaşımın zamanı bulunamadı, devam ediliyor", "INFO")
        return True

    try:
        from dateutil import parser as date_parser

        last_posted_at: datetime = date_parser.isoparse(last_posted_at_str)
        now_turkey: datetime = get_turkey_now()
        time_diff: timedelta = now_turkey - last_posted_at
        hours_since_last: float = time_diff.total_seconds() / 3600

        if hours_since_last < min_interval_hours:
            remaining_minutes: int = int(
                (min_interval_hours - hours_since_last) * 60
            )
            log(
                f"⏰ Son paylaşımdan {hours_since_last:.1f} saat geçmiş "
                f"(minimum: {min_interval_hours} saat). "
                f"Yaklaşık {remaining_minutes} dk daha beklenecek. Çıkılıyor.",
                "INFO",
            )
            return False

        log(
            f"⏰ Son paylaşımdan {hours_since_last:.1f} saat geçmiş "
            f"(minimum: {min_interval_hours} saat). Devam ediliyor.",
            "INFO",
        )
        return True

    except (ValueError, TypeError) as e:
        log(
            f"⚠️ Son paylaşım zamanı parse edilemedi: {e}. Devam ediliyor.",
            "WARNING",
        )
        return True


def main() -> None:
    """
    otoXtra botunun ana fonksiyonu.

    TEST MODUNDA (Run workflow butonu):
      - Gecikme yok → anında çalışır
      - Atlama yok → her seferinde paylaşır
      - Aralık kontrolü yok → art arda test edilebilir

    NORMAL MODDA (zamanlanmış çalışma):
      - 0-8 dk rastgele gecikme
      - %10 rastgele atlama
      - 2 saat minimum aralık
    """
    separator: str = "═" * 60
    test_mode: bool = is_test_mode()

    try:
        # ══════════════════════════════════════════════
        # ADIM 1: BAŞLANGIÇ KONTROLLERİ
        # ══════════════════════════════════════════════
        log(separator, "INFO")
        log("🚗 otoXtra Bot Başlatılıyor", "INFO")
        if test_mode:
            log("🧪 ══ TEST MODU AKTİF — Gecikmeler devre dışı ══", "INFO")
        log(separator, "INFO")

        turkey_now: datetime = get_turkey_now()
        log(f"🕐 Türkiye saati: {turkey_now.strftime('%Y-%m-%d %H:%M:%S')}", "INFO")

        settings: dict = load_config("settings")
        log("✅ Ayarlar yüklendi", "INFO")

        posted_data: dict = get_posted_news()

        # ── Kontrol 1: Günlük limit ──
        if not _check_daily_limit(settings, posted_data):
            return

        # ── Kontrol 2: Rastgele atlama ──
        if not _check_random_skip(settings):
            return

        # ── Kontrol 3: Minimum aralık ──
        if not _check_min_interval(settings, posted_data):
            return

        # ── Rastgele gecikme ──
        if test_mode:
            log("🧪 TEST MODU: Rastgele gecikme atlandı (0 saniye)", "INFO")
        else:
            max_delay_minutes: int = settings.get("posting", {}).get(
                "random_delay_max_minutes", 8
            )
            random_delay(max_delay_minutes)

        # ══════════════════════════════════════════════
        # ADIM 2: HABER TARAMA
        # ══════════════════════════════════════════════
        log(separator, "INFO")
        log("📰 ADIM 2: Haber taramaya başlanıyor...", "INFO")
        log(separator, "INFO")

        articles: list[dict] = fetch_and_filter_news()

        if not articles:
            log("ℹ️ Uygun haber bulunamadı. Çıkılıyor.", "INFO")
            log(separator, "INFO")
            log("🏁 İşlem tamamlandı: Paylaşılacak haber yok", "INFO")
            log(separator, "INFO")
            return

        log(f"📋 {len(articles)} aday haber bulundu", "INFO")

        # ══════════════════════════════════════════════
        # ADIM 3-4: KALİTE FİLTRE + PUANLAMA
        # ══════════════════════════════════════════════
        log(separator, "INFO")
        log("🔍 ADIM 3-4: Kalite filtre ve puanlama...", "INFO")
        log(separator, "INFO")

        selected: Optional[dict] = filter_and_score(articles)

        if selected is None:
            log(
                "ℹ️ Kalite eşiğini geçen haber yok. "
                "Bugün paylaşılacak kaliteli haber bulunamadı.",
                "INFO",
            )
            log(separator, "INFO")
            log("🏁 İşlem tamamlandı: Kaliteli haber yok", "INFO")
            log(separator, "INFO")
            return

        selected_title: str = selected.get("title", "Başlık yok")
        selected_score: int = selected.get("score", 0)
        log(
            f"🏆 Seçilen haber: {selected_title} (puan: {selected_score})",
            "INFO",
        )

        # ══════════════════════════════════════════════
        # ADIM 5: İÇERİK ÜRETİMİ
        # ══════════════════════════════════════════════
        log(separator, "INFO")
        log("✍️ ADIM 5: İçerik üretimi...", "INFO")
        log(separator, "INFO")

        article_link: str = selected.get("link", "")
        if article_link:
            log("📄 Haber tam metni çekiliyor...", "INFO")
            full_text: str = get_article_full_text(article_link)
            if full_text:
                selected["full_text"] = full_text
                log(
                    f"✅ Tam metin çekildi ({len(full_text)} karakter)",
                    "INFO",
                )
            else:
                log(
                    "ℹ️ Tam metin çekilemedi, özet ile devam edilecek",
                    "INFO",
                )

        post_text: str = generate_post_text(selected)

        if not post_text:
            log("❌ İçerik üretilemedi. Çıkılıyor.", "ERROR")
            log(separator, "INFO")
            log("🏁 İşlem tamamlandı: İçerik üretim hatası", "INFO")
            log(separator, "INFO")
            return

        log("✅ Facebook post metni hazır", "INFO")

        # ══════════════════════════════════════════════
        # ADIM 6: GÖRSEL TEMİNİ
        # ══════════════════════════════════════════════
        log(separator, "INFO")
        log("🖼️ ADIM 6: Görsel temini...", "INFO")
        log(separator, "INFO")

        image_path: Optional[str] = prepare_image(selected)

        if image_path:
            log(f"✅ Görsel hazır: {image_path}", "INFO")
        else:
            log(
                "⚠️ Görsel temin edilemedi. Metin olarak paylaşılacak.",
                "WARNING",
            )

        # ══════════════════════════════════════════════
        # ADIM 7: FACEBOOK'A PAYLAŞ
        # ══════════════════════════════════════════════
        log(separator, "INFO")
        log("📣 ADIM 7: Facebook'a paylaşım...", "INFO")
        log(separator, "INFO")

        success: bool = publish(selected, post_text, image_path)

        log(separator, "INFO")
        if success:
            log("🎉 ═══ İşlem tamamlandı: BAŞARILI ═══", "INFO")
        else:
            log("😞 ═══ İşlem tamamlandı: BAŞARISIZ ═══", "WARNING")
        log(separator, "INFO")

    except KeyboardInterrupt:
        log("⚠️ Kullanıcı tarafından durduruldu (Ctrl+C)", "WARNING")

    except Exception as e:
        log(separator, "ERROR")
        log(f"💥 KRİTİK HATA: {str(e)}", "ERROR")

        import traceback
        error_details: str = traceback.format_exc()
        log(f"📋 Hata detayı:\n{error_details}", "ERROR")

        log("ℹ️ Bot bir sonraki çalışmada tekrar deneyecek.", "INFO")
        log(separator, "ERROR")


if __name__ == "__main__":
    main()
