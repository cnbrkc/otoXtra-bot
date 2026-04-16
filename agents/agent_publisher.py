"""
agents/agent_publisher.py - Yayinci Ajani (v3.7)

v3.7:
  - Telegram bildirim metni sadeletirildi (intro + post id kaldirildi)
  - Bildirime A{aksiyon}-P{paylasim} eklendi
  - Bildirime saglik raporu eklendi (Saglikli / Hata)
  - Haftalik paylasim sayaci icin istatistik guncellemesi eklendi
"""

import os
import random
import time
from typing import Optional

from core.config_loader import load_config
from core.helpers import (
    generate_topic_fingerprint,
    get_posted_news,
    get_today_action_count,
    get_today_post_count,
    get_today_str,
    get_turkey_now,
    increment_weekly_share,
    save_last_check_time,
    save_posted_news,
)
from core.logger import log
from core.state_manager import get_stage, set_stage
from platforms import facebook as fb_platform
from platforms import telegram as tg_platform


_RETRY_DELAY = 5


def _get_env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    return default


def _is_dry_run(settings: dict) -> bool:
    if os.environ.get("DRY_RUN") is not None:
        return _get_env_bool("DRY_RUN", False)
    posting_cfg = settings.get("posting", {}) if isinstance(settings, dict) else {}
    return bool(posting_cfg.get("dry_run", False))


def _is_random_delay_enabled() -> bool:
    return _get_env_bool("ENABLE_RANDOM_DELAY", True)


def _is_random_skip_enabled() -> bool:
    return _get_env_bool("ENABLE_RANDOM_SKIP", True)


def _is_persist_state_enabled() -> bool:
    return _get_env_bool("PERSIST_STATE", True)


def _check_daily_limit(posted_data: dict, max_daily_posts: int) -> bool:
    today_count = get_today_post_count(posted_data)
    if today_count >= max_daily_posts:
        log(f"Gunluk limit doldu: {today_count}/{max_daily_posts}", "WARNING")
        return False
    log(f"Gunluk limit: {today_count}/{max_daily_posts} (devam)")
    return True


def _check_skip_probability(skip_percent: int) -> bool:
    if skip_percent <= 0:
        return True
    roll = random.randint(1, 100)
    if roll <= skip_percent:
        log(f"Rastgele atlama: zar={roll}, esik={skip_percent} -> atlaniyor", "INFO")
        return False
    log(f"Rastgele atlama: zar={roll}, esik={skip_percent} -> devam")
    return True


def _build_new_post_record(article: dict, post_id: str, image_source: str, image_count: int) -> dict:
    fingerprint = article.get("topic_fingerprint", "") or generate_topic_fingerprint(article.get("title", ""))
    return {
        "title": article.get("title", "Baslik yok"),
        "url": article.get("link", ""),
        "topic_fingerprint": fingerprint,
        "source": article.get("source_name", "Bilinmeyen"),
        "score": article.get("score", 0),
        "trend_count": article.get("trend_count", 1),
        "posted_at": get_turkey_now().isoformat(),
        "fb_post_id": post_id,
        "image_source": image_source,
        "image_count": image_count,
    }


def _record_posted(article: dict, post_id: str, image_source: str, image_count: int) -> None:
    if not _is_persist_state_enabled():
        log("State persistence kapali (PERSIST_STATE=false), posted kaydi yazilmadi")
        return

    try:
        posted_data = get_posted_news()
        today_str = get_today_str()

        posts_list = posted_data.get("posts", [])
        daily_counts = posted_data.get("daily_counts", {})

        posts_list.append(_build_new_post_record(article, post_id, image_source, image_count))
        daily_counts[today_str] = daily_counts.get(today_str, 0) + 1

        posted_data["posts"] = posts_list
        posted_data["daily_counts"] = daily_counts

        increment_weekly_share(posted_data)
        save_last_check_time(posted_data)
        save_posted_news(posted_data)

        log(
            f"Kayit guncellendi: {article.get('title', '')[:60]} "
            f"(bugun toplam: {daily_counts[today_str]}, gorsel: {image_count})"
        )
    except Exception as exc:
        log(f"Kayit guncellenirken hata: {exc}", "WARNING")


def _collect_valid_image_paths(image_output: dict) -> list[str]:
    collected: list[str] = []
    seen_keys: set[str] = set()

    def _add_if_valid(path: str) -> None:
        if not isinstance(path, str) or not path:
            return
        if not os.path.exists(path):
            log(f"Image path skip (not found): {path}", "WARNING")
            return

        key = os.path.normcase(os.path.realpath(path))
        if key in seen_keys:
            log(f"Image path skip (duplicate path key): {path}", "INFO")
            return

        seen_keys.add(key)
        collected.append(path)

    for item in image_output.get("image_paths", []) if isinstance(image_output.get("image_paths", []), list) else []:
        _add_if_valid(item)

    primary_path = image_output.get("image_path", "")
    if isinstance(primary_path, str) and primary_path and not os.path.exists(primary_path):
        log(f"Primary image path not found: {primary_path}", "WARNING")
    _add_if_valid(primary_path)

    return collected


def _try_post_multi_photos(image_paths: list[str], post_text_content: str) -> Optional[str]:
    for fn_name in ("post_photos", "post_multi_photo", "post_album"):
        fn = getattr(fb_platform, fn_name, None)
        if not callable(fn):
            continue

        try:
            log(f"Coklu gorsel fonksiyonu deneniyor: facebook.{fn_name}()")
            result = fn(image_paths, post_text_content)
            if result:
                log(f"Coklu gorsel fonksiyonu basarili: facebook.{fn_name}() -> {result}")
                return result
            log(f"Coklu gorsel fonksiyonu sonuc vermedi: facebook.{fn_name}()", "WARNING")
        except Exception as exc:
            log(f"facebook.{fn_name} hata: {exc}", "WARNING")

    return None


def _log_publish_preview(article: dict, post_text_content: str, image_paths: list[str]) -> None:
    image_source = article.get("image_source", "unknown")
    image_count = len(image_paths)

    log("=" * 55)
    log(f"Facebook'a paylasiliyor: {article.get('title', '')[:80]}")
    log("=" * 55)
    log(f"Gorsel adedi: {image_count} (kaynak: {image_source})")

    if image_paths:
        for idx, path in enumerate(image_paths, start=1):
            try:
                size_kb = os.path.getsize(path) // 1024
            except Exception:
                size_kb = -1
            log(f"Final image[{idx}] path={path} size_kb={size_kb}")
    else:
        log("Final image listesi bos", "WARNING")

    preview = post_text_content[:220] + ("..." if len(post_text_content) > 220 else "")
    log(f"Post onizleme:\n{preview}")


def _publish_to_facebook(article: dict, post_text_content: str, image_paths: list[str]) -> Optional[str]:
    _log_publish_preview(article, post_text_content, image_paths)

    image_count = len(image_paths)

    if image_count >= 2:
        log("Deneme 1/3: Coklu gorsel paylasimi...")
        result_id = _try_post_multi_photos(image_paths, post_text_content)
        if result_id:
            article["image_source"] = "multi_image"
            log("Publish path: multi_success")
            return result_id
        log("Coklu paylasim basarisiz, tek gorsele dusulecek", "WARNING")

    if image_count >= 1:
        post_photo_fn = getattr(fb_platform, "post_photo", None)
        if callable(post_photo_fn):
            log("Deneme 2/3: Tek gorsel paylasimi...")
            try:
                result_id = post_photo_fn(image_paths[0], post_text_content)
            except Exception as exc:
                log(f"post_photo hata: {exc}", "WARNING")
                result_id = None

            if result_id:
                if image_count >= 2:
                    article["image_source"] = "single_fallback_from_multi"
                log("Publish path: single_success")
                return result_id

    post_text_fn = getattr(fb_platform, "post_text", None)
    if callable(post_text_fn):
        log("Deneme 3/3: Metin paylasimi...")
        try:
            result_id = post_text_fn(post_text_content)
        except Exception as exc:
            log(f"post_text hata: {exc}", "WARNING")
            result_id = None

        if result_id:
            article["image_source"] = "fallback_text"
            log("Publish path: text_success")
            return result_id

    log("Publish path: all_failed", "ERROR")
    return None


def _retry_text_publish(post_text_content: str) -> Optional[str]:
    post_text_fn = getattr(fb_platform, "post_text", None)
    if not callable(post_text_fn):
        return None

    try:
        return post_text_fn(post_text_content)
    except Exception as exc:
        log(f"Retry post_text hata: {exc}", "WARNING")
        return None


def _build_health_report() -> str:
    for stage_name in ("fetch", "score", "write", "image"):
        try:
            stage_data = get_stage(stage_name)
            if stage_data.get("status") == "error":
                error_text = (stage_data.get("error", "") or "unknown error").strip()
                code = f"{stage_name.upper()}_ERROR"
                return f"Hata: {stage_name} ({code}) - {error_text}"
        except Exception:
            continue
    return "Saglikli"


def _send_telegram_notification(article: dict, action_count: int, share_count: int, health_report: str) -> bool:
    title = (article.get("title", "") or "").strip()
    link = (article.get("link", "") or "").strip()
    score = article.get("score", 0)

    message = (
        f"Baslik: {title or 'Bilinmiyor'}\n"
        f"Link: {link or '-'}\n"
        f"Skor: {score}\n"
        f"Durum: A{action_count}-P{share_count}\n"
        f"Saglik: {health_report}"
    )
    return tg_platform.send_message(message)


def _build_publish_output(article: dict, post_id: str, image_source: str, image_count: int, dry_run: bool) -> dict:
    return {
        "success": True,
        "post_id": post_id,
        "article_title": article.get("title", ""),
        "image_source": image_source,
        "image_count": image_count,
        "dry_run": dry_run,
    }


def run() -> bool:
    log("-" * 55)
    log("agent_publisher basliyor")
    log("-" * 55)

    image_stage = get_stage("image")
    if image_stage.get("status") != "done":
        log("image asamasi tamamlanmamis", "ERROR")
        set_stage("publish", "error", error="image asamasi tamamlanmamis")
        return False

    image_output = image_stage.get("output", {})
    article = image_output.get("article", {})
    post_text_content = image_output.get("post_text", "")
    image_source = image_output.get("image_source", "unknown")
    image_paths = _collect_valid_image_paths(image_output)

    if not article:
        set_stage("publish", "error", error="Image ciktisinda haber yok")
        return False
    if not post_text_content:
        set_stage("publish", "error", error="Post metni yok")
        return False

    log(f"Paylasilacak haber: {article.get('title', '')[:80]}")
    log(f"Image source: {image_source}, image count: {len(image_paths)}")
    manual_priority = bool(article.get("manual_priority", False))
    if manual_priority:
        log("Manual priority aktif: telegram icerigi once paylasilacak")

    set_stage("publish", "running")

    try:
        settings = load_config("settings")
        posting_settings = settings.get("posting", {})
        max_daily_posts = int(posting_settings.get("max_daily_posts", 9))
        skip_probability = int(posting_settings.get("skip_probability_percent", 10))
        random_delay_max = int(posting_settings.get("random_delay_max_minutes", 8))

        dry_run = _is_dry_run(settings)
        random_delay_enabled = _is_random_delay_enabled()
        random_skip_enabled = _is_random_skip_enabled()

        posted_data = get_posted_news()
        if not manual_priority:
            if not _check_daily_limit(posted_data, max_daily_posts):
                set_stage("publish", "error", error="Gunluk limit doldu")
                return False
        else:
            log("Gunluk limit kontrolu atlandi (manual_priority=true)")

        if not dry_run and (not manual_priority) and random_skip_enabled and not _check_skip_probability(skip_probability):
            set_stage("publish", "error", error="Rastgele atlama")
            return False
        if not dry_run and not manual_priority and not random_skip_enabled:
            log("Rastgele atlama devre disi (ENABLE_RANDOM_SKIP=false)")
        if not dry_run and manual_priority:
            log("Rastgele atlama atlandi (manual_priority=true)")

        if dry_run:
            log("DRY RUN: Gercek Facebook paylasimi yapilmayacak")
            output = _build_publish_output(
                article=article,
                post_id="dryrun_000000000_111111111",
                image_source=image_source,
                image_count=len(image_paths),
                dry_run=True,
            )
            set_stage("publish", "done", output=output)
            log("DRY RUN tamamlandi")
            return True

        if manual_priority:
            log("Rastgele bekleme atlandi (manual_priority=true)")
        elif random_delay_enabled and random_delay_max > 0:
            delay_seconds = random.randint(0, random_delay_max * 60)
            log(f"Rastgele bekleme: {delay_seconds} sn")
            time.sleep(delay_seconds)
        elif not random_delay_enabled:
            log("Rastgele bekleme devre disi (ENABLE_RANDOM_DELAY=false)")

        post_id = _publish_to_facebook(article, post_text_content, image_paths)
        if post_id is None:
            log(f"Ilk tur basarisiz, {_RETRY_DELAY} sn sonra metin retry", "WARNING")
            time.sleep(_RETRY_DELAY)
            post_id = _retry_text_publish(post_text_content)
            if post_id:
                article["image_source"] = "retry_text"
                log("Publish path: retry_text_success")

        if post_id is None:
            set_stage("publish", "error", error="Facebook paylasimi basarisiz")
            return False

        final_image_source = article.get("image_source", image_source)
        final_image_count = len(image_paths) if final_image_source != "fallback_text" else 0
        _record_posted(article, post_id, final_image_source, final_image_count)

        fresh_data = get_posted_news()
        action_count = get_today_action_count(fresh_data)
        share_count = get_today_post_count(fresh_data)
        health_report = _build_health_report()

        telegram_ok = _send_telegram_notification(article, action_count, share_count, health_report)
        if not telegram_ok:
            log("Telegram bildirimi gonderilemedi, akis devam ediyor", "WARNING")

        output = _build_publish_output(
            article=article,
            post_id=post_id,
            image_source=final_image_source,
            image_count=final_image_count,
            dry_run=False,
        )

        if manual_priority and image_source == "telegram":
            tg_platform.finalize_consumed_shareable_content(image_output)

        set_stage("publish", "done", output=output)
        log("BASARIYLA PAYLASILDI")
        return True

    except Exception as exc:
        log(f"agent_publisher kritik hata: {exc}", "ERROR")
        set_stage("publish", "error", error=str(exc))
        return False


if __name__ == "__main__":
    from core.state_manager import init_pipeline

    log("=== agent_publisher.py modul testi basliyor ===")

    init_pipeline("test-publisher")
    fake_article = {
        "title": "Test Haber",
        "link": "https://test.com/haber1",
        "topic_fingerprint": "test-haber",
        "image_source": "article_or_rss",
    }
    fake_post_text = "Test post"

    set_stage(
        "image",
        "done",
        output={
            "article": fake_article,
            "post_text": fake_post_text,
            "image_path": "",
            "image_paths": [],
            "image_source": "article_or_rss",
            "image_count": 0,
        },
    )

    run()
    log("=== agent_publisher.py modul testi tamamlandi ===")
