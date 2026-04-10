"""
agents/agent_publisher.py - Yayinci Ajani (v3.0 - Multi Image Ready)

Degisiklikler v3.0:
  - image_paths destegi eklendi (coklu gorsel)
  - Facebook coklu gorsel fonksiyonu varsa onu dener
  - Yoksa tek gorsel veya metin fallback ile devam eder
  - Kayitlara image_count eklendi
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
from platforms import facebook as fb_platform


# ============================================================
# SABITLER
# ============================================================

_RETRY_DELAY = 5


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
# 1. GUNLUK LIMIT KONTROLU
# ============================================================

def _check_daily_limit(posted_data: dict, max_daily_posts: int) -> bool:
    today_count = get_today_post_count(posted_data)
    if today_count >= max_daily_posts:
        log(
            f"Gunluk limit doldu: bugun {today_count}/{max_daily_posts} post yapildi",
            "WARNING",
        )
        return False
    log(f"Gunluk limit: {today_count}/{max_daily_posts} post (devam)")
    return True


# ============================================================
# 2. RASTGELE ATLAMA KONTROLU
# ============================================================

def _check_skip_probability(skip_percent: int) -> bool:
    if skip_percent <= 0:
        return True
    roll = random.randint(1, 100)
    if roll <= skip_percent:
        log(f"Rastgele atlama: zar={roll}, esik={skip_percent} -> atlaniyor", "INFO")
        return False
    log(f"Rastgele atlama: zar={roll}, esik={skip_percent} -> devam")
    return True


# ============================================================
# 3. KAYIT GUNCELLEME
# ============================================================

def _record_posted(
    article: dict,
    post_id: str,
    image_source: str,
    image_count: int,
) -> None:
    """Basarili paylasimi data/posted_news.json dosyasina kaydeder."""
    try:
        posted_data = get_posted_news()
        posts_list = posted_data.get("posts", [])
        daily_counts = posted_data.get("daily_counts", {})

        turkey_now = get_turkey_now()
        today_str = get_today_str()

        fingerprint = article.get("topic_fingerprint", "")
        if not fingerprint:
            fingerprint = generate_topic_fingerprint(article.get("title", ""))

        new_record = {
            "title": article.get("title", "Baslik yok"),
            "url": article.get("link", ""),
            "topic_fingerprint": fingerprint,
            "source": article.get("source_name", "Bilinmeyen"),
            "score": article.get("score", 0),
            "trend_count": article.get("trend_count", 1),
            "posted_at": turkey_now.isoformat(),
            "fb_post_id": post_id,
            "image_source": image_source,
            "image_count": image_count,
        }

        posts_list.append(new_record)
        daily_counts[today_str] = daily_counts.get(today_str, 0) + 1

        posted_data["posts"] = posts_list
        posted_data["daily_counts"] = daily_counts

        save_last_check_time(posted_data)
        save_posted_news(posted_data)

        log(
            f"Kayit guncellendi: {article.get('title', '')[:60]} "
            f"(bugun toplam: {daily_counts[today_str]}, gorsel: {image_count})"
        )

    except Exception as exc:
        log(f"Kayit guncellenirken hata: {exc}", "WARNING")


# ============================================================
# 4. FACEBOOK YAYIN YARDIMCILARI
# ============================================================

def _collect_valid_image_paths(image_output: dict) -> list[str]:
    """image stage output'tan gecerli dosya yollarini toplar."""
    collected: list[str] = []

    multi_paths = image_output.get("image_paths", [])
    if isinstance(multi_paths, list):
        for item in multi_paths:
            if isinstance(item, str) and item and os.path.exists(item):
                collected.append(item)

    single_path = image_output.get("image_path", "")
    if isinstance(single_path, str) and single_path and os.path.exists(single_path):
        if single_path not in collected:
            collected.append(single_path)

    return collected


def _try_post_multi_photos(image_paths: list[str], post_text_content: str) -> Optional[str]:
    """
    Facebook platform modulu coklu gorsel fonksiyonu sagliyorsa dener.
    Destekli olasi isimler:
      - post_photos(image_paths, text)
      - post_multi_photo(image_paths, text)
      - post_album(image_paths, text)
    """
    candidate_names = ["post_photos", "post_multi_photo", "post_album"]

    for fn_name in candidate_names:
        fn = getattr(fb_platform, fn_name, None)
        if callable(fn):
            try:
                log(f"Coklu gorsel fonksiyonu denenecek: facebook.{fn_name}()")
                result = fn(image_paths, post_text_content)
                if result:
                    return result
            except Exception as exc:
                log(f"facebook.{fn_name} hata verdi: {exc}", "WARNING")

    return None


def _publish_to_facebook(
    article: dict,
    post_text_content: str,
    image_paths: list[str],
) -> Optional[str]:
    """Icerigi Facebook'a gonderir. Coklu -> tekli -> metin fallback sirasi kullanir."""
    image_source = article.get("image_source", "unknown")
    image_count = len(image_paths)

    log("=" * 55)
    log(f"Facebook'a paylasiliyor: {article.get('title', '')[:80]}")
    log("=" * 55)
    log(f"Gorsel adedi: {image_count} (kaynak: {image_source})")

    preview = post_text_content[:220]
    if len(post_text_content) > 220:
        preview += "..."
    log(f"Post onizleme:\n{preview}")

    result_id = None

    # 1) Coklu gorsel dene
    if image_count >= 2:
        log("Deneme 1/3: Coklu gorsel paylasimi...")
        result_id = _try_post_multi_photos(image_paths, post_text_content)
        if result_id:
            article["image_source"] = "multi_image"
            return result_id
        log("Coklu gorsel paylasimi basarisiz, tek gorsele dusulecek", "WARNING")

    # 2) Tek gorsel dene
    if image_count >= 1:
        first_image = image_paths[0]
        post_photo_fn = getattr(fb_platform, "post_photo", None)
        if callable(post_photo_fn):
            log("Deneme 2/3: Tek gorsel paylasimi...")
            try:
                result_id = post_photo_fn(first_image, post_text_content)
            except Exception as exc:
                log(f"post_photo hata verdi: {exc}", "WARNING")
                result_id = None

            if result_id:
                if image_count >= 2:
                    article["image_source"] = "single_fallback_from_multi"
                return result_id
        else:
            log("facebook.post_photo fonksiyonu bulunamadi", "WARNING")

    # 3) Metin paylasimi
    post_text_fn = getattr(fb_platform, "post_text", None)
    if callable(post_text_fn):
        log("Deneme 3/3: Metin paylasimi...")
        try:
            result_id = post_text_fn(post_text_content)
        except Exception as exc:
            log(f"post_text hata verdi: {exc}", "WARNING")
            result_id = None
        if result_id:
            article["image_source"] = "fallback_text"
            return result_id
    else:
        log("facebook.post_text fonksiyonu bulunamadi", "WARNING")

    return None


# ============================================================
# 5. AJAN GIRIS NOKTASI
# ============================================================

def run() -> bool:
    """Ajani calistirir. orchestrator.py tarafindan cagrilir."""
    log("-" * 55)
    log("agent_publisher basliyor")
    log("-" * 55)

    test_mode = _is_test_mode()

    image_stage = get_stage("image")
    if image_stage.get("status") != "done":
        log("image asamasi tamamlanmamis, publisher calistirilamaz", "ERROR")
        set_stage("publish", "error", error="image asamasi tamamlanmamis")
        return False

    image_output = image_stage.get("output", {})
    article = image_output.get("article", {})
    post_text_content = image_output.get("post_text", "")
    image_source = image_output.get("image_source", "unknown")
    image_paths = _collect_valid_image_paths(image_output)

    if not article:
        log("Image cikisinda haber yok", "WARNING")
        set_stage("publish", "error", error="Image cikisinda haber yok")
        return False

    if not post_text_content:
        log("Image cikisinda post metni yok", "WARNING")
        set_stage("publish", "error", error="Post metni yok")
        return False

    log(f"Paylasilacak haber: {article.get('title', '')[:80]}")
    log(
        f"Trend: {article.get('trend_count', 1)} kaynak, "
        f"+{article.get('trend_bonus', 0)} bonus puan"
    )
    log(f"Image source: {image_source}, image count: {len(image_paths)}")

    set_stage("publish", "running")

    try:
        settings = load_config("settings")
        posting_settings = settings.get("posting", {})
        max_daily_posts = int(posting_settings.get("max_daily_posts", 9))
        skip_probability = int(posting_settings.get("skip_probability_percent", 10))
        random_delay_max = int(posting_settings.get("random_delay_max_minutes", 8))

        posted_data = get_posted_news()

        if not _check_daily_limit(posted_data, max_daily_posts):
            set_stage("publish", "error", error="Gunluk limit doldu")
            return False

        if not test_mode and not _check_skip_probability(skip_probability):
            set_stage("publish", "error", error="Rastgele atlama")
            return False

        if test_mode:
            log("TEST MODU: Gercek Facebook paylasimi yapilmayacak")
            fake_post_id = "test_000000000_111111111"

            output = {
                "success": True,
                "post_id": fake_post_id,
                "article_title": article.get("title", ""),
                "image_source": image_source,
                "image_count": len(image_paths),
                "test_mode": True,
            }
            set_stage("publish", "done", output=output)
            log("TEST basarili, gercek paylasim yapilmadi")
            return True

        if random_delay_max > 0:
            delay_seconds = random.randint(0, random_delay_max * 60)
            log(f"Rastgele bekleme: {delay_seconds} sn")
            time.sleep(delay_seconds)

        post_id = _publish_to_facebook(article, post_text_content, image_paths)

        if post_id is None:
            log(f"Ilk tur basarisiz, {_RETRY_DELAY} sn sonra metin retry", "WARNING")
            time.sleep(_RETRY_DELAY)

            post_text_fn = getattr(fb_platform, "post_text", None)
            if callable(post_text_fn):
                try:
                    post_id = post_text_fn(post_text_content)
                    if post_id:
                        article["image_source"] = "retry_text"
                except Exception as exc:
                    log(f"Retry post_text hatasi: {exc}", "WARNING")
                    post_id = None

        if post_id is None:
            log("Facebook paylasimi basarisiz, tum denemeler tukendi", "ERROR")
            set_stage("publish", "error", error="Facebook paylasimi basarisiz")
            return False

        final_image_source = article.get("image_source", image_source)
        final_image_count = len(image_paths) if final_image_source != "fallback_text" else 0
        _record_posted(article, post_id, final_image_source, final_image_count)

        output = {
            "success": True,
            "post_id": post_id,
            "article_title": article.get("title", ""),
            "image_source": final_image_source,
            "image_count": final_image_count,
            "test_mode": False,
        }
        set_stage("publish", "done", output=output)

        log("=" * 55)
        log(f"BASARIYLA PAYLASILDI: {article.get('title', '')[:80]}")
        log("=" * 55)

        return True

    except Exception as exc:
        log(f"agent_publisher kritik hata: {exc}", "ERROR")
        set_stage("publish", "error", error=str(exc))
        return False


# ============================================================
# MODUL TESTI
# ============================================================

if __name__ == "__main__":
    log("=== agent_publisher.py modul testi basliyor ===")

    init_pipeline("test-publisher")

    fake_article = {
        "title": "Test: Yeni Elektrikli SUV Turkiye'de Satisa Cikti",
        "link": "https://test.com/haber1",
        "summary": "Test ozet metni.",
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
        "Yeni elektrikli SUV Turkiye'de.\n\n"
        "Test post metni burada yer aliyor.\n\n"
        "#elektrikli #SUV #otomotiv"
    )

    set_stage("fetch", "done", output={"articles": [fake_article], "count": 1})
    set_stage(
        "score",
        "done",
        output={
            "selected_article": fake_article,
            "score": 78,
            "title": fake_article["title"],
            "trend_count": 2,
            "trend_bonus": 5,
        },
    )
    set_stage(
        "write",
        "done",
        output={
            "article": fake_article,
            "post_text": fake_post_text,
            "post_text_length": len(fake_post_text),
        },
    )
    set_stage(
        "image",
        "done",
        output={
            "article": fake_article,
            "post_text": fake_post_text,
            "image_path": "",
            "image_paths": [],
            "image_source": "fallback",
            "image_count": 0,
        },
    )

    success = run()

    if success:
        publish_stage = get_stage("publish")
        output = publish_stage.get("output", {})
        log("-" * 50)
        log("SONUC:")
        log(f"Basarili   : {output.get('success', False)}")
        log(f"Post ID    : {output.get('post_id', 'YOK')}")
        log(f"Baslik     : {output.get('article_title', 'YOK')[:60]}")
        log(f"Image src  : {output.get('image_source', 'YOK')}")
        log(f"Image cnt  : {output.get('image_count', 0)}")
        log(f"Test modu  : {output.get('test_mode', False)}")
        log("-" * 50)
    else:
        log("Ajan basarisiz oldu", "WARNING")

    log("=== agent_publisher.py modul testi tamamlandi ===")
