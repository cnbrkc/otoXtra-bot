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
)
from core.state_manager import init_pipeline, get_stage


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


def _log_stage_error(stage_name: str) -> None:
    try:
        stage = get_stage(stage_name)
        error_msg = stage.get("error", "")
        if error_msg:
            log(f"{stage_name} error: {error_msg}", "WARNING")
    except Exception:
        pass


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


def main() -> None:
    try:
        settings = load_config("settings")
        posted_data = get_posted_news()

        if not _check_daily_limit(settings, posted_data):
            return
        if not _check_min_interval(settings, posted_data):
            return

        run_id = get_turkey_now().strftime("%Y-%m-%d-%H:%M")
        if not init_pipeline(run_id):
            log("Pipeline init failed", "ERROR")
            return

        from agents.agent_fetcher import run as fetcher_run
        if not _run_agent("agent_fetcher", fetcher_run):
            _log_stage_error("fetch")
            _log_source_health()
            return
        _log_source_health()

        from agents.agent_scorer import run as scorer_run
        if not _run_agent("agent_scorer", scorer_run):
            _log_stage_error("score")
            return

        from agents.agent_writer import run as writer_run
        if not _run_agent("agent_writer", writer_run):
            _log_stage_error("write")
            return

        from agents.agent_image import run as image_run
        if not _run_agent("agent_image", image_run):
            _log_stage_error("image")
            return
        _log_image_summary()

        from agents.agent_publisher import run as publisher_run
        if not _run_agent("agent_publisher", publisher_run):
            _log_stage_error("publish")
            return

    except KeyboardInterrupt:
        log("Interrupted by user", "WARNING")
    except Exception as exc:
        log(f"Critical orchestrator error: {exc}", "ERROR")
        log(traceback.format_exc(), "ERROR")
    finally:
        _save_check_time()


if __name__ == "__main__":
    main()
