"""
agents/agent_publisher.py - Yayinci Ajani (v4.3)

v4.3:
  - Score tabanli skip politikasi yeniden duzenlendi (scorer ile uyumlu):
      <70 => %100 skip
      70-79 => %2 skip
      80-89 => %1 skip
      >=90 => %0 skip
  - publish_score threshold scorer ile senkronize edildi (70).

v4.2:
  - Score tabanli skip politikasi sabitlendi:
      <80 => %100 skip
      80-84 => %3 skip
      85-89 => %2 skip
      diger => %0 skip
  - ENABLE_RANDOM_SKIP akisi kaldirildi (boşa dusen kontrol temizlendi).
  - fallback/logo gorsel geldiğinde text-only paylasima gecilir.
  - image_source NameError ve cagri uyumlulugu duzeltmeleri korunur.
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


def _is_persist_state_enabled() -> bool:
    return _get_env_bool("PERSIST_STATE", True)


def _check_daily_limit(posted_data: dict, max_daily_posts: int) -> bool:
    today_count = get_today_post_count(posted_data)
    if today_count >= max_daily_posts:
        log(f"Gunluk limit doldu: {today_count}/{max_daily_posts}", "WARNING")
        return False
    log(f"Gunluk limit: {today_count}/{max_daily_posts} (devam)")
    return True


def _score_based_skip_percent(score: int) -> int:
    """
    DÜZELTME v4.3: Scorer ile uyumlu hale getirildi.
    - Score < 70 → %100 skip
    - Score 70-79 → %2 skip
    - Score 80-89 → %1 skip
    - Score >= 90 → %0 skip
    """
    if score < 70:
        return 100
    if 70 <= score <= 79:
        return 2
    if 80 <= score <= 89:
        return 1
    return 0


def _check_skip_probability(score: int = 0) -> tuple[bool, str]:
    effective_percent = _score_based_skip_percent(score)
    if effective_percent >= 100:
        reason = f"score_below_70_skip(score={score})"
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
    # Foto/video yoksa logo/fallback görseli basmak yerine sadece metin paylaş.
    if (image_source or "").strip().lower() == "fallback":
        if image_paths:
            log("Fallback gorsel tespit edildi, text-only paylasima geciliyor", "INFO")
        return []
    return image_paths


def _try_call_multi_fn(fn, image_paths: list[str], post_text_content: str) -> Optional[str]:
    call_patterns = [
        lambda: fn(image_paths, post_text_content),
        lambda: fn(post_text_content, image_paths),
        lambda: fn(image_paths=image_paths, message=post_text_content),
        lambda: fn(paths=image_paths, message=post_text_content),
        lambda: fn(photo_paths=image_paths, message=post_text_content),
        lambda: fn(images=image_paths, message=post_text_content),
    ]

    for call_idx, call in enumerate(call_patterns, start=1):
        try:
            result = call()
            if result:
                log(f"Coklu gorsel cagri basarili (pattern={call_idx})")
                return result
        except TypeError:
            continue
        except Exception as exc:
            log(f"Coklu gorsel cagri hatasi (pattern={call_idx}): {exc}", "WARNING")

    return None


def _try_post_multi_photos(image_paths: list[str], post_text_content: str) -> Optional[str]:
    for fn_name in ("post_photos", "post_multi_photo", "post_album"):
        fn = getattr(fb_platform, fn_name, None)
        if not callable(fn):
            continue

        try:
            log(f"Coklu gorsel fonksiyonu deneniyor: facebook.{fn_name}()")
            result = _try_call_multi_fn(fn, image_paths, post_text_content)
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
        log("Final image listesi bos -> text-only olasiligi", "INFO")

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
                else:
                    article["image_source"] = "single_image"
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


def _resolve_publish_mode(article: dict, image_source: str) -> str:
    if bool(article.get("manual_priority", False)) or image_source == "telegram":
        return "MANUEL"
    return "OTOMATIK"


def _queue_status_text() -> str:
    try:
        info = tg_platform.get_pending_shareable_queue_info()
        ready = int(info.get("ready_count", 0))
        total = int(info.get("total_groups", 0))
        if ready > 0:
            return f"Var ({ready} hazir / {total} toplam grup)"
        return f"Yok ({total} toplam grup)"
    except Exception as exc:
        log(f"Kuyruk durumu okunamadi: {exc}", "WARNING")
        return "Bilinmiyor"


def _format_score_breakdown(breakdown: dict) -> str:
    if not isinstance(breakdown, dict) or not breakdown:
        return "Yok"
    labels = {
        "guncellik": "Güncellik",
        "etkilesim_potansiyeli": "Etkileşim Potansiyeli",
        "benzersizlik": "Benzersizlik",
        "gundem_gucu": "Gündem Gücü",
        "paylasilabilirlik": "Paylaşılabilirlik",
    }
    return "\n".join(f"- {labels.get(k, k)}: {v}" for k, v in breakdown.items())


def _format_top_articles() -> str:
    try:
        score_output = (get_stage("score").get("output", {}) or {})
        top_articles = score_output.get("top_articles", [])
        if not isinstance(top_articles, list) or not top_articles:
            return "Yok"
        lines = []
        for idx, item in enumerate(top_articles[:3], start=1):
            lines.append(f"#{idx} {item.get('title', 'Bilinmiyor')[:90]}\nSkor: {item.get('score', 0)}")
        return "\n\n".join(lines)
    except Exception:
        return "Yok"


def _workflow_timing_line() -> str:
    started_tr = (os.environ.get("WORKFLOW_STARTED_AT_TR", "") or "").strip()
    started_utc = (os.environ.get("WORKFLOW_STARTED_AT_UTC", "") or "").strip()
    if started_tr and started_utc:
        return f"Workflow baslangici: {started_tr} TR ({started_utc} UTC)"
    if started_tr:
        return f"Workflow baslangici: {started_tr} TR"
    if started_utc:
        return f"Workflow baslangici: {started_utc} UTC"
    return "Workflow baslangici: bilinmiyor"


def _record_shared_variant_cooldowns(article: dict) -> None:
    if not _is_persist_state_enabled():
        return

    try:
        score_output = (get_stage("score").get("output", {}) or {})
        candidates = score_output.get("cooldown_candidates", [])
        if not isinstance(candidates, list) or not candidates:
            return

        posted_data = get_posted_news()
        recorded = 0
        for candidate in candidates:
            if not isinstance(candidate, dict) or not candidate.get("title"):
                continue
            if not is_duplicate_article(article, candidate):
                continue
            record_shared_variant_cooldown(posted_data, article, candidate)
            recorded += 1

        if recorded:
            cleanup_shared_variant_cooldowns(posted_data, 72)
            save_posted_news(posted_data)
            log(f"Paylasilan haber varyasyon cooldown kaydi: count={recorded}")
    except Exception as exc:
        log(f"Paylasilan haber varyasyon cooldown kaydi yazilamadi: {exc}", "WARNING")


def _send_telegram_notification(
    article: dict,
    action_count: int,
    share_count: int,
    health_report: str,
    image_source: str,
    image_count: int,
) -> bool:
    title = (article.get("title", "") or "").strip()
    link = (article.get("link", "") or "").strip()
    score = article.get("score", 0)

    publish_mode = _resolve_publish_mode(article, image_source)
    queue_state = _queue_status_text()

    message = (
        f"Paylaşım yapıldı.\n\n"
        f"Günün {action_count}. tetiklemesi\n"
        f"Günün {share_count}. paylaşımı\n"
        f"{_workflow_timing_line()}\n\n"
        f"Paylaşım Tipi: {publish_mode}\n"
        f"Seçilen haber:\n"
        f"Başlık: {title or 'Bilinmiyor'}\n"
        f"Link: {link or '-'}\n"
        f"Toplam Skor: {score}\n\n"
        f"Alt skorlar:\n{_format_score_breakdown(article.get('score_breakdown', {}))}\n\n"
        f"İlk 3 haber:\n{_format_top_articles()}\n\n"
        f"Görsel: {image_source} ({image_count})\n"
        f"Sağlık: {health_report}\n"
        f"Kuyruk Durumu: {queue_state}"
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
) -> dict:
    output = {
        "success": not skipped,
        "post_id": post_id,
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
        random_delay_max = _safe_int(posting_settings.get("random_delay_max_minutes", 8), 8)

        random_delay_max = max(0, random_delay_max)

        dry_run = _is_dry_run(settings)
        random_delay_enabled = _is_random_delay_enabled()

        posted_data = get_posted_news()
        if not manual_priority:
            if not _check_daily_limit(posted_data, max_daily_posts):
                set_stage("publish", "error", error="Gunluk limit doldu")
                return False
        else:
            log("Gunluk limit kontrolu atlandi (manual_priority=true)")

        if not dry_run and (not manual_priority):
            skip_allowed, skip_reason = _check_skip_probability(
                _safe_int(article.get("score", 0), 0)
            )
            if not skip_allowed:
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
        final_image_count = _resolve_final_image_count(final_image_source, image_paths)

        _record_posted(article, post_id, final_image_source, final_image_count)
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
        )
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
