"""
Main orchestrator for otoXtra bot.
"""

import os
import sys
import random
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


def _is_test_mode() -> bool:
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return True
    return "--test" in sys.argv


def _check_daily_limit(settings: dict, posted_data: dict) -> bool:
    today_count = get_today_post_count(posted_data)
    max_daily = settings.get("posting", {}).get("max_daily_posts", 9)
    log(f"Today posts: {today_count}/{max_daily}")
    return today_count < max_daily


def _check_random_skip(settings: dict, test_mode: bool) -> bool:
    if test_mode:
        return True
    skip_probability = settings.get("posting", {}).get("skip_probability_percent", 10)
    if skip_probability <= 0:
        return True
    roll = random.randint(1, 100)
    return roll > skip_probability


def _check_min_interval(settings: dict, posted_data: dict, test_mode: bool) -> bool:
    if test_mode:
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


def _random_delay(settings: dict, test_mode: bool) -> None:
    if test_mode:
        return
    import time

    max_delay_minutes = settings.get("posting", {}).get("random_delay_max_minutes", 8)
    if max_delay_minutes <= 0:
        return
    time.sleep(random.randint(0, max_delay_minutes * 60))


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
            f"Source health: ok={ok_count}, error={err_count}, empty={empty_count}, disabled={disabled_count}"
        )
    except Exception:
        pass


def _save_check_time() -> None:
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
    test_mode = _is_test_mode()
    try:
        settings = load_config("settings")
        posted_data = get_posted_news()

        if not _check_daily_limit(settings, posted_data):
            return
        if not _check_random_skip(settings, test_mode):
            return
        if not _check_min_interval(settings, posted_data, test_mode):
            return

        _random_delay(settings, test_mode)

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
