"""
Viral scoring agent.
"""

import os
import time
from datetime import timedelta
from typing import Optional, Tuple

from core.ai_client import ask_ai, parse_ai_json
from core.config_loader import load_config
from core.helpers import (
    get_posted_news,
    get_today_post_count,
    get_turkey_now,
    is_similar_title,
)
from core.logger import log
from core.state_manager import get_stage, set_stage


BATCH_SIZE: int = 20
BATCH_DELAY_SECONDS: int = 3
UNSCORED_DEFAULT: int = 0
CROSS_VALIDATE_THRESHOLD: float = 0.4

FRESHNESS_TIERS = [
    (1, 10),
    (3, 7),
    (6, 4),
    (12, 1),
]
FRESHNESS_OLD_MALUS: int = -4
TREND_BONUS_CAP: int = 18


def _is_score_breakdown_enabled() -> bool:
    value = os.environ.get("DEBUG_SCORE_BREAKDOWN", "false").strip().lower()
    return value in ("1", "true", "yes", "on")


def _allow_skip_as_success() -> bool:
    value = os.environ.get("SCORE_SKIP_AS_SUCCESS", "false").strip().lower()
    return value in ("1", "true", "yes", "on")


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _clamp_score(value: int) -> int:
    return max(0, min(100, value))


def _format_articles_numbered(articles: list) -> str:
    lines = []
    for i, article in enumerate(articles, start=1):
        title = article.get("title", "No title").strip()
        summary = article.get("summary", "No summary").strip()
        if len(summary) > 300:
            summary = summary[:297] + "..."
        lines.append(f"{i}. Baslik: {title} | Ozet: {summary}")
    return "\n".join(lines)


def _split_into_batches(articles: list) -> list:
    return [articles[i : i + BATCH_SIZE] for i in range(0, len(articles), BATCH_SIZE)]


def _mark_unscored_batch(batch: list, reason: str, all_scored: list) -> None:
    for article in batch:
        article["score"] = UNSCORED_DEFAULT
        article["score_reason"] = reason
        all_scored.append(article)


def _normalize_ai_results(parsed: object) -> Optional[list]:
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    return None


def _match_by_order(ai_result: dict, articles: list, used_indices: set) -> tuple[Optional[dict], Optional[int]]:
    sira = ai_result.get("sira")
    if sira is None:
        return None, None

    try:
        index = int(sira) - 1
    except (ValueError, TypeError):
        return None, None

    if not (0 <= index < len(articles)) or index in used_indices:
        return None, None

    ai_title = ai_result.get("baslik", "").strip()
    article_title = articles[index].get("title", "").strip()
    if not ai_title or is_similar_title(ai_title, article_title, threshold=CROSS_VALIDATE_THRESHOLD):
        return articles[index], index

    return None, None


def _match_by_exact_title(ai_result: dict, articles: list, used_indices: set) -> tuple[Optional[dict], Optional[int]]:
    ai_title = ai_result.get("baslik", "").strip()
    if not ai_title:
        return None, None

    ai_lower = ai_title.lower()
    for i, article in enumerate(articles):
        if i in used_indices:
            continue
        if ai_lower == article.get("title", "").strip().lower():
            return article, i
    return None, None


def _match_by_fuzzy_title(ai_result: dict, articles: list, used_indices: set) -> tuple[Optional[dict], Optional[int]]:
    ai_title = ai_result.get("baslik", "").strip()
    if not ai_title:
        return None, None

    for i, article in enumerate(articles):
        if i in used_indices:
            continue
        if is_similar_title(ai_title, article.get("title", "").strip(), threshold=0.6):
            return article, i
    return None, None


def _match_ai_results_to_articles(ai_results: list, articles: list) -> list:
    matched = []
    used_indices = set()

    for ai_result in ai_results:
        if not isinstance(ai_result, dict):
            continue

        matched_article, matched_index = _match_by_order(ai_result, articles, used_indices)
        if matched_article is None:
            matched_article, matched_index = _match_by_exact_title(ai_result, articles, used_indices)
        if matched_article is None:
            matched_article, matched_index = _match_by_fuzzy_title(ai_result, articles, used_indices)

        if matched_article is not None and matched_index is not None:
            matched.append((ai_result, matched_article))
            used_indices.add(matched_index)

    return matched


def run_viral_scoring(articles: list) -> list:
    if not articles:
        log("No articles for scoring", "INFO")
        return []

    scorer_prompt = load_config("prompts").get("viral_scorer", "")
    if not scorer_prompt:
        log("viral_scorer prompt not found", "WARNING")
        _mark_unscored_batch(articles, "prompt_missing", articles)
        return articles

    batches = _split_into_batches(articles)
    all_scored = []

    for batch_num, batch in enumerate(batches, start=1):
        ai_response = ask_ai(f"{scorer_prompt}\n\n{_format_articles_numbered(batch)}")
        if not ai_response:
            _mark_unscored_batch(batch, "ai_empty", all_scored)
            if batch_num < len(batches):
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        ai_results = _normalize_ai_results(parse_ai_json(ai_response))
        if not ai_results:
            _mark_unscored_batch(batch, "ai_parse_failed", all_scored)
            if batch_num < len(batches):
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        matched_pairs = _match_ai_results_to_articles(ai_results, batch)
        matched_ids = {id(art) for _, art in matched_pairs}

        for ai_result, article in matched_pairs:
            base_score = _clamp_score(_safe_int(ai_result.get("puan", UNSCORED_DEFAULT), UNSCORED_DEFAULT))
            article["base_ai_score"] = base_score
            article["score"] = base_score
            article["score_reason"] = "ai_scored"
            all_scored.append(article)

        for article in batch:
            if id(article) in matched_ids:
                continue
            article["base_ai_score"] = UNSCORED_DEFAULT
            article["score"] = UNSCORED_DEFAULT
            article["score_reason"] = "ai_unmatched"
            all_scored.append(article)

        if batch_num < len(batches):
            time.sleep(BATCH_DELAY_SECONDS)

    all_scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    return all_scored


def _calculate_freshness_bonus(published_str: str) -> int:
    if not published_str:
        return 0

    try:
        from datetime import datetime, timezone

        try:
            from dateutil import parser as dateutil_parser

            pub_dt = dateutil_parser.parse(published_str)
        except ImportError:
            cleaned = published_str.strip()
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            pub_dt = datetime.fromisoformat(cleaned)

        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone(timedelta(hours=3)))

        age_hours = (get_turkey_now() - pub_dt).total_seconds() / 3600
        if age_hours < 0:
            return 0

        for max_hours, bonus in FRESHNESS_TIERS:
            if age_hours < max_hours:
                return bonus

        return FRESHNESS_OLD_MALUS
    except Exception:
        return 0


def apply_freshness_bonus(scored_articles: list) -> list:
    if not scored_articles:
        return []

    for article in scored_articles:
        bonus = _calculate_freshness_bonus(article.get("published", ""))
        article["freshness_bonus"] = bonus
        article["score"] = _clamp_score(_safe_int(article.get("score", 0), 0) + bonus)

    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored_articles


def _trend_count_bonus(trend_count: int) -> int:
    if trend_count >= 6:
        return 12
    if trend_count >= 5:
        return 10
    if trend_count >= 4:
        return 8
    if trend_count >= 3:
        return 6
    if trend_count >= 2:
        return 3
    return 0


def _priority_bonus(source_priority: str) -> int:
    value = (source_priority or "").lower().strip()
    if value == "high":
        return 2
    if value == "medium":
        return 1
    return 0


def _confidence_multiplier(ai_score: int) -> float:
    if ai_score < 40:
        return 0.4
    if ai_score < 55:
        return 0.7
    return 1.0


def apply_trend_bonus(scored_articles: list) -> list:
    if not scored_articles:
        return []

    for article in scored_articles:
        ai_score = _safe_int(article.get("score", 0), 0)
        trend_count = _safe_int(article.get("trend_count", 1), 1)
        incoming_trend_bonus = _safe_int(article.get("trend_bonus", 0), 0)
        src_priority = article.get("source_priority", "low")

        computed_count_bonus = _trend_count_bonus(trend_count)
        raw_trend_bonus = max(incoming_trend_bonus, computed_count_bonus) + _priority_bonus(src_priority)
        raw_trend_bonus = min(raw_trend_bonus, TREND_BONUS_CAP)

        effective_bonus = int(round(raw_trend_bonus * _confidence_multiplier(ai_score)))
        summary = (article.get("summary", "") or "").strip()
        if len(summary) < 25:
            effective_bonus = max(0, effective_bonus - 2)

        article["trend_count"] = trend_count
        article["trend_bonus_raw"] = raw_trend_bonus
        article["trend_bonus"] = effective_bonus
        article["score"] = _clamp_score(ai_score + effective_bonus)

    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored_articles


def _get_active_threshold() -> int:
    scoring_config = load_config("scoring")
    thresholds = scoring_config.get("thresholds", {}) if isinstance(scoring_config, dict) else {}

    publish_score = _safe_int(thresholds.get("publish_score", 65), 65)
    slow_day_score = _safe_int(thresholds.get("slow_day_score", 50), 50)
    today_post_count = get_today_post_count(get_posted_news())

    return slow_day_score if today_post_count < 2 else publish_score


def apply_thresholds(scored_articles: list) -> list:
    if not scored_articles:
        return []

    threshold = _get_active_threshold()
    return [a for a in scored_articles if a.get("score", 0) >= threshold]


def _log_score_breakdown(scored_articles: list, threshold: int) -> None:
    if not _is_score_breakdown_enabled():
        return
    if not scored_articles:
        log("Score breakdown: no articles", "INFO")
        return

    log("=== SCORE BREAKDOWN (TOP 5) ===", "INFO")
    for idx, article in enumerate(scored_articles[:5], start=1):
        title = (article.get("title", "") or "")[:90]
        base_ai = _safe_int(article.get("base_ai_score", article.get("score", 0)), 0)
        freshness = _safe_int(article.get("freshness_bonus", 0), 0)
        trend_raw = _safe_int(article.get("trend_bonus_raw", 0), 0)
        trend_eff = _safe_int(article.get("trend_bonus", 0), 0)
        final_score = _safe_int(article.get("score", 0), 0)
        trend_count = _safe_int(article.get("trend_count", 1), 1)

        log(
            f"{idx}) score={final_score} (base={base_ai} + fresh={freshness} + trend={trend_eff}/{trend_raw}) "
            f"trend_count={trend_count} threshold={threshold} | {title}",
            "INFO",
        )


def filter_and_score(articles: list) -> Tuple[Optional[dict], dict]:
    if not articles:
        return None, {"skipped": True, "skip_reason": "no_articles"}

    scored = run_viral_scoring(articles)
    if not scored:
        return None, {"skipped": True, "skip_reason": "no_scored_articles"}

    scored = apply_freshness_bonus(scored)
    scored = apply_trend_bonus(scored)

    threshold = _get_active_threshold()
    _log_score_breakdown(scored, threshold)

    above_threshold = [a for a in scored if a.get("score", 0) >= threshold]
    if not above_threshold:
        top = scored[0] if scored else {}
        return None, {
            "skipped": True,
            "skip_reason": "No article above threshold",
            "threshold": threshold,
            "top_score": _safe_int(top.get("score", 0), 0),
            "top_title": top.get("title", ""),
        }

    return above_threshold[0], {"skipped": False, "threshold": threshold}


def _build_skip_output(meta: dict) -> dict:
    threshold = _safe_int(meta.get("threshold", _get_active_threshold()), _get_active_threshold())
    return {
        "selected_article": None,
        "score": 0,
        "title": "",
        "trend_count": 0,
        "trend_bonus": 0,
        "freshness_bonus": 0,
        "score_reason": "skipped",
        "skipped": True,
        "skip_reason": meta.get("skip_reason", "No article above threshold"),
        "threshold": threshold,
        "top_score": _safe_int(meta.get("top_score", 0), 0),
        "top_title": meta.get("top_title", ""),
    }


def _build_success_output(best_article: dict, meta: dict) -> dict:
    return {
        "selected_article": best_article,
        "score": best_article.get("score", 0),
        "title": best_article.get("title", ""),
        "trend_count": best_article.get("trend_count", 1),
        "trend_bonus": best_article.get("trend_bonus", 0),
        "freshness_bonus": best_article.get("freshness_bonus", 0),
        "score_reason": best_article.get("score_reason", "unknown"),
        "skipped": False,
        "threshold": _safe_int(meta.get("threshold", 0), 0),
    }


def run() -> bool:
    fetch_stage = get_stage("fetch")
    if fetch_stage.get("status") != "done":
        set_stage("score", "error", error="fetch stage not done")
        return False

    articles = (fetch_stage.get("output", {}) or {}).get("articles", [])
    if not articles:
        set_stage("score", "error", error="No articles in fetch output")
        return False

    set_stage("score", "running")
    try:
        best_article, meta = filter_and_score(articles)

        if best_article is None:
            skip_output = _build_skip_output(meta)
            if _allow_skip_as_success():
                set_stage("score", "done", output=skip_output)
                log(
                    "score skipped: "
                    f"{skip_output['skip_reason']} "
                    f"(threshold={skip_output['threshold']}, top_score={skip_output['top_score']})",
                    "INFO",
                )
                return True

            set_stage(
                "score",
                "error",
                error=(
                    f"{skip_output['skip_reason']} "
                    f"(threshold={skip_output['threshold']}, top_score={skip_output['top_score']})"
                ),
            )
            return False

        set_stage("score", "done", output=_build_success_output(best_article, meta))
        return True

    except Exception as exc:
        set_stage("score", "error", error=str(exc))
        return False


if __name__ == "__main__":
    from core.state_manager import init_pipeline

    init_pipeline("test-scorer")
    run()
