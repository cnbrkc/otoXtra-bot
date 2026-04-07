"""
Viral scoring agent.
"""

import time
from datetime import timedelta
from typing import Optional

from core.logger import log
from core.config_loader import load_config
from core.helpers import (
    get_posted_news,
    get_today_post_count,
    is_similar_title,
    get_turkey_now,
)
from core.state_manager import get_stage, set_stage, init_pipeline
from core.ai_client import ask_ai, parse_ai_json


BATCH_SIZE: int = 20
BATCH_DELAY_SECONDS: int = 3
UNSCORED_DEFAULT: int = 0
CROSS_VALIDATE_THRESHOLD: float = 0.4

# Daha hassas tazelik bonusu
FRESHNESS_TIERS = [
    (1, 10),
    (3, 7),
    (6, 4),
    (12, 1),
]
FRESHNESS_OLD_MALUS: int = -4

# Trend bonusunda tavana vurup skor sisirmeyi engellemek icin
TREND_BONUS_CAP: int = 18


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


def _match_ai_results_to_articles(ai_results: list, articles: list) -> list:
    matched = []
    used_indices = set()

    for ai_result in ai_results:
        matched_article = None
        matched_index = None

        sira = ai_result.get("sira")
        if sira is not None:
            try:
                index = int(sira) - 1
                if 0 <= index < len(articles) and index not in used_indices:
                    ai_title = ai_result.get("baslik", "").strip()
                    article_title = articles[index].get("title", "").strip()
                    if not ai_title or is_similar_title(
                        ai_title, article_title, threshold=CROSS_VALIDATE_THRESHOLD
                    ):
                        matched_article = articles[index]
                        matched_index = index
            except (ValueError, TypeError):
                pass

        if matched_article is None:
            ai_title_str = ai_result.get("baslik", "").strip()
            if ai_title_str:
                ai_lower = ai_title_str.lower()
                for i, article in enumerate(articles):
                    if i in used_indices:
                        continue
                    if ai_lower == article.get("title", "").strip().lower():
                        matched_article = article
                        matched_index = i
                        break

        if matched_article is None:
            ai_title_fuzzy = ai_result.get("baslik", "").strip()
            if ai_title_fuzzy:
                for i, article in enumerate(articles):
                    if i in used_indices:
                        continue
                    if is_similar_title(
                        ai_title_fuzzy,
                        article.get("title", "").strip(),
                        threshold=0.6,
                    ):
                        matched_article = article
                        matched_index = i
                        break

        if matched_article is not None and matched_index is not None:
            matched.append((ai_result, matched_article))
            used_indices.add(matched_index)

    return matched


def run_viral_scoring(articles: list) -> list:
    if not articles:
        log("No articles for scoring", "INFO")
        return []

    prompts_config = load_config("prompts")
    scorer_prompt = prompts_config.get("viral_scorer", "")
    if not scorer_prompt:
        log("viral_scorer prompt not found", "WARNING")
        for article in articles:
            article["score"] = UNSCORED_DEFAULT
        return articles

    batches = _split_into_batches(articles)
    all_scored = []

    for batch_num, batch in enumerate(batches, start=1):
        numbered_text = _format_articles_numbered(batch)
        ai_response = ask_ai(f"{scorer_prompt}\n\n{numbered_text}")

        if not ai_response:
            for article in batch:
                article["score"] = UNSCORED_DEFAULT
                article["score_reason"] = "ai_empty"
                all_scored.append(article)
            if batch_num < len(batches):
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        ai_results = parse_ai_json(ai_response)
        if not ai_results or not isinstance(ai_results, list):
            for article in batch:
                article["score"] = UNSCORED_DEFAULT
                article["score_reason"] = "ai_parse_failed"
                all_scored.append(article)
            if batch_num < len(batches):
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        matched_pairs = _match_ai_results_to_articles(ai_results, batch)
        matched_ids = {id(art) for _, art in matched_pairs}

        for ai_result, article in matched_pairs:
            puan = _clamp_score(_safe_int(ai_result.get("puan", UNSCORED_DEFAULT), UNSCORED_DEFAULT))
            article["score"] = puan
            article["score_reason"] = "ai_scored"
            all_scored.append(article)

        for article in batch:
            if id(article) not in matched_ids:
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

        now = get_turkey_now()
        age_hours = (now - pub_dt).total_seconds() / 3600

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

        if bonus == 0:
            continue

        original = _safe_int(article.get("score", 0), 0)
        article["score"] = _clamp_score(original + bonus)

    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored_articles


def _trend_count_bonus(trend_count: int) -> int:
    # trend_count artisinda azalan getiri
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
    # Dusuk AI skorda trend bonusunu tum gucuyle uygulamayip sisirmeyi azaltir
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

        # fetch katmanindan gelen trend_bonus'u koru, ama tek kaynak yerine count + priority degerini de hesaba kat
        computed_count_bonus = _trend_count_bonus(trend_count)
        priority_bonus = _priority_bonus(src_priority)

        raw_trend_bonus = max(incoming_trend_bonus, computed_count_bonus) + priority_bonus
        raw_trend_bonus = min(raw_trend_bonus, TREND_BONUS_CAP)

        effective_bonus = int(round(raw_trend_bonus * _confidence_multiplier(ai_score)))

        # Kalitesiz icerikte bonusu biraz daha kis
        summary = (article.get("summary", "") or "").strip()
        if len(summary) < 25:
            effective_bonus = max(0, effective_bonus - 2)

        article["trend_count"] = trend_count
        article["trend_bonus_raw"] = raw_trend_bonus
        article["trend_bonus"] = effective_bonus

        if effective_bonus <= 0:
            continue

        article["score"] = _clamp_score(ai_score + effective_bonus)

    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored_articles


def apply_thresholds(scored_articles: list) -> list:
    if not scored_articles:
        return []

    scoring_config = load_config("scoring")
    thresholds = scoring_config.get("thresholds", {}) if isinstance(scoring_config, dict) else {}
    publish_score = _safe_int(thresholds.get("publish_score", 65), 65)
    slow_day_score = _safe_int(thresholds.get("slow_day_score", 50), 50)

    today_post_count = get_today_post_count(get_posted_news())
    threshold = slow_day_score if today_post_count < 2 else publish_score

    return [a for a in scored_articles if a.get("score", 0) >= threshold]


def filter_and_score(articles: list) -> Optional[dict]:
    if not articles:
        return None

    scored = run_viral_scoring(articles)
    if not scored:
        return None

    scored = apply_freshness_bonus(scored)
    scored = apply_trend_bonus(scored)
    above_threshold = apply_thresholds(scored)

    if not above_threshold:
        return None

    best = above_threshold[0]
    return best


def run() -> bool:
    fetch_stage = get_stage("fetch")
    if fetch_stage.get("status") != "done":
        set_stage("score", "error", error="fetch stage not done")
        return False

    fetch_output = fetch_stage.get("output", {})
    articles = fetch_output.get("articles", [])
    if not articles:
        set_stage("score", "error", error="No articles in fetch output")
        return False

    set_stage("score", "running")

    try:
        best_article = filter_and_score(articles)
        if best_article is None:
            set_stage("score", "error", error="No article above threshold")
            return False

        output = {
            "selected_article": best_article,
            "score": best_article.get("score", 0),
            "title": best_article.get("title", ""),
            "trend_count": best_article.get("trend_count", 1),
            "trend_bonus": best_article.get("trend_bonus", 0),
            "freshness_bonus": best_article.get("freshness_bonus", 0),
            "score_reason": best_article.get("score_reason", "unknown"),
        }
        set_stage("score", "done", output=output)
        return True

    except Exception as exc:
        set_stage("score", "error", error=str(exc))
        return False


if __name__ == "__main__":
    init_pipeline("test-scorer")
    run()
