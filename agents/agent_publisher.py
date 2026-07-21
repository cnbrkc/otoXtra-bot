"""
agents/agent_publisher.py - Yayinci Ajani (v6.3 - Story Checkpoint Stabil)
"""

import os
import random
import tempfile
import requests
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
    is_duplicate_article,
    record_shared_variant_cooldown,
    save_last_check_time,
    save_posted_news,
)
from core.logger import log
from core.state_manager import get_stage, set_stage
from platforms import facebook as fb_platform
from platforms import telegram as tg_platform
from platforms import threads as threads_platform
from platforms import instagram as ig_platform


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


def _is_image_test_mode() -> bool:
    return _get_env_bool("IMAGE_TEST_MODE", False)


def _is_all_test_mode() -> bool:
    return _get_env_bool("TUM_PLATFORMLAR_TEST", False)


def _is_fb_test_mode() -> bool:
    return _get_env_bool("SADECE_FACEBOOK_TEST", False)


def _is_threads_test_mode() -> bool:
    return _get_env_bool("SADECE_THREADS_TEST", False)


def _is_persist_state_enabled() -> bool:
    return _get_env_bool("PERSIST_STATE", True)


def _check_daily_limit(posted_data: dict, max_daily_posts: int) -> bool:
    today_count = get_today_post_count(posted_data)
    if today_count >= max_daily_posts:
        log(f"Gunluk limit doldu: {today_count}/{max_daily_posts}", "WARNING")
        return False
    log(f"Gunluk limit: {today_count}/{max_daily_posts} (devam)")
    return True


def _get_publish_threshold() -> int:
    try:
        scoring_config = load_config("scoring")
        thresholds = scoring_config.get("thresholds", {}) if isinstance(scoring_config, dict) else {}
        publish_score = _safe_int(thresholds.get("publish_score", 35), 35)
        slow_day_score = _safe_int(thresholds.get("slow_day_score", 25), 25)
        posted_data = get_posted_news()
        today_post_count = get_today_post_count(posted_data)
        threshold = slow_day_score if today_post_count < 2 else publish_score
        return threshold
    except Exception:
        return 35


def _score_based_skip_percent(score: int) -> int:
    threshold = _get_publish_threshold()
    if score < threshold:
        return 100
    margin = score - threshold
    if margin < 10:
        return 2
    if margin < 20:
        return 1
    return 0


def _check_skip_probability(score: int = 0) -> tuple[bool, str]:
    effective_percent = _score_based_skip_percent(score)
    if effective_percent >= 100:
        return False, "score_below_threshold"
    if effective_percent <= 0:
        return True, ""
    roll = random.randint(1, 100)
    if roll <= effective_percent:
        return False, "score_based_skip"
    return True, ""


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
        log(f"Kayit guncellendi: {article.get('title', '')[:60]} (bugun toplam: {daily_counts[today_str]})")
    except Exception as exc:
        log(f"Kayit guncellenirken hata: {exc}", "WARNING")


def _collect_valid_image_paths(image_output: dict) -> list[str]:
    collected: list[str] = []
    seen_keys: set[str] = set()

    def _add_if_valid(path: str) -> None:
        if not isinstance(path, str) or not path:
            return
        if not os.path.exists(path):
            return
        key = os.path.normcase(os.path.realpath(path))
        if key in seen_keys:
            return
        seen_keys.add(key)
        collected.append(path)

    raw_paths = image_output.get("image_paths", [])
    if isinstance(raw_paths, list):
        for item in raw_paths:
            _add_if_valid(item)
    _add_if_valid(image_output.get("image_path", ""))
    return collected


def _prefer_text_only_on_fallback(image_source: str, image_paths: list[str]) -> list[str]:
    if (image_source or "").strip().lower() in {"no_image", "fallback"}:
        return []
    return image_paths


def _try_call_multi_fn(fn, image_paths: list[str], post_text_content: str) -> Optional[str]:
    call_patterns = [
        lambda: fn(image_paths, post_text_content),
        lambda: fn(post_text_content, image_paths),
        lambda: fn(image_paths=image_paths, message=post_text_content),
    ]
    for call in call_patterns:
        try:
            result = call()
            if result:
                return result
        except TypeError:
            continue
        except Exception:
            pass
    return None


def _try_post_multi_photos(image_paths: list[str], post_text_content: str) -> Optional[str]:
    for fn_name in ("post_photos", "post_multi_photo", "post_album"):
        fn = getattr(fb_platform, fn_name, None)
        if callable(fn):
            result = _try_call_multi_fn(fn, image_paths, post_text_content)
            if result:
                return result
    return None


def _try_post_single_photo(image_path: str, post_text_content: str) -> Optional[str]:
    post_photo_fn = getattr(fb_platform, "post_photo", None)
    if callable(post_photo_fn):
        return post_photo_fn(image_path, post_text_content)
    return None


def _try_post_text_only(post_text_content: str) -> Optional[str]:
    post_text_fn = getattr(fb_platform, "post_text", None)
    if callable(post_text_fn):
        return post_text_fn(post_text_content)
    return None


def _publish_to_facebook(article: dict, post_text_content: str, image_paths: list[str]) -> Optional[str]:
    image_count = len(image_paths)
    if image_count >= 2:
        post_id = _try_post_multi_photos(image_paths, post_text_content)
        if post_id:
            return post_id
    if image_count >= 1:
        post_id = _try_post_single_photo(image_paths[0], post_text_content)
        if post_id:
            return post_id
    return _try_post_text_only(post_text_content)


def _build_health_report() -> str:
    for stage_name in ("fetch", "score", "write", "image"):
        stage_data = get_stage(stage_name)
        if stage_data.get("status") == "error":
            return f"Hata: {stage_name} - {stage_data.get('error', '')}"
    return "Saglikli"


def _workflow_timing_line() -> str:
    started_tr = os.environ.get("WORKFLOW_STARTED_AT_TR", "")
    return f"Workflow baslangici: {started_tr} TR" if started_tr else "Zaman bilinmiyor"


def _record_shared_variant_cooldowns(article: dict) -> None:
    if not _is_persist_state_enabled():
        return
    try:
        score_output = (get_stage("score").get("output", {}) or {})
        candidates = score_output.get("cooldown_candidates", [])
        if not candidates:
            return
        posted_data = get_posted_news()
        for candidate in candidates:
            if is_duplicate_article(article, candidate):
                record_shared_variant_cooldown(posted_data, article, candidate)
        save_posted_news(posted_data)
    except Exception:
        pass


def _build_story_checkpoint_line(status_obj: dict) -> str:
    enabled = bool(status_obj.get("enabled", True))
    attempted = bool(status_obj.get("attempted", False))
    success = bool(status_obj.get("success", False))
    sid = (status_obj.get("id", "") or "").strip()
    error = (status_obj.get("error", "") or "").strip()

    if not enabled:
        return "disabled"
    if not attempted:
        return "not_attempted"
    if success:
        return f"success ({sid})" if sid else "success"
    return f"failed ({error})" if error else "failed"


def _send_telegram_notification(
    article: dict,
    action_count: int,
    share_count: int,
    health_report: str,
    image_source: str,
    image_count: int,
    fb_ok: bool,
    threads_ok: bool,
    story_status: dict,
) -> bool:
    title = (article.get("title", "") or "").strip()
    link = (article.get("link", "") or "").strip()
    score = article.get("score", 0)

    fb_feed = "OK" if fb_ok else "FAIL"
    threads_feed = "OK" if threads_ok else "FAIL"
    ig_story_line = _build_story_checkpoint_line(story_status.get("instagram", {}))
    fb_story_line = _build_story_checkpoint_line(story_status.get("facebook", {}))

    message = (
        "Paylasim Yapildi.\n\n"
        f"Gunun {action_count}. tetiklemesi\n"
        f"Gunun {share_count}. paylasimi\n"
        f"{_workflow_timing_line()}\n\n"
        "Platform Durumu:\n"
        f"- Facebook Feed: {fb_feed}\n"
        f"- Threads: {threads_feed}\n"
        f"- Instagram Story: {ig_story_line}\n"
        f"- Facebook Story: {fb_story_line}\n\n"
        "Secilen haber:\n"
        f"Baslik: {title or 'Bilinmiyor'}\n"
        f"Link: {link or '-'}\n"
        f"Toplam Skor: {score}\n"
        f"Gorsel: {image_source} ({image_count})\n"
        f"Saglik: {health_report}"
    )
    return tg_platform.send_message(message)


def _send_test_image_to_telegram(image_path: str, article_title: str) -> bool:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id or not image_path or not os.path.exists(image_path):
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    caption = f"GORSEL TEST MODU\n\nBaslik: {article_title}"
    try:
        with open(image_path, "rb") as photo:
            resp = requests.post(
                url,
                files={"photo": photo},
                data={"chat_id": chat_id, "caption": caption},
                timeout=30,
            )
            return resp.status_code == 200
    except Exception:
        return False


def _build_story_card(post_text_content: str, base_image_path: str) -> str | None:
    if not base_image_path or not os.path.exists(base_image_path):
        return None
    try:
        from core.image_generator import create_social_card
        from PIL import Image

        card_path = tempfile.NamedTemporaryFile(suffix="_story_card.jpg", delete=False).name
        create_social_card(post_text_content, base_image_path, card_path)

        if not os.path.exists(card_path):
            return None

        with Image.open(card_path) as img:
            rgb = img.convert("RGB")
            rgb.save(card_path, format="JPEG", quality=95, optimize=True, subsampling=0)

        return card_path
    except Exception as exc:
        log(f"Story card olusturma hatasi: {exc}", "ERROR")
        return None


def run() -> bool:
    log("-" * 55)
    log("agent_publisher basliyor")
    log("-" * 55)

    image_stage = get_stage("image")
    if image_stage.get("status") != "done":
        set_stage("publish", "error", error="image asamasi tamamlanmamis")
        return False

    image_output = image_stage.get("output", {})
    article = image_output.get("article", {})
    post_text_content = image_output.get("post_text", "")
    image_source = image_output.get("image_source", "unknown")
    image_paths = _collect_valid_image_paths(image_output)
    image_paths = _prefer_text_only_on_fallback(image_source, image_paths)

    if not article or not post_text_content:
        set_stage("publish", "error", error="Haber veya post metni yok")
        return False

    if _is_image_test_mode():
        log("GORSEL TEST MODU AKTIF: Paylasim yapilmayacak.", "INFO")
        test_image_path = image_paths[0] if image_paths else ""
        _send_test_image_to_telegram(test_image_path, article.get("title", "Test"))
        set_stage("publish", "done", output={"skipped": True, "skip_reason": "image_test_mode"})
        return True

    manual_priority = bool(article.get("manual_priority", False))
    set_stage("publish", "running")

    fb_success = False
    threads_success = False
    final_image_source = image_source
    final_image_count = len(image_paths)
    fb_post_id = None

    story_status = {
        "instagram": {"enabled": True, "attempted": False, "success": False, "id": "", "error": ""},
        "facebook": {"enabled": True, "attempted": False, "success": False, "id": "", "error": ""},
    }

    story_card_path = None

    try:
        settings = load_config("settings")
        posting_settings = settings.get("posting", {})
        max_daily_posts = _safe_int(posting_settings.get("max_daily_posts", 9), 9)

        all_test_mode = _is_all_test_mode()
        fb_test_mode = _is_fb_test_mode()
        threads_test_mode = _is_threads_test_mode()

        posted_data = get_posted_news()
        if not manual_priority and not all_test_mode:
            if not _check_daily_limit(posted_data, max_daily_posts):
                set_stage("publish", "error", error="Gunluk limit doldu")
                return False

        if not all_test_mode and not fb_test_mode and (not manual_priority):
            skip_allowed, _ = _check_skip_probability(_safe_int(article.get("score", 0), 0))
            if not skip_allowed:
                set_stage("publish", "done", output={"skipped": True, "skip_reason": "score_based"})
                return True

        # 1) FACEBOOK FEED
        if all_test_mode or fb_test_mode:
            fb_post_id = "test_mode_skip"
            fb_success = True
        else:
            fb_post_id = _publish_to_facebook(article, post_text_content, image_paths)
            if fb_post_id:
                fb_success = True
                final_image_source = article.get("image_source", image_source)
                final_image_count = len(image_paths)
                _record_posted(article, fb_post_id, final_image_source, final_image_count)
                _record_shared_variant_cooldowns(article)
            else:
                log("[PUBLISH] Facebook paylasimi basarisiz!", "ERROR")

        # 2) THREADS
        try:
            threads_cfg = settings.get("threads", {}) if isinstance(settings, dict) else {}
            if threads_cfg.get("enabled", False) and not all_test_mode and not threads_test_mode:
                mode = threads_cfg.get("mode", "text_only")
                if image_paths and mode == "text_image_carousel" and len(image_paths) >= 2:
                    threads_post_id = threads_platform.post_carousel(post_text_content, image_paths, article=article)
                elif image_paths and mode in ("text_and_image", "text_image_carousel"):
                    threads_post_id = threads_platform.post_with_image(post_text_content, image_paths[0], article=article)
                else:
                    threads_post_id = threads_platform.post_text(post_text_content)

                if threads_post_id:
                    threads_success = True
            elif threads_cfg.get("enabled", False) and (all_test_mode or threads_test_mode):
                threads_success = True
        except Exception as exc:
            log(f"Threads paylasimi hata: {exc}", "WARNING")

        # Story card tek sefer
        if image_paths and os.path.exists(image_paths[0]):
            log("Story card: olusturma basliyor...")
            story_card_path = _build_story_card(post_text_content, image_paths[0])
            if story_card_path:
                log(f"Story card hazir: {story_card_path}")
            else:
                log("Story card olusturulamadi.", "ERROR")

        # 3) INSTAGRAM STORY
        try:
            ig_cfg = settings.get("instagram", {}) if isinstance(settings, dict) else {}
            ig_enabled = bool(ig_cfg.get("enabled", False))
            story_status["instagram"]["enabled"] = ig_enabled

            if ig_enabled and not all_test_mode and not _is_image_test_mode():
                ig_user_id = os.environ.get("IG_USER_ID", "").strip()
                ig_token = os.environ.get("IG_ACCESS_TOKEN", "").strip()

                story_status["instagram"]["attempted"] = True

                if not ig_user_id or not ig_token:
                    story_status["instagram"]["error"] = "credentials_missing"
                    log("IG Story: IG_USER_ID veya IG_ACCESS_TOKEN eksik.", "WARNING")
                elif story_card_path and os.path.exists(story_card_path):
                    ig_story_id = ig_platform.post_story(story_card_path)
                    if ig_story_id:
                        story_status["instagram"]["success"] = True
                        story_status["instagram"]["id"] = str(ig_story_id)
                        log(f"IG Story paylasim basarili: {ig_story_id}")
                    else:
                        story_status["instagram"]["error"] = "publish_failed"
                        log("IG Story: Paylasim basarisiz (post_story None).", "ERROR")
                else:
                    story_status["instagram"]["error"] = "story_card_missing"
                    log("IG Story: Story card yok.", "WARNING")
            elif not ig_enabled:
                log("IG Story: Instagram config'de enabled=false.")
        except Exception as exc:
            story_status["instagram"]["attempted"] = True
            story_status["instagram"]["error"] = f"exception:{exc}"
            log(f"IG Story beklenmeyen hata: {exc}", "WARNING")

        # 4) FACEBOOK STORY
        try:
            fb_cfg = settings.get("facebook", {}) if isinstance(settings, dict) else {}
            fb_story_enabled = bool(fb_cfg.get("enabled", True))
            story_status["facebook"]["enabled"] = fb_story_enabled

            if fb_story_enabled and not all_test_mode and not fb_test_mode:
                fb_page_id = os.environ.get("FB_PAGE_ID", "").strip()
                fb_token = os.environ.get("FB_ACCESS_TOKEN", "").strip()

                story_status["facebook"]["attempted"] = True

                if not fb_page_id or not fb_token:
                    story_status["facebook"]["error"] = "credentials_missing"
                    log("FB Story: FB_PAGE_ID veya FB_ACCESS_TOKEN eksik.", "WARNING")
                elif story_card_path and os.path.exists(story_card_path):
                    fb_story_id = fb_platform.post_story(story_card_path)
                    if fb_story_id:
                        story_status["facebook"]["success"] = True
                        story_status["facebook"]["id"] = str(fb_story_id)
                        log(f"FB Story paylasim basarili: {fb_story_id}")
                    else:
                        story_status["facebook"]["error"] = "publish_failed"
                        log("FB Story: Paylasim basarisiz (post_story None).", "ERROR")
                else:
                    story_status["facebook"]["error"] = "story_card_missing"
                    log("FB Story: Story card yok.", "WARNING")
            elif not fb_story_enabled:
                log("FB Story: Facebook config'de enabled=false.")
        except Exception as exc:
            story_status["facebook"]["attempted"] = True
            story_status["facebook"]["error"] = f"exception:{exc}"
            log(f"FB Story beklenmeyen hata: {exc}", "WARNING")

        # 5) TELEGRAM
        fresh_data = get_posted_news()
        action_count = get_today_action_count(fresh_data)
        share_count = get_today_post_count(fresh_data)
        health_report = _build_health_report()

        _send_telegram_notification(
            article=article,
            action_count=action_count,
            share_count=share_count,
            health_report=health_report,
            image_source=final_image_source,
            image_count=final_image_count,
            fb_ok=fb_success,
            threads_ok=threads_success,
            story_status=story_status,
        )

        if fb_post_id and fb_post_id != "test_mode_skip":
            set_stage("publish", "done", output={"success": True, "post_id": fb_post_id, "story_status": story_status})
            log("BASARIYLA PAYLASILDI (FB/Threads/Story akislari tamamlandi)")
        else:
            set_stage("publish", "done", output={"success": True, "post_id": "test_mode", "story_status": story_status})

        return True

    except Exception as exc:
        log(f"agent_publisher kritik hata: {exc}", "ERROR")
        import traceback
        log(f"Traceback: {traceback.format_exc()}", "ERROR")
        set_stage("publish", "error", error=str(exc))
        return False

    finally:
        if story_card_path and os.path.exists(story_card_path):
            try:
                os.unlink(story_card_path)
            except Exception:
                pass
