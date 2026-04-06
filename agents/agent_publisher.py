"""
agents/agent_publisher.py — Yayıncı Ajanı

otoXtra Facebook Botu için pipeline'dan hazır içeriği alıp
Facebook'a paylaşan ve kayıt tutan bağımsız ajan.

Sorumlulukları:
  - Günlük limit kontrolü (max_daily_posts)
  - Rastgele atlama kontrolü (skip_probability_percent)
  - platforms/facebook.py ile Facebook'a paylaşım
  - data/posted_news.json güncelleme
  - last_check_time güncelleme
  - Rastgele bekleme (doğal görünsün diye)

Bağımsız çalıştırma:
    python agents/agent_publisher.py
    python agents/agent_publisher.py --test

Diğer modüller bu ajanı şöyle çağırır:
    from agents.agent_publisher import run
    success = run()
"""

import os
import random
import sys
import time
from typing import Optional

from core.logger import log
from core.config_loader import load_config
from core.helpers import (
    get_posted_news,
    save_posted_news,
    get_today_str,
    get_today_post_count,
    get_turkey_now,
    save_last_check_time,
)
from core.state_manager import get_stage, set_stage, init_pipeline
from platforms.facebook import post_photo, post_text


# ============================================================
# SABİTLER
# ============================================================

_RETRY_DELAY = 5   # İlk deneme başarısız olursa kaç saniye bekle
_VERIFY_DELAY = 4  # Paylaşım sonrası doğrulama bekleme süresi


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
# 1. GÜNLÜK LİMİT KONTROLÜ
# ============================================================

def _check_daily_limit(posted_data: dict, max_daily_posts: int) -> bool:
    """Bugün maksimum post sayısına ulaşılıp ulaşılmadığını kontrol eder.

    Args:
        posted_data:     get_posted_news() ile alınan dict.
        max_daily_posts: Günlük maksimum post sayısı.

    Returns:
        bool: Limit aşılmamışsa True (devam et), aşıldıysa False (dur).
    """
    today_count = get_today_post_count(posted_data)

    if today_count >= max_daily_posts:
        log(
            f"🚫 Günlük limit doldu: bugün {today_count}/{max_daily_posts} post yapıldı",
            "WARNING",
        )
        return False

    log(f"✅ Günlük limit: {today_count}/{max_daily_posts} post (devam ediliyor)")
    return True


# ============================================================
# 2. RASTGELE ATLAMA KONTROLÜ
# ============================================================

def _check_skip_probability(skip_percent: int) -> bool:
    """Rastgele atlama kontrolü yapar.

    Args:
        skip_percent: Atlama olasılığı (0-100 arası yüzde).

    Returns:
        bool: Atlanmaması gerekiyorsa True, atlanması gerekiyorsa False.
    """
    if skip_percent <= 0:
        return True

    roll = random.randint(1, 100)
    if roll <= skip_percent:
        log(
            f"🎲 Rastgele atlama: zar={roll}, eşik={skip_percent} "
            f"→ bu çalışma atlanıyor",
            "INFO",
        )
        return False

    log(f"🎲 Rastgele atlama: zar={roll}, eşik={skip_percent} → devam")
    return True


# ============================================================
# 3. KAYIT GÜNCELLEME
# ============================================================

def _record_posted(
    article: dict,
    post_id: str,
    image_source: str,
) -> None:
    """Başarılı paylaşımı data/posted_news.json dosyasına kaydeder.

    Args:
        article:      Paylaşılan haber dict'i.
        post_id:      Facebook'tan dönen post ID.
        image_source: Görsel kaynağı ("rss_image", "og:image", "fallback" vb.)
    """
    try:
        posted_data = get_posted_news()
        posts_list = posted_data.get("posts", [])
        daily_counts = posted_data.get("daily_counts", {})

        turkey_now = get_turkey_now()
        today_str = get_today_str()

        new_record = {
            "title": article.get("title", "Başlık yok"),
            "url": article.get("link", ""),
            "source": article.get("source_name", "Bilinmeyen"),
            "score": article.get("score", 0),
            "posted_at": turkey_now.isoformat(),
            "fb_post_id": post_id,
            "image_source": image_source,
        }

        posts_list.append(new_record)
        daily_counts[today_str] = daily_counts.get(today_str, 0) + 1

        posted_data["posts"] = posts_list
        posted_data["daily_counts"] = daily_counts

        # last_check_time güncelle
        save_last_check_time(posted_data)

        # Dosyaya kaydet
        save_posted_news(posted_data)

        log(
            f"💾 Kayıt güncellendi: {article.get('title', '')[:50]} "
            f"(bugün toplam: {daily_counts[today_str]} post)"
        )

    except Exception as exc:
        log(f"⚠️ Kayıt güncellenirken hata: {exc}", "WARNING")


# ============================================================
# 4. FACEBOOK'A PAYLAŞIM
# ============================================================

def _publish_to_facebook(
    article: dict,
    post_text_content: str,
    image_path: Optional[str],
) -> Optional[str]:
    """İçeriği Facebook'a gönderir. Fallback mekanizması içerir.

    Sıra:
      1. Görsel varsa → görselli post (post_photo)
      2. Görselli başarısız → sadece metin (post_text)
      3. İlk deneme tamamen başarısız → 5sn bekle → metin olarak tekrar dene

    Args:
        article:           Haber dict'i.
        post_text_content: Facebook post metni.
        image_path:        Görsel dosya yolu (None olabilir).

    Returns:
        str: Başarılıysa post_id. Başarısızsa None.
    """
    has_image = bool(image_path and os.path.exists(image_path))
    image_source = article.get("image_source", "unknown")

    log("=" * 55)
    log(f"📣 Facebook'a paylaşılıyor: {article.get('title', '')[:60]}")
    log("=" * 55)

    if has_image:
        log(f"🖼️ Görsel mevcut: {image_path} (kaynak: {image_source})")
    else:
        log("📝 Görsel yok — sadece metin paylaşılacak")

    # Metin önizleme
    preview = post_text_content[:200]
    if len(post_text_content) > 200:
        preview += "..."
    log(f"📝 Post önizleme:\n{preview}")

    # Deneme 1
    result_id = None

    if has_image:
        log("📤 Deneme 1/2: Görselli paylaşım...")
        result_id = post_photo(image_path, post_text_content)

        if result_id is None:
            log("⚠️ Görselli paylaşım başarısız → metin olarak deneniyor...", "WARNING")
            result_id = post_text(post_text_content)
            if result_id:
                article["image_source"] = "fallback_text"
    else:
        log("📤 Deneme 1/2: Metin paylaşımı...")
        result_id = post_text(post_text_content)

    # Deneme 2 (her şey başarısızsa)
    if result_id is None:
        log(
            f"⚠️ İlk deneme başarısız → {_RETRY_DELAY}sn beklenip tekrar deneniyor...",
            "WARNING",
        )
        time.sleep(_RETRY_DELAY)

        log("📤 Deneme 2/2: Metin paylaşımı (tekrar)...")
        result_id = post_text(post_text_content)
        if result_id:
            article["image_source"] = "retry_text"

    return result_id


# ============================================================
# 5. AJAN GİRİŞ NOKTASI
# ============================================================

def run() -> bool:
    """Ajanı çalıştırır. orchestrator.py tarafından çağrılır.

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    log("─" * 55)
    log("agent_publisher başlıyor")
    log("─" * 55)

    test_mode = _is_test_mode()

    # Image aşaması bitti mi?
    image_stage = get_stage("image")
    if image_stage.get("status") != "done":
        log("image aşaması tamamlanmamış — publisher çalıştırılamaz", "ERROR")
        set_stage("publish", "error", error="image aşaması tamamlanmamış")
        return False

    # Image çıktısından verileri al
    image_output = image_stage.get("output", {})
    article = image_output.get("article", {})
    post_text_content = image_output.get("post_text", "")
    image_path = image_output.get("image_path", "")
    image_source = image_output.get("image_source", "unknown")

    if not article:
        log("Image çıktısında haber yok", "WARNING")
        set_stage("publish", "error", error="Image çıktısında haber yok")
        return False

    if not post_text_content:
        log("Image çıktısında post metni yok", "WARNING")
        set_stage("publish", "error", error="Post metni yok")
        return False

    log(f"Paylaşılacak haber: {article.get('title', '')[:60]}")

    # Aşamayı çalışıyor işaretle
    set_stage("publish", "running")

    try:
        # Ayarları oku
        settings = load_config("settings")
        posting_settings = settings.get("posting", {})
        max_daily_posts = posting_settings.get("max_daily_posts", 9)
        skip_probability = posting_settings.get("skip_probability_percent", 10)
        random_delay_max = posting_settings.get("random_delay_max_minutes", 8)

        posted_data = get_posted_news()

        # Günlük limit kontrolü
        if not _check_daily_limit(posted_data, max_daily_posts):
            set_stage("publish", "error", error="Günlük limit doldu")
            return False

        # Rastgele atlama kontrolü (test modunda atlanır)
        if not test_mode:
            if not _check_skip_probability(skip_probability):
                set_stage("publish", "error", error="Rastgele atlama")
                return False
        else:
            log("🧪 TEST MODU: Rastgele atlama kontrolü atlandı")

        # TEST MODUNDA gerçek paylaşım yapma
        if test_mode:
            log("🧪 TEST MODU: Gerçek Facebook paylaşımı YAPILMAYACAK")
            log(f"🧪 Simüle edilen paylaşım: {article.get('title', '')[:60]}")

            fake_post_id = "test_000000000_111111111"

            output = {
                "success": True,
                "post_id": fake_post_id,
                "article_title": article.get("title", ""),
                "image_source": image_source,
                "test_mode": True,
            }
            set_stage("publish", "done", output=output)
            log("🧪 TEST başarılı — gerçek paylaşım yapılmadı")
            return True

        # Rastgele bekleme (paylaşımdan ÖNCE — doğal görünsün)
        if random_delay_max > 0:
            delay_seconds = random.randint(0, random_delay_max * 60)
            delay_minutes = delay_seconds // 60
            delay_secs = delay_seconds % 60
            log(f"⏱️ Rastgele bekleme: {delay_minutes}dk {delay_secs}sn")
            time.sleep(delay_seconds)

        # Facebook'a paylaşım
        post_id = _publish_to_facebook(article, post_text_content, image_path)

        if post_id is None:
            log("❌ Facebook paylaşımı BAŞARISIZ — tüm denemeler tükendi", "ERROR")
            set_stage("publish", "error", error="Facebook paylaşımı başarısız")
            return False

        # Kaydı güncelle
        final_image_source = article.get("image_source", image_source)
        _record_posted(article, post_id, final_image_source)

        # Pipeline'a yaz
        output = {
            "success": True,
            "post_id": post_id,
            "article_title": article.get("title", ""),
            "image_source": final_image_source,
            "test_mode": False,
        }
        set_stage("publish", "done", output=output)

        log("=" * 55)
        log(f"🎉 BAŞARIYLA PAYLAŞILDI: {article.get('title', '')[:60]}")
        log("=" * 55)

        return True

    except Exception as exc:
        log(f"agent_publisher kritik hata: {exc}", "ERROR")
        set_stage("publish", "error", error=str(exc))
        return False


# ============================================================
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ============================================================

if __name__ == "__main__":
    log("=== agent_publisher.py modül testi başlıyor ===")

    # Test için pipeline başlat
    init_pipeline("test-publisher")

    # Sahte image verisi oluştur
    fake_article = {
        "title": "Test: Yeni Elektrikli SUV Türkiye'de Satışa Çıktı",
        "link": "https://test.com/haber1",
        "summary": "Test özet metni.",
        "image_url": "",
        "source_name": "Test Kaynak",
        "source_priority": "high",
        "can_scrape_image": False,
        "score": 78,
        "image_source": "fallback",
    }
    fake_post_text = (
        "🚗 Yeni elektrikli SUV Türkiye'de!\n\n"
        "Test post metni burada yer alıyor.\n\n"
        "#elektrikli #SUV #otomotiv"
    )

    set_stage("fetch", "done", output={"articles": [fake_article], "count": 1})
    set_stage("score", "done", output={
        "selected_article": fake_article,
        "score": 78,
        "title": fake_article["title"],
    })
    set_stage("write", "done", output={
        "article": fake_article,
        "post_text": fake_post_text,
        "post_text_length": len(fake_post_text),
    })
    set_stage("image", "done", output={
        "article": fake_article,
        "post_text": fake_post_text,
        "image_path": "",
        "image_source": "fallback",
    })

    # Ajanı çalıştır (--test ile otomatik test modu)
    log("\nagent_publisher çalıştırılıyor (TEST MODU)...")
    success = run()

    if success:
        publish_stage = get_stage("publish")
        output = publish_stage.get("output", {})

        log(f"\n{'─' * 50}")
        log("SONUÇ:")
        log(f"  Başarılı   : {output.get('success', False)}")
        log(f"  Post ID    : {output.get('post_id', 'YOK')}")
        log(f"  Başlık     : {output.get('article_title', 'YOK')[:60]}")
        log(f"  Test modu  : {output.get('test_mode', False)}")
        log(f"{'─' * 50}")
    else:
        log("Ajan başarısız oldu", "WARNING")

    log("=== agent_publisher.py modül testi tamamlandı ===")
