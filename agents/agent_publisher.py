"""
agents/agent_publisher.py - Yayinci Ajani (Instagram-only versiyon)
Orijinal yapı korunarak sadece Facebook çağrıları Instagram ile değiştirildi.
"""

import os
import random
import time
from typing import Optional

from core.config_loader import load_config
from core.helpers import (
    cleanup_shared_variant_cooldowns,
    generate_topic_fingerprint,
    get_posted_news,
    get_today_action_count,
    get_today_post_count,
    get_today_str,
    get_turkey_now,
    increment_weekly_share,
    is_duplicate_article,
    record_shared_variant_cooldown,
    save_last_check_time,
    save_posted_news,
)
from core.logger import log
from core.state_manager import get_stage, set_stage
from platforms import instagram as ig_platform
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


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
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


def _score_based_skip_percent(score: int, fallback_percent: int) -> int:
    if score >= 95:
        return 0
    if score >= 90:
        return 2
    if score >= 85:
        return 5
    if score >= 80:
        return 10
    if score < 80:
        return 100
    return fallback_percent


def _check_skip_probability(skip_percent: int, score: int = 0) -> tuple[bool, str]:
    effective_percent = _score_based_skip_percent(score, skip_percent)
    if effective_percent >= 100:
        reason = f"score_below_publish_skip(score={score})"
        log(f"Skora bagli atlama: {reason}", "INFO")
        return False, reason
    if effective_percent <= 0:
        log(f"Skora bagli atlama: score={score}, esik=0 -> devam")
        return True, ""
    roll = random.randint(1, 100)
    if roll <= effective_percent:
        reason = f"score_based_skip(score={score}, roll={roll}, threshold={effective_percent})"
        log(f"Skora bagli atlama: {reason} -> atlaniyor", "INFO")
        return False, reason
    log(f"Skora bagli atlama: score={score}, zar={roll}, esik={effective_percent} -> devam")
    return True, ""


def _build_new_post_record(article: dict, post_id: str, image_source: str, image_count: int, story_id: Optional[str] = None) -> dict:
    fingerprint = article.get("topic_fingerprint", "") or generate_topic_fingerprint(article.get("title", ""))
    return {
        "title": article.get("title", "Baslik yok"),
        "url": article.get("link", ""),
        "topic_fingerprint": fingerprint,
        "source": article.get("source_name", "Bilinmeyen"),
        "score": article.get("score", 0),
        "trend_count": article.get("trend_count", 1),
        "posted_at": get_turkey_now().isoformat(),
        "ig_post_id": post_id,
        "ig_story_id": story_id,
        "image_source": image_source,
        "image_count": image_count,
    }


def _record_posted(article: dict, post_id: str, image_source: str, image_count: int, story_id: Optional[str] = None) -> None:
    if not _is_persist_state_enabled():
        log("State persistence kapali (PERSIST_STATE=false), posted kaydi yazilmadi")
        return

    try:
        posted_data = get_posted_news()
        today_str = get_today_str()

        posts_list = posted_data.get("posts", [])
        daily_counts = posted_data.get("daily_counts", {})

        posts_list.append(_build_new_post_record(article, post_id, image_source, image_count, story_id))
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

    raw_paths = image_output.get("image_paths", [])
    if isinstance(raw_paths, list):
        for item in raw_paths:
            _add_if_valid(item)

    primary_path = image_output.get("image_path", "")
    if isinstance(primary_path, str) and primary_path and not os.path.exists(primary_path):
        log(f"Primary image path not found: {primary_path}", "WARNING")
    _add_if_valid(primary_path)

    return collected


def _prefer_text_only_on_fallback(image_source: str, image_paths: list[str]) -> list[str]:
    if (image_source or "").strip().lower() == "fallback":
        if image_paths:
            log("Fallback gorsel tespit edildi, text-only paylasima geciliyor", "INFO")
        return []
    return image_paths


def _log_publish_preview(article: dict, post_text_content: str, image_paths: list[str]) -> None:
    image_source = article.get("image_source", "unknown")
    image_count = len(image_paths)

    log("=" * 55)
    log(f"Instagram'a paylasiliyor: {article.get('title', '')[:80]}")
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
        log("Final image listesi bos -> text-only olasiligi", "INFO")

    preview = post_text_content[:220] + ("..." if len(post_text_content) > 220 else "")
    log(f"Post onizleme:\n{preview}")


def _publish_to_instagram(article: dict, post_text_content: str, image_paths: list[str]) -> Optional[dict]:
    _log_publish_preview(article, post_text_content, image_paths)

    if not image_paths:
        log("Gorsel yok, Instagram paylasimi yapilamiyor", "ERROR")
        return None

    image_url = image_paths[0]

    try:
        ig = ig_platform.InstagramPlatform()
        result = ig.post_with_story(image_url=image_url, post_caption=post_text_content)
        return result
    except Exception as exc:
        log(f"Instagram paylasim hatasi: {exc}", "ERROR")
        return None


def _send_telegram_notification(
    article: dict,
    action_count: int,
    share_count: int,
    health_report: str,
    image_source: str,
    image_count: int,
    post_id: str,
    story_id: Optional[str] = None,
) -> bool:
    title = (article.get("title", "") or "").strip()
    link = (article.get("link", "") or "").strip()
    score = article.get("score", 0)

    story_status = f"Story ID: {story_id}" if story_id else "Story: BASARISIZ veya ATLANDI"

    message = (
        f"Paylasim yapildi (Instagram).\n\n"
        f"Gunün {action_count}. tetiklemesi\n"
        f"Gunün {share_count}. paylasimi\n"
        f"\n"
        f"Paylasim Tipi: Instagram Post + Story\n"
        f"Secilen haber:\n"
        f"Baslik: {title or 'Bilinmiyor'}\n"
        f"Link: {link or '-'}\n"
        f"Toplam Skor: {score}\n\n"
        f"Instagram Post ID: {post_id}\n"
        f"{story_status}\n"
        f"Gorsel: {image_source} ({image_count})\n"
        f"Saglik: {health_report}"
    )
    return tg_platform.send_message(message)


def _build_publish_output(
    article: dict,
    post_id: str,
    image_source: str,
    image_count: int,
    dry_run: bool,
    skipped: bool = False,
    skip_reason: str = "",
    story_id: Optional[str] = None,
) -> dict:
    output = {
        "success": not skipped,
        "post_id": post_id,
        "story_id": story_id,
        "article_title": article.get("title", ""),
        "image_source": image_source,
        "image_count": image_count,
        "dry_run": dry_run,
        "skipped": skipped,
    }
    if skip_reason:
        output["skip_reason"] = skip_reason
    return output


def _resolve_final_image_count(final_image_source: str, image_paths: list[str]) -> int:
    if final_image_source == "multi_image":
        return len(image_paths)
    if final_image_source in {"single_image", "single_fallback_from_multi"}:
        return 1 if image_paths else 0
    if final_image_source in {"fallback_text", "retry_text"}:
        return 0
    return len(image_paths)


def run() -> bool:
    log("-" * 55)
    log("agent_publisher basliyor (Instagram-only)")
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
    image_paths = _prefer_text_only_on_fallback(image_source, image_paths)

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
        max_daily_posts = _safe_int(posting_settings.get("max_daily_posts", 9), 9)
        skip_probability = _safe_int(posting_settings.get("skip_probability_percent", 10), 10)
        random_delay_max = _safe_int(posting_settings.get("random_delay_max_minutes", 8), 8)

        skip_probability = max(0, min(skip_probability, 100))
        random_delay_max = max(0, random_delay_max)

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

        skip_allowed, skip_reason = True, ""
        if not dry_run and (not manual_priority) and random_skip_enabled:
            skip_allowed, skip_reason = _check_skip_probability(
                skip_probability, _safe_int(article.get("score", 0), 0)
            )
        if not dry_run and (not manual_priority) and random_skip_enabled and not skip_allowed:
            output = _build_publish_output(
                article=article,
                post_id="skipped_score_based",
                image_source=image_source,
                image_count=len(image_paths),
                dry_run=False,
                skipped=True,
                skip_reason=skip_reason or "score_based_skip",
            )
            set_stage("publish", "done", output=output)
            log("Skora bagli atlama nedeniyle bu tur publish skip edildi")
            return True

        if not dry_run and not manual_priority and not random_skip_enabled:
            log("Rastgele atlama devre disi (ENABLE_RANDOM_SKIP=false)")
        if not dry_run and manual_priority:
            log("Rastgele atlama atlandi (manual_priority=true)")

        if dry_run:
            log("DRY RUN: Gercek Instagram paylasimi yapilmayacak")
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

        # === INSTAGRAM PAYLAŞIMI ===
        publish_result = _publish_to_instagram(article, post_text_content, image_paths)
        if not publish_result or not publish_result.get("post_id"):
            set_stage("publish", "error", error="Instagram paylasimi basarisiz")
            return False

        post_id = publish_result.get("post_id")
        story_id = publish_result.get("story_id")
        final_image_source = article.get("image_source", image_source)
        final_image_count = _resolve_final_image_count(final_image_source, image_paths)

        _record_posted(article, post_id, final_image_source, final_image_count, story_id)
        _record_shared_variant_cooldowns(article)

        fresh_data = get_posted_news()
        action_count = get_today_action_count(fresh_data)
        share_count = get_today_post_count(fresh_data)
        health_report = _build_health_report()

        telegram_ok = _send_telegram_notification(
            article=article,
            action_count=action_count,
            share_count=share_count,
            health_report=health_report,
            image_source=final_image_source,
            image_count=final_image_count,
            post_id=post_id,
            story_id=story_id,
        )
        if not telegram_ok:
            log("Telegram bildirimi gonderilemedi, akis devam ediyor", "WARNING")

        output = _build_publish_output(
            article=article,
            post_id=post_id,
            image_source=final_image_source,
            image_count=final_image_count,
            dry_run=False,
            story_id=story_id,
        )

        if manual_priority and image_source == "telegram":
            tg_platform.finalize_consumed_shareable_content(image_output)

        set_stage("publish", "done", output=output)
        log("BASARIYLA INSTAGRAM'A PAYLASILDI (Post + Story)")
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
