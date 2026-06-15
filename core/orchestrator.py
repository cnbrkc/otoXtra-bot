"""
Main orchestrator for otoXtra bot. (v4.2)

v4.2:
  - Min post interval kontrolu saat + dakika destekler hale getirildi.
  - Hata/skip ayrimi guclendirildi.
  - ai_invalid_scale_10 skip sinifina eklendi.
  - Publish stage skip durumu merkezi olarak yakalaniyor.
"""

import os
import traceback

from core.config_loader import load_config
from core.helpers import (
    get_posted_news,
    get_previous_week_key,
    get_today_post_count,
    get_token_remaining_days,
    get_turkey_now,
    get_weekly_stats,
    increment_action_trigger,
    is_weekly_report_sent,
    mark_weekly_report_sent,
    record_weekly_error,
    record_weekly_skip,
    save_last_check_time,
    save_posted_news,
)
from core.logger import log
from core.state_manager import get_stage, init_pipeline, set_stage
from platforms import telegram as tg_platform


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


def _is_persist_state_enabled() -> bool:
    return _get_env_bool("PERSIST_STATE", True)


def _ignore_min_post_interval() -> bool:
    return _get_env_bool("IGNORE_MIN_POST_INTERVAL", False)


def _save_posted_data_if_enabled(data: dict) -> None:
    if _is_persist_state_enabled():
        save_posted_news(data)


def _check_daily_limit(settings: dict, posted_data: dict) -> bool:
    today_count = get_today_post_count(posted_data)
    max_daily = _safe_int(settings.get("posting", {}).get("max_daily_posts", 9), 9)
    log(f"Today posts: {today_count}/{max_daily}")
    return today_count < max_daily


def _resolve_min_interval_minutes(settings: dict) -> int:
    posting = settings.get("posting", {}) if isinstance(settings, dict) else {}
    hours = max(0, _safe_int(posting.get("min_post_interval_hours", 0), 0))
    minutes = max(0, _safe_int(posting.get("min_post_interval_minutes", 0), 0))
    return (hours * 60) + minutes


def _check_min_interval(settings: dict, posted_data: dict) -> bool:
    if _ignore_min_post_interval():
        log("Min post interval kontrolu atlandi (IGNORE_MIN_POST_INTERVAL=true)")
        return True

    min_interval_minutes = _resolve_min_interval_minutes(settings)
    if min_interval_minutes <= 0:
        return True

    posts = posted_data.get("posts", [])
    if not posts:
        return True

    last_posted_at_str = posts[-1].get("posted_at", "")
    if not last_posted_at_str:
        return True

    try:
        from dateutil import parser as date_parser

        last_posted_at = date_parser.isoparse(last_posted_at_str)
        minutes_since = (get_turkey_now() - last_posted_at).total_seconds() / 60.0
        ok = minutes_since >= float(min_interval_minutes)
        log(
            f"Min interval kontrolu: gecen={minutes_since:.1f}dk, "
            f"gereken={min_interval_minutes}dk, sonuc={'devam' if ok else 'bekle'}"
        )
        return ok
    except Exception:
        return True


def _log_stage_error(stage_name: str) -> str:
    try:
        stage = get_stage(stage_name)
        error_msg = stage.get("error", "")
        if error_msg:
            log(f"{stage_name} error: {error_msg}", "WARNING")
            return error_msg
    except Exception:
        pass
    return ""


def _log_source_health() -> None:
    try:
        stage = get_stage("fetch")
        output = stage.get("output") or {}
        source_health = output.get("source_health", {})
        if not source_health:
            return

        ok_count = sum(1 for v in source_health.values() if v.get("status") == "ok")
        err_count = sum(1 for v in source_health.values() if v.get("status") == "error")
        empty_count = sum(1 for v in source_health.values() if v.get("status") == "no_entries")
        disabled_count = sum(1 for v in source_health.values() if v.get("status") == "disabled")

        log(
            f"Source health: ok={ok_count}, error={err_count}, "
            f"empty={empty_count}, disabled={disabled_count}"
        )
    except Exception:
        pass


def _log_image_summary() -> None:
    try:
        stage = get_stage("image")
        if stage.get("status") != "done":
            return

        output = stage.get("output") or {}
        image_source = output.get("image_source", "unknown")
        image_path = output.get("image_path", "")
        image_paths = output.get("image_paths", [])
        image_count = output.get("image_count", 0)

        if not image_count and isinstance(image_paths, list):
            image_count = len(image_paths)

        log(f"Image summary: source={image_source}, count={image_count}, primary={image_path}")
    except Exception:
        pass


def _save_check_time() -> None:
    if not _is_persist_state_enabled():
        log("State persistence kapali (PERSIST_STATE=false), last_check_time kaydedilmeyecek")
        return

    try:
        fresh_data = get_posted_news()
        save_last_check_time(fresh_data)
        save_posted_news(fresh_data)
    except Exception as exc:
        log(f"Could not save last_check_time: {exc}", "WARNING")


def _run_agent(agent_name: str, run_func) -> bool:
    try:
        result = run_func()
        if not result:
            log(f"{agent_name} failed", "ERROR")
        return result
    except Exception as exc:
        log(f"{agent_name} critical error: {exc}", "ERROR")
        log(traceback.format_exc(), "ERROR")
        return False


def _record_error_stat(error_code: str, error_name: str) -> None:
    if not _is_persist_state_enabled():
        return
    try:
        data = get_posted_news()
        record_weekly_error(data, error_code, error_name)
        save_posted_news(data)
    except Exception as exc:
        log(f"Error stat kaydedilemedi: {exc}", "WARNING")


def _record_skip_stat(skip_reason: str) -> None:
    if not _is_persist_state_enabled():
        return
    try:
        data = get_posted_news()
        record_weekly_skip(data, skip_reason)
        save_posted_news(data)
    except Exception as exc:
        log(f"Skip stat kaydedilemedi: {exc}", "WARNING")


def _token_line(token_days) -> str:
    if token_days is None:
        return "Bilinmiyor"
    if token_days < 0:
        return f"Token suresi dolmus ({abs(token_days)} gun gecmis)"
    return f"{token_days} gun"


def _dict_to_lines(values: dict) -> str:
    if not values:
        return "Yok"
    return "\n".join([f"{name}: {count}" for name, count in values.items()])


def _send_weekly_report_if_needed(posted_data: dict) -> None:
    now = get_turkey_now()
    if now.weekday() != 0:
        return

    previous_week_key = get_previous_week_key(now)
    if is_weekly_report_sent(posted_data, previous_week_key):
        return

    stats = get_weekly_stats(posted_data, previous_week_key)
    token_line = _token_line(get_token_remaining_days())
    error_lines = _dict_to_lines(stats.get("errors", {}))
    skip_lines = _dict_to_lines(stats.get("skips", {}))

    message = (
        "Haftalik Rapor\n\n"
        f"Hafta: {previous_week_key}\n"
        f"Toplam tetiklenme: {stats.get('actions', 0)}\n"
        f"Toplam otomatik paylasim: {stats.get('shares', 0)}\n"
        f"Toplam kalite nedeniyle pas gecme: {stats.get('skip_total', 0)}\n"
        f"Token kalan sure: {token_line}\n"
        f"Toplam hata: {stats.get('error_total', 0)}\n"
        f"Hata dagilimi:\n{error_lines}\n\n"
        f"Skip dagilimi:\n{skip_lines}"
    )

    sent = tg_platform.send_message(message)
    if sent:
        mark_weekly_report_sent(posted_data, previous_week_key)
        _save_posted_data_if_enabled(posted_data)
        log(f"Haftalik rapor gonderildi: {previous_week_key}")
    else:
        log(f"Haftalik rapor gonderilemedi: {previous_week_key}", "WARNING")


def _is_score_skipped() -> tuple[bool, str]:
    try:
        score_stage = get_stage("score")
        if score_stage.get("status") != "done":
            return False, ""
        output = score_stage.get("output") or {}
        if bool(output.get("skipped", False)):
            return True, (output.get("skip_reason", "") or "").strip()
    except Exception:
        pass
    return False, ""


def _is_publish_skipped() -> tuple[bool, str]:
    try:
        publish_stage = get_stage("publish")
        if publish_stage.get("status") != "done":
            return False, ""
        output = publish_stage.get("output") or {}
        if bool(output.get("skipped", False)):
            reason = (output.get("skip_reason", "") or "").strip() or "publish_skipped"
            return True, reason
    except Exception:
        pass
    return False, ""


def _is_soft_skip_error(error_text: str) -> tuple[bool, str]:
    text = (error_text or "").strip()
    low = text.lower()

    if "no article found" in low:
        return True, "no_article_found"
    if "no article above threshold" in low:
        return True, "no_article_above_threshold"
    if "invalid 10-scale" in low or "invalid_scale_10" in low:
        return True, "ai_invalid_scale_10"

    return False, ""


def _stage_output(stage_name: str) -> dict:
    try:
        stage = get_stage(stage_name)
        return stage.get("output", {}) or {}
    except Exception:
        return {}


def _format_breakdown(breakdown: dict) -> str:
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


def _format_top3(top_articles: list) -> str:
    if not isinstance(top_articles, list) or not top_articles:
        return "Yok"
    lines = []
    for idx, item in enumerate(top_articles[:3], start=1):
        lines.append(f"#{idx} {item.get('title', 'Bilinmiyor')[:90]}\nSkor: {item.get('score', 0)}")
    return "\n\n".join(lines)


def _send_no_share_report(reason: str, action_no: int = 0) -> None:
    fetch_output = _stage_output("fetch")
    score_output = _stage_output("score")
    metrics = fetch_output.get("metrics", {}) or {}
    top_articles = score_output.get("top_articles", [])
    top_score = score_output.get("top_score", 0) or (top_articles[0].get("score", 0) if top_articles else 0)
    share_count = get_today_post_count(get_posted_news())

    message = (
        "Paylaşım yapılmadı.\n\n"
        f"Sebep:\n{reason}\n\n"
        f"Günün {action_no or 'bilinmeyen'}. tetiklemesi\n"
        f"Günün {share_count}. paylaşımı\n"
        f"{_workflow_timing_line()}\n"
        f"Bulunan haber sayısı: {metrics.get('found', fetch_output.get('count', 0))}\n"
        f"Filtre sonrası kalan haber sayısı: {metrics.get('after_duplicate', fetch_output.get('count', 0))}\n"
        f"AI değerlendirmesine giren haber sayısı: {score_output.get('scored_count', 0)}\n"
        f"En yüksek skor: {top_score}\n"
        "Paylaşım durumu: Yapılmadı\n\n"
        f"İlk 3 haber:\n{_format_top3(top_articles)}"
    )
    if not tg_platform.send_message(message):
        log("Telegram no-share bildirimi gonderilemedi", "WARNING")


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


def _log_workflow_start_context() -> None:
    log(_workflow_timing_line())


def _run_telegram_priority_share() -> bool:
    """
    Telegram sohbetinden gelen oncelikli (gorsel + aciklama) icerigi
    varsa dogrudan publish asamasina tasir.
    Donus:
      - True  -> Telegram icerigi paylasildi ya da denendi (normal akis durur)
      - False -> Telegram icerigi yok, normal haber akisi devam eder
    """
    telegram_payload = tg_platform.consume_pending_shareable_content()
    if not telegram_payload:
        return False

    log("Telegram oncelikli icerik bulundu, anlik paylasim akisina geciliyor")

    run_id = get_turkey_now().strftime("%Y-%m-%d-%H:%M")
    if not init_pipeline(run_id):
        log("Pipeline init failed (telegram priority)", "ERROR")
        _record_error_stat("PIPELINE_INIT_FAILED", "Pipeline init failed (telegram priority)")
        return True

    set_stage("fetch", "done", output={"telegram_priority": True, "articles": []})
    set_stage(
        "score",
        "done",
        output={
            "telegram_priority": True,
            "selected_article": telegram_payload.get("article", {}),
            "score": 100,
            "skipped": False,
        },
    )
    set_stage(
        "write",
        "done",
        output={
            "telegram_priority": True,
            "article": telegram_payload.get("article", {}),
            "post_text": telegram_payload.get("post_text", ""),
            "post_text_length": len(telegram_payload.get("post_text", "")),
            "skipped": False,
        },
    )
    set_stage("image", "done", output=telegram_payload)
    _log_image_summary()

    from agents.agent_publisher import run as publisher_run

    if not _run_agent("agent_publisher", publisher_run):
        err = _log_stage_error("publish")
        _record_error_stat("PUBLISH_FAILED", err or "agent_publisher failed (telegram priority)")
        return True

    skipped, reason = _is_publish_skipped()
    if skipped:
        _record_skip_stat(reason)
        log(f"Telegram priority publish skip: {reason}", "INFO")

    return True


def main() -> None:
    try:
        settings = load_config("settings")
        posted_data = get_posted_news()

        action_no = increment_action_trigger(posted_data)
        _save_posted_data_if_enabled(posted_data)
        log(f"Cron başladı: Action tetiklendi A{action_no}")
        _log_workflow_start_context()

        _send_weekly_report_if_needed(posted_data)

        if _run_telegram_priority_share():
            return

        if not _check_daily_limit(settings, posted_data):
            reason = "daily_limit_reached"
            _record_skip_stat(reason)
            _send_no_share_report(reason, action_no)
            return

        if not _check_min_interval(settings, posted_data):
            reason = "min_post_interval_not_reached"
            _record_skip_stat(reason)
            _send_no_share_report(reason, action_no)
            return

        run_id = get_turkey_now().strftime("%Y-%m-%d-%H:%M")
        if not init_pipeline(run_id):
            log("Pipeline init failed", "ERROR")
            _record_error_stat("PIPELINE_INIT_FAILED", "Pipeline init failed")
            _send_no_share_report("Pipeline init failed", action_no)
            return

        from agents.agent_fetcher import run as fetcher_run

        if not _run_agent("agent_fetcher", fetcher_run):
            err = _log_stage_error("fetch")
            _log_source_health()

            is_skip, skip_reason = _is_soft_skip_error(err)
            if is_skip:
                _record_skip_stat(skip_reason)
            else:
                _record_error_stat("FETCH_FAILED", err or "agent_fetcher failed")
            _send_no_share_report(skip_reason or err or "agent_fetcher failed", action_no)
            return
        log("Haberler çekildi")
        _log_source_health()

        from agents.agent_scorer import run as scorer_run

        if not _run_agent("agent_scorer", scorer_run):
            err = _log_stage_error("score")

            is_skip, skip_reason = _is_soft_skip_error(err)
            if is_skip:
                _record_skip_stat(skip_reason)
            else:
                _record_error_stat("SCORER_FAILED", err or "agent_scorer failed")
            _send_no_share_report(skip_reason or err or "agent_scorer failed", action_no)
            return
        log("AI puanlama tamamlandı")

        skipped, skip_reason = _is_score_skipped()
        if skipped:
            reason = skip_reason or "No article above threshold"
            log(f"Score skip: {reason}", "INFO")
            _record_skip_stat(reason)
            _send_no_share_report(reason, action_no)
            return

        from agents.agent_writer import run as writer_run

        if not _run_agent("agent_writer", writer_run):
            err = _log_stage_error("write")
            _record_error_stat("WRITER_FAILED", err or "agent_writer failed")
            _send_no_share_report(err or "agent_writer failed", action_no)
            return

        from agents.agent_image import run as image_run

        if not _run_agent("agent_image", image_run):
            err = _log_stage_error("image")
            _record_error_stat("IMAGE_FAILED", err or "agent_image failed")
            _send_no_share_report(err or "agent_image failed", action_no)
            return
        log("Görseller puanlandı")
        _log_image_summary()

        from agents.agent_publisher import run as publisher_run

        if not _run_agent("agent_publisher", publisher_run):
            err = _log_stage_error("publish")

            is_skip, skip_reason = _is_soft_skip_error(err)
            if is_skip:
                _record_skip_stat(skip_reason)
            else:
                _record_error_stat("PUBLISH_FAILED", err or "agent_publisher failed")
            _send_no_share_report(skip_reason or err or "agent_publisher failed", action_no)
            return

        publish_skipped, publish_skip_reason = _is_publish_skipped()
        if publish_skipped:
            _record_skip_stat(publish_skip_reason)
            log(f"Publish skip: {publish_skip_reason}", "INFO")
            _send_no_share_report(publish_skip_reason, action_no)
            return

        log("Paylaşım yapıldı")

    except KeyboardInterrupt:
        log("Interrupted by user", "WARNING")
        _record_error_stat("INTERRUPTED", "Interrupted by user")
    except Exception as exc:
        log(f"Critical orchestrator error: {exc}", "ERROR")
        log(traceback.format_exc(), "ERROR")
        _record_error_stat("ORCHESTRATOR_CRITICAL", str(exc))
        try:
            _send_no_share_report(f"Exception oluştu: {exc}", locals().get("action_no", 0))
        except Exception:
            pass
    finally:
        _save_check_time()


if __name__ == "__main__":
    main()
