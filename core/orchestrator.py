"""
Main orchestrator for otoXtra bot.
"""

import os
import traceback

from core.logger import log
from core.config_loader import load_config
from core.helpers import (
    get_posted_news,
    save_posted_news,
    get_today_post_count,
    get_turkey_now,
    save_last_check_time,
    increment_action_trigger,
    get_previous_week_key,
    get_weekly_stats,
    is_weekly_report_sent,
    mark_weekly_report_sent,
    get_token_remaining_days,
    record_weekly_error,
)
from core.state_manager import init_pipeline, get_stage
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


def _is_persist_state_enabled() -> bool:
    return _get_env_bool("PERSIST_STATE", True)


def _ignore_min_post_interval() -> bool:
    return _get_env_bool("IGNORE_MIN_POST_INTERVAL", False)


def _check_daily_limit(settings: dict, posted_data: dict) -> bool:
    today_count = get_today_post_count(posted_data)
    max_daily = settings.get("posting", {}).get("max_daily_posts", 9)
    log(f"Today posts: {today_count}/{max_daily}")
    return today_count < max_daily


def _check_min_interval(settings: dict, posted_data: dict) -> bool:
    if _ignore_min_post_interval():
        log("Min post interval kontrolu atlandi (IGNORE_MIN_POST_INTERVAL=true)")
        return True

    min_interval_hours = settings.get("posting", {}).get("min_post_interval_hours", 0)
    if min_interval_hours <= 0:
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
        hours_since = (get_turkey_now() - last_posted_at).total_seconds() / 3600
        return hours_since >= min_interval_hours
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


def _save_posted_data_if_enabled(data: dict) -> None:
    if not _is_persist_state_enabled():
        return
    save_posted_news(data)


def _record_error_stat(error_code: str, error_name: str) -> None:
    if not _is_persist_state_enabled():
        return
    try:
        data = get_posted_news()
        record_weekly_error(data, error_code, error_name)
        save_posted_news(data)
    except Exception as exc:
        log(f"Error stat kaydedilemedi: {exc}", "WARNING")


def _send_weekly_report_if_needed(posted_data: dict) -> None:
    now = get_turkey_now()

    # Sadece pazartesi calisir
    if now.weekday() != 0:
        return

    previous_week_key = get_previous_week_key(now)
    if is_weekly_report_sent(posted_data, previous_week_key):
        return

    stats = get_weekly_stats(posted_data, previous_week_key)
    token_days = get_token_remaining_days()

    if token_days is None:
        token_line = "Bilinmiyor"
    elif token_days < 0:
        token_line = f"Token suresi dolmus ({abs(token_days)} gun gecmis)"
    else:
        token_line = f"{token_days} gun"

    errors = stats.get("errors", {})
    if errors:
        error_lines = "\n".join([f"{name}: {count}" for name, count in errors.items()])
    else:
        error_lines = "Yok"

    message = (
        "Haftalik Rapor\n\n"
        f"Hafta: {previous_week_key}\n"
        f"Toplam tetiklenme: {stats.get('actions', 0)}\n"
        f"Toplam otomatik paylasim: {stats.get('shares', 0)}\n"
        f"Token kalan sure: {token_line}\n"
        f"Toplam hata: {stats.get('error_total', 0)}\n"
        f"Hata dagilimi:\n{error_lines}"
    )

    sent = tg_platform.send_message(message)
    if sent:
        mark_weekly_report_sent(posted_data, previous_week_key)
        _save_posted_data_if_enabled(posted_data)
        log(f"Haftalik rapor gonderildi: {previous_week_key}")
    else:
        log(f"Haftalik rapor gonderilemedi: {previous_week_key}", "WARNING")


def main() -> None:
    try:
        settings = load_config("settings")
        posted_data = get_posted_news()

        # Her orchestrator calismasi bir "action/tetiklenme" olarak sayilir.
        action_no = increment_action_trigger(posted_data)
        _save_posted_data_if_enabled(posted_data)
        log(f"Action tetiklendi: A{action_no}")

        # Pazartesi ilk calismada (onceki hafta icin) haftalik rapor.
        _send_weekly_report_if_needed(posted_data)

        if not _check_daily_limit(settings, posted_data):
            return
        if not _check_min_interval(settings, posted_data):
            return

        run_id = get_turkey_now().strftime("%Y-%m-%d-%H:%M")
        if not init_pipeline(run_id):
            log("Pipeline init failed", "ERROR")
            _record_error_stat("PIPELINE_INIT_FAILED", "Pipeline init failed")
            return

        from agents.agent_fetcher import run as fetcher_run
        if not _run_agent("agent_fetcher", fetcher_run):
            err = _log_stage_error("fetch")
            _log_source_health()
            _record_error_stat("FETCH_FAILED", err or "agent_fetcher failed")
            return
        _log_source_health()

        from agents.agent_scorer import run as scorer_run
        if not _run_agent("agent_scorer", scorer_run):
            err = _log_stage_error("score")
            _record_error_stat("SCORER_FAILED", err or "agent_scorer failed")
            return

        from agents.agent_writer import run as writer_run
        if not _run_agent("agent_writer", writer_run):
            err = _log_stage_error("write")
            _record_error_stat("WRITER_FAILED", err or "agent_writer failed")
            return

        from agents.agent_image import run as image_run
        if not _run_agent("agent_image", image_run):
            err = _log_stage_error("image")
            _record_error_stat("IMAGE_FAILED", err or "agent_image failed")
            return
        _log_image_summary()

        from agents.agent_publisher import run as publisher_run
        if not _run_agent("agent_publisher", publisher_run):
            err = _log_stage_error("publish")
            _record_error_stat("PUBLISH_FAILED", err or "agent_publisher failed")
            return

    except KeyboardInterrupt:
        log("Interrupted by user", "WARNING")
        _record_error_stat("INTERRUPTED", "Interrupted by user")
    except Exception as exc:
        log(f"Critical orchestrator error: {exc}", "ERROR")
        log(traceback.format_exc(), "ERROR")
        _record_error_stat("ORCHESTRATOR_CRITICAL", str(exc))
    finally:
        _save_check_time()


if __name__ == "__main__":
    main()
