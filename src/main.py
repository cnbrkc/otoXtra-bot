"""
main.py — Ana Orkestrasyon Modülü

Bu dosya otoXtra botunun ana kontrolcüsüdür.
GitHub Actions tarafından "python src/main.py" komutuyla çalıştırılır.

Tüm modülleri sırayla çağırarak şu akışı yönetir:
  1. Başlangıç kontrolleri (günlük limit, anti-bot, min aralık)
  2. Haber tarama (RSS/Google News'ten çekme + temel filtreleme)
  3. Kalite kapısı + viral puanlama (YZ ile değerlendirme)
  4. İçerik üretimi (YZ ile Facebook post metni yazma)
  5. Görsel temini (scraping veya YZ ile üretim + logo ekleme)
  6. Facebook'a paylaşım (Graph API ile post + kayıt tutma)

Anti-bot stratejisi:
  - Rastgele gecikme (0-8 dk) → paylaşım saati her seferinde farklı
  - Rastgele atlama (%10) → her tetiklemede paylaşım garanti değil
  - Minimum aralık (2 saat) → art arda paylaşım engellenir
  - Değişken üslup → YZ her seferinde farklı tonda yazar

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
            f"🛑 Günlük limit doldu ({today_count}/{max_daily}). "
            "Çıkılıyor.",
            "INFO",
        )
        return False

    return True


def _check_random_skip(settings: dict) -> bool:
    """
    Anti-bot rastgele atlama kontrolü yapar.

    Belirli bir olasılıkla (varsayılan %10) paylaşım yapmadan çıkar.
    Bu, botun daha doğal görünmesini sağlar.

    Args:
        settings: settings.json içeriği.

    Returns:
        True: Devam edilebilir (atlanmadı).
        False: Bu sefer atlanacak, çıkılmalı.
    """
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

    Art arda paylaşım yapılmasını engeller (varsayılan minimum 2 saat).
    Bu hem anti-bot stratejisi hem de içerik kalitesi için önemlidir.

    Args:
        settings:    settings.json içeriği.
        posted_data: posted_news.json içeriği.

    Returns:
        True: Devam edilebilir (yeterli süre geçmiş).
        False: Henüz erken, çıkılmalı.
    """
    min_interval_hours: float = settings.get("posting", {}).get(
        "min_post_interval_hours", 2
    )

    posts: list = posted_data.get("posts", [])

    if not posts:
        log("ℹ️ Daha önce paylaşım yapılmamış, devam ediliyor", "INFO")
        return True

    # Son paylaşımın zamanını al
    last_post: dict = posts[-1]
    last_posted_at_str: str = last_post.get("posted_at", "")

    if not last_posted_at_str:
        log("ℹ️ Son paylaşımın zamanı bulunamadı, devam ediliyor", "INFO")
        return True

    try:
        # ISO format parse et
        # python-dateutil ile esnek parse
        from dateutil import parser as date_parser

        last_posted_at: datetime = date_parser.isoparse(last_posted_at_str)

        # Şu anki Türkiye zamanı
        now_turkey: datetime = get_turkey_now()

        # Zaman farkı
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
            f"⚠️ Son paylaşım zamanı parse edilemedi: {e}. "
            "Devam ediliyor.",
            "WARNING",
        )
        return True


def main() -> None:
    """
    otoXtra botunun ana fonksiyonu.

    Tüm adımları sırayla çalıştırır:
      1. Başlangıç kontrolleri (limit, anti-bot, min aralık)
      2. Haber tarama
      3. Kalite filtre + puanlama
      4. İçerik üretimi
      5. Görsel temini
      6. Facebook paylaşımı

    Bu fonksiyon hiçbir zaman exception fırlatmaz (graceful çıkış).
    Tüm hatalar loglanır ama program çökmez.
    """
    separator: str = "═" * 60

    try:
        # ══════════════════════════════════════════════
        # ADIM 1: BAŞLANGIÇ KONTROLLERİ
        # ══════════════════════════════════════════════
        log(separator, "INFO")
        log("🚗 otoXtra Bot Başlatılıyor", "INFO")
        log(separator, "INFO")

        # Şu anki Türkiye zamanını göster
        turkey_now: datetime = get_turkey_now()
        log(f"🕐 Türkiye saati: {turkey_now.strftime('%Y-%m-%d %H:%M:%S')}", "INFO")

        # Ayarları yükle
        settings: dict = load_config("settings")
        log("✅ Ayarlar yüklendi", "INFO")

        # Paylaşım geçmişini yükle
        posted_data: dict = get_posted_news()

        # ── Kontrol 1: Günlük limit ──
        if not _check_daily_limit(settings, posted_data):
            return

        # ── Kontrol 2: Rastgele atlama (anti-bot) ──
        if not _check_random_skip(settings):
            return

        # ── Kontrol 3: Minimum aralık ──
        if not _check_min_interval(settings, posted_data):
            return

        # ── Anti-bot: Rastgele gecikme ──
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

        # Haber tam metnini çekmeyi dene (daha iyi içerik için)
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

        # YZ ile Facebook post metni üret
        post_text: str = generate_post_text(selected)

        if not post_text:
            log(
                "❌ İçerik üretilemedi. Çıkılıyor.",
                "ERROR",
            )
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

        # ── Final ──
        log(separator, "INFO")
        if success:
            log("🎉 ═══ İşlem tamamlandı: BAŞARILI ═══", "INFO")
        else:
            log("😞 ═══ İşlem tamamlandı: BAŞARISIZ ═══", "WARNING")
        log(separator, "INFO")

    except KeyboardInterrupt:
        log("⚠️ Kullanıcı tarafından durduruldu (Ctrl+C)", "WARNING")

    except Exception as e:
        # Tüm beklenmeyen hataları yakala — graceful çıkış
        # exit(1) yapmıyoruz: GitHub Actions workflow'un hata
        # göstermesini engelliyoruz (bir sonraki çalışmada tekrar dener)
        log(separator, "ERROR")
        log(f"💥 KRİTİK HATA: {str(e)}", "ERROR")

        # Hata detayını logla (debug için)
        import traceback

        error_details: str = traceback.format_exc()
        log(f"📋 Hata detayı:\n{error_details}", "ERROR")

        log(
            "ℹ️ Bot bir sonraki çalışmada tekrar deneyecek.",
            "INFO",
        )
        log(separator, "ERROR")


if __name__ == "__main__":
    main()
