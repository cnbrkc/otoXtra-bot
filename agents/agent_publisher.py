"""
agents/agent_publisher.py — Yayıncı Ajanı (v2.1 — Konu Hafızası)

Değişiklikler v2.1:
  - _record_posted() : "topic_fingerprint" ve standart "url" alanı eklendi
  - Alan adı tutarsızlığı giderildi: hep "url" kullanılıyor (original_url kaldırıldı)
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
    generate_topic_fingerprint,
)
from core.state_manager import get_stage, set_stage, init_pipeline
from platforms.facebook import post_photo, post_text


# ============================================================
# SABİTLER
# ============================================================

_RETRY_DELAY = 5
_VERIFY_DELAY = 4


# ============================================================
# TEST MODU
# ============================================================

def _is_test_mode() -> bool:
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    if "--test" in sys.argv:
        return True
    return False


# ============================================================
# 1. GÜNLÜK LİMİT KONTROLÜ
# ============================================================

def _check_daily_limit(posted_data: dict, max_daily_posts: int) -> bool:
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
    if skip_percent <= 0:
        return True
    roll = random.randint(1, 100)
    if roll <= skip_percent:
        log(
            f"🎲 Rastgele atlama: zar={roll}, eşik={skip_percent} → atlanıyor",
            "INFO",
        )
        return False
    log(f"🎲 Rastgele atlama: zar={roll}, eşik={skip_percent} → devam")
    return True


# ============================================================
# 3. KAYIT GÜNCELLEME — KONU PARMAK İZİ EKLENDİ
# ============================================================

def _record_posted(
    article: dict,
    post_id: str,
    image_source: str,
) -> None:
    """Başarılı paylaşımı data/posted_news.json dosyasına kaydeder.

    Kaydedilen alanlar:
      - title            : Haber başlığı
      - url              : Haber URL'si (standart alan adı)
      - topic_fingerprint: Konu parmak izi  ← YENİ
      - source           : Kaynak adı
      - score            : YZ puanı
      - trend_count      : Kaç kaynaktan geldi  ← YENİ
      - posted_at        : Paylaşım zamanı
      - fb_post_id       : Facebook post ID
      - image_source     : Görsel kaynağı
    """
    try:
        posted_data = get_posted_news()
        posts_list = posted_data.get("posts", [])
        daily_counts = posted_data.get("daily_counts", {})

        turkey_now = get_turkey_now()
        today_str = get_today_str()

        # Parmak izi: haberde varsa al, yoksa üret
        fingerprint = article.get("topic_fingerprint", "")
        if not fingerprint:
            fingerprint = generate_topic_fingerprint(article.get("title", ""))

        new_record = {
            "title": article.get("title", "Başlık yok"),
            "url": article.get("link", ""),           # Standart alan: "url"
            "topic_fingerprint": fingerprint,          # YENİ
            "source": article.get("source_name", "Bilinmeyen"),
            "score": article.get("score", 0),
            "trend_count": article.get("trend_count", 1),   # YENİ
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

        # Dosyaya kaydet (otomatik 30 günlük temizlik içeriyor)
        save_posted_news(posted_data)

        log(
            f"💾 Kayıt güncellendi: {article.get('title', '')[:50]} "
            f"(bugün toplam: {daily_counts[today_str]} post, "
            f"parmak izi: {fingerprint[:30]}...)"
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
    """İçeriği Facebook'a gönderir. Fallback mekanizması içerir."""
    has_image = bool(image_path and os.path.exists(image_path))
    image_source = article.get("image_source", "unknown")

    log("=" * 55)
    log(f"📣 Facebook'a paylaşılıyor: {article.get('title', '')[:60]}")
    log("=" * 55)

    if has_image:
        log(f"🖼️ Görsel mevcut: {image_path} (kaynak: {image_source})")
    else:
        log("📝 Görsel yok — sadece metin paylaşılacak")

    preview = post_text_content[:200]
    if len(post_text_content) > 200:
        preview += "..."
    log(f"📝 Post önizleme:\n{preview}")

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

    if result_id is None:
        log(f"⚠️ İlk deneme başarısız → {_RETRY_DELAY}sn beklenip tekrar deneniyor...", "WARNING")
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
    """Ajanı çalıştırır. orchestrator.py tarafından çağrılır."""
    log("─" * 55)
    log("agent_publisher başlıyor")
    log("─" * 55)

    test_mode = _is_test_mode()

    image_stage = get_stage("image")
    if image_stage.get("status") != "done":
        log("image aşaması tamamlanmamış — publisher çalıştırılamaz", "ERROR")
        set_stage("publish", "error", error="image aşaması tamamlanmamış")
        return False

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
    log(f"Trend: {article.get('trend_count', 1)} kaynak, "
        f"+{article.get('trend_bonus', 0)} bonus puan")

    set_stage("publish", "running")

    try:
        settings = load_config("settings")
        posting_settings = settings.get("posting", {})
        max_daily_posts = posting_settings.get("max_daily_posts", 9)
        skip_probability = posting_settings.get("skip_probability_percent", 10)
        random_delay_max = posting_settings.get("random_delay_max_minutes", 8)

        posted_data = get_posted_news()

        if not _check_daily_limit(posted_data, max_daily_posts):
            set_stage("publish", "error", error="Günlük limit doldu")
            return False

        if not test_mode:
            if not _check_skip_probability(skip_probability):
                set_stage("publish", "error", error="Rastgele atlama")
                return False
        else:
            log("🧪 TEST MODU: Rastgele atlama kontrolü atlandı")

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

        if random_delay_max > 0:
            delay_seconds = random.randint(0, random_delay_max * 60)
            delay_minutes = delay_seconds // 60
            delay_secs = delay_seconds % 60
            log(f"⏱️ Rastgele bekleme: {delay_minutes}dk {delay_secs}sn")
            time.sleep(delay_seconds)

        post_id = _publish_to_facebook(article, post_text_content, image_path)

        if post_id is None:
            log("❌ Facebook paylaşımı BAŞARISIZ — tüm denemeler tükendi", "ERROR")
            set_stage("publish", "error", error="Facebook paylaşımı başarısız")
            return False

        final_image_source = article.get("image_source", image_source)
        _record_posted(article, post_id, final_image_source)

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
# MODÜL TESTİ
# ============================================================

if __name__ == "__main__":
    log("=== agent_publisher.py modül testi başlıyor ===")

    init_pipeline("test-publisher")

    fake_article = {
        "title": "Test: Yeni Elektrikli SUV Türkiye'de Satışa Çıktı",
        "link": "https://test.com/haber1",
        "summary": "Test özet metni.",
        "image_url": "",
        "source_name": "Test Kaynak",
        "source_priority": "high",
        "can_scrape_image": False,
        "score": 78,
        "trend_count": 2,
        "trend_bonus": 5,
        "topic_fingerprint": "elektrikli-satis-suv-turkiye-yeni",
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
        "trend_count": 2,
        "trend_bonus": 5,
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
