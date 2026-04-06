"""
core/orchestrator.py — Ana Dirijан (v3)

otoXtra Facebook Botunun ana kontrolcüsü.
GitHub Actions tarafından "python core/orchestrator.py" komutuyla çalıştırılır.

Tüm ajanları sırayla çalıştırır. Kendisi hiçbir iş yapmaz,
sadece yönetir ve koordine eder.

Akış:
  1. Başlangıç kontrolleri (limit, atlama, aralık)
  2. Pipeline başlat
  3. agent_fetcher → RSS çek, filtrele
  4. agent_scorer  → YZ ile puanla, en iyisini seç
  5. agent_writer  → YZ ile metin yaz
  6. agent_image   → Görsel hazırla
  7. agent_publisher → Facebook'a paylaş

TEST MODU:
  GitHub Actions'da manuel tetiklendiğinde otomatik aktif olur.
  Ayrıca: python core/orchestrator.py --test
  - Rastgele gecikme ATLANIR
  - Rastgele atlama ATLANIR
  - Minimum aralık kontrolü ATLANIR
  - Gerçek Facebook paylaşımı YAPILMAZ

Bağımsız çalıştırma:
  python core/orchestrator.py
  python core/orchestrator.py --test
"""

import os
import sys
import random
from datetime import datetime, timedelta
from typing import Optional

from core.logger import log
from core.config_loader import load_config
from core.helpers import (
    get_posted_news,
    save_posted_news,
    get_today_post_count,
    get_turkey_now,
    save_last_check_time,
)
from core.state_manager import init_pipeline, get_stage, get_status


# ============================================================
# TEST MODU
# ============================================================

def _is_test_mode() -> bool:
    """TEST_MODE ortam değişkenini veya --test argümanını kontrol eder."""
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    if "--test" in sys.argv:
        return True
    return False


# ============================================================
# 1. BAŞLANGIÇ KONTROLLERİ
# ============================================================

def _check_daily_limit(settings: dict, posted_data: dict) -> bool:
    """Günlük paylaşım limitinin dolup dolmadığını kontrol eder.

    Returns:
        bool: Devam edilebilirse True, limit dolmuşsa False.
    """
    today_count = get_today_post_count(posted_data)
    max_daily = settings.get("posting", {}).get("max_daily_posts", 9)

    log(f"📊 Bugün {today_count}/{max_daily} post yapıldı")

    if today_count >= max_daily:
        log(
            f"🛑 Günlük limit doldu ({today_count}/{max_daily})",
            "WARNING",
        )
        return False

    return True


def _check_random_skip(settings: dict, test_mode: bool) -> bool:
    """Rastgele atlama kontrolü yapar.

    TEST MODUNDA her zaman True döner (atlamaz).

    Returns:
        bool: Devam edilebilirse True, atlanacaksa False.
    """
    if test_mode:
        log("🧪 TEST MODU: Rastgele atlama devre dışı")
        return True

    skip_probability = settings.get("posting", {}).get(
        "skip_probability_percent", 10
    )

    if skip_probability <= 0:
        return True

    roll = random.randint(1, 100)
    if roll <= skip_probability:
        log(
            f"🎲 Rastgele atlama: zar={roll}, eşik={skip_probability} "
            f"→ bu çalışma atlanıyor",
            "INFO",
        )
        return False

    log(f"🎲 Rastgele atlama: zar={roll}, eşik={skip_probability} → devam")
    return True


def _check_min_interval(settings: dict, posted_data: dict, test_mode: bool) -> bool:
    """Son paylaşımdan yeterli süre geçip geçmediğini kontrol eder.

    TEST MODUNDA her zaman True döner (kontrol yapmaz).

    Returns:
        bool: Devam edilebilirse True, henüz erkense False.
    """
    if test_mode:
        log("🧪 TEST MODU: Minimum aralık kontrolü devre dışı")
        return True

    min_interval_hours = settings.get("posting", {}).get(
        "min_post_interval_hours", 0
    )

    if min_interval_hours <= 0:
        return True

    posts = posted_data.get("posts", [])
    if not posts:
        log("ℹ️ Daha önce paylaşım yapılmamış → devam ediliyor")
        return True

    last_post = posts[-1]
    last_posted_at_str = last_post.get("posted_at", "")

    if not last_posted_at_str:
        log("ℹ️ Son paylaşım zamanı bulunamadı → devam ediliyor")
        return True

    try:
        from dateutil import parser as date_parser

        last_posted_at = date_parser.isoparse(last_posted_at_str)
        now_turkey = get_turkey_now()
        hours_since = (now_turkey - last_posted_at).total_seconds() / 3600

        if hours_since < min_interval_hours:
            remaining = int((min_interval_hours - hours_since) * 60)
            log(
                f"⏰ Son paylaşımdan {hours_since:.1f} saat geçmiş "
                f"(minimum: {min_interval_hours}s) → "
                f"yaklaşık {remaining}dk daha bekle",
                "WARNING",
            )
            return False

        log(
            f"⏰ Son paylaşımdan {hours_since:.1f} saat geçmiş "
            f"(minimum: {min_interval_hours}s) → devam"
        )
        return True

    except (ValueError, TypeError) as exc:
        log(f"⚠️ Son paylaşım zamanı parse edilemedi: {exc} → devam", "WARNING")
        return True


def _random_delay(settings: dict, test_mode: bool) -> None:
    """Rastgele bekleme yapar. TEST MODUNDA atlar."""
    if test_mode:
        log("🧪 TEST MODU: Rastgele gecikme atlandı")
        return

    import time

    max_delay_minutes = settings.get("posting", {}).get(
        "random_delay_max_minutes", 8
    )

    if max_delay_minutes <= 0:
        return

    delay_seconds = random.randint(0, max_delay_minutes * 60)
    delay_min = delay_seconds // 60
    delay_sec = delay_seconds % 60

    log(f"⏱️ Rastgele bekleme: {delay_min}dk {delay_sec}sn")
    time.sleep(delay_seconds)


# ============================================================
# 2. KAYIT GÜNCELLEME
# ============================================================

def _save_check_time() -> None:
    """Son kontrol zamanını güvenli şekilde kaydeder."""
    try:
        fresh_data = get_posted_news()
        save_last_check_time(fresh_data)
        save_posted_news(fresh_data)
        log("💾 Son kontrol zamanı kaydedildi")
    except Exception as exc:
        log(f"⚠️ Son kontrol zamanı kaydedilemedi: {exc}", "WARNING")


# ============================================================
# 3. AJAN ÇALIŞTIRICI
# ============================================================

def _run_agent(agent_name: str, run_func) -> bool:
    """Bir ajanı çalıştırır, hata yönetimi yapar.

    Args:
        agent_name: Ajan adı (log için).
        run_func:   Ajanın run() fonksiyonu.

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    log(f"▶️  {agent_name} çalıştırılıyor...")

    try:
        success = run_func()
        if success:
            log(f"✅ {agent_name} tamamlandı")
        else:
            log(f"❌ {agent_name} başarısız oldu", "ERROR")
        return success

    except Exception as exc:
        log(f"❌ {agent_name} kritik hata: {exc}", "ERROR")

        import traceback
        log(f"📋 Hata detayı:\n{traceback.format_exc()}", "ERROR")
        return False


# ============================================================
# 4. ANA ORKESTRASYON
# ============================================================

def main() -> None:
    """otoXtra botunun ana orkestrasyon fonksiyonu.

    Tüm ajanları sırayla çalıştırır.
    Bir ajan başarısız olursa durur, sonraki ajanı çalıştırmaz.
    """
    sep = "═" * 60
    test_mode = _is_test_mode()

    try:
        # ── Başlık ──
        log(sep)
        log("🚗 otoXtra Bot Başlatılıyor (Modüler Ajan Sistemi v3)")
        if test_mode:
            log("🧪 ══ TEST MODU AKTİF — Gecikmeler ve paylaşım devre dışı ══")
        log(sep)

        turkey_now = get_turkey_now()
        log(f"🕐 Türkiye saati: {turkey_now.strftime('%Y-%m-%d %H:%M:%S')}")

        # ── Ayarlar ──
        settings = load_config("settings")
        log("✅ Ayarlar yüklendi")

        posted_data = get_posted_news()

        # ────────────────────────────────────────────
        # BAŞLANGIÇ KONTROLLERİ
        # ────────────────────────────────────────────

        # Kontrol 1: Günlük limit
        if not _check_daily_limit(settings, posted_data):
            _save_check_time()
            return

        # Kontrol 2: Rastgele atlama
        if not _check_random_skip(settings, test_mode):
            _save_check_time()
            return

        # Kontrol 3: Minimum aralık
        if not _check_min_interval(settings, posted_data, test_mode):
            _save_check_time()
            return

        # Rastgele gecikme
        _random_delay(settings, test_mode)

        # ────────────────────────────────────────────
        # PİPELINE BAŞLAT
        # ────────────────────────────────────────────

        run_id = turkey_now.strftime("%Y-%m-%d-%H:%M")
        init_pipeline(run_id)
        log(f"🔄 Pipeline başlatıldı: {run_id}")

        # ────────────────────────────────────────────
        # AJAN 1: FETCHER — Haber çek ve filtrele
        # ────────────────────────────────────────────
        log(sep)
        log("📰 AJAN 1: Haber Çekme (agent_fetcher)")
        log(sep)

        from agents.agent_fetcher import run as fetcher_run
        if not _run_agent("agent_fetcher", fetcher_run):
            log("Haber çekilemedi — işlem durduruluyor", "WARNING")
            _save_check_time()
            return

        # ────────────────────────────────────────────
        # AJAN 2: SCORER — YZ ile puanla
        # ────────────────────────────────────────────
        log(sep)
        log("🔍 AJAN 2: Puanlama (agent_scorer)")
        log(sep)

        from agents.agent_scorer import run as scorer_run
        if not _run_agent("agent_scorer", scorer_run):
            log("Puanlama başarısız — işlem durduruluyor", "WARNING")
            _save_check_time()
            return

        # ────────────────────────────────────────────
        # AJAN 3: WRITER — YZ ile metin yaz
        # ────────────────────────────────────────────
        log(sep)
        log("✍️  AJAN 3: İçerik Yazma (agent_writer)")
        log(sep)

        from agents.agent_writer import run as writer_run
        if not _run_agent("agent_writer", writer_run):
            log("Metin üretilemedi — işlem durduruluyor", "WARNING")
            _save_check_time()
            return

        # ────────────────────────────────────────────
        # AJAN 4: IMAGE — Görsel hazırla
        # ────────────────────────────────────────────
        log(sep)
        log("🖼️  AJAN 4: Görsel Hazırlama (agent_image)")
        log(sep)

        from agents.agent_image import run as image_run
        if not _run_agent("agent_image", image_run):
            log("Görsel hazırlanamadı — işlem durduruluyor", "WARNING")
            _save_check_time()
            return

        # ────────────────────────────────────────────
        # AJAN 5: PUBLISHER — Facebook'a paylaş
        # ────────────────────────────────────────────
        log(sep)
        log("📣 AJAN 5: Yayıncı (agent_publisher)")
        log(sep)

        from agents.agent_publisher import run as publisher_run
        success = _run_agent("agent_publisher", publisher_run)

        # ────────────────────────────────────────────
        # SONUÇ
        # ────────────────────────────────────────────
        log(sep)
        if success:
            publish_output = get_stage("publish").get("output", {})
            is_real = not publish_output.get("test_mode", False)
            title = publish_output.get("article_title", "")

            if is_real:
                log(f"🎉 BAŞARIYLA PAYLAŞILDI: {title[:60]}")
            else:
                log(f"🧪 TEST MODU TAMAMLANDI (gerçek paylaşım yapılmadı): {title[:60]}")
        else:
            log("😞 İşlem tamamlandı: BAŞARISIZ", "WARNING")
        log(sep)

        _save_check_time()

    except KeyboardInterrupt:
        log("⚠️ Kullanıcı tarafından durduruldu (Ctrl+C)", "WARNING")
        _save_check_time()

    except Exception as exc:
        log(sep, "ERROR")
        log(f"💥 KRİTİK HATA: {exc}", "ERROR")

        import traceback
        log(f"📋 Hata detayı:\n{traceback.format_exc()}", "ERROR")
        log("ℹ️ Bot bir sonraki çalışmada tekrar deneyecek", "INFO")
        log(sep, "ERROR")

        _save_check_time()


# ============================================================
# GİRİŞ NOKTASI
# ============================================================

if __name__ == "__main__":
    main()
