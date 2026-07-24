"""
agents/scorer_engine.py - Puanlama Motoru ve Bonus Hesaplamaları (v5.3)
AI ile iletişim, batch puanlama, tazelik/trend bonusları ve eşik filtreleme burada.
"""
import time
from datetime import timedelta
from typing import Optional, Tuple

from core.ai_client import ask_ai, parse_ai_json
from core.config_loader import load_config
from core.helpers import get_posted_news, get_today_post_count, get_turkey_now
from core.logger import log
from agents.scorer_helpers import (
    BATCH_DELAY_SECONDS, BATCH_SIZE, FALLBACK_SCORE_HIGH, FALLBACK_SCORE_LOW, 
    FALLBACK_SCORE_MEDIUM, FRESHNESS_OLD_MALUS, FRESHNESS_TIERS, TREND_BONUS_CAP,
    UNSCORED_DEFAULT, _JSON_REPAIR_APPEND, _SCORER_MAX_TOKENS, _STRICT_SCALE_APPEND,
    _allow_skip_as_success, _apply_component_delta, _breakdown_total, _clamp_score,
    _extract_raw_ai_score, _extract_score_breakdown, _format_articles_numbered,
    _is_probably_ten_scale, _is_score_breakdown_enabled, _mark_unscored_batch,
    _match_ai_results_to_articles, _normalize_ai_results, _normalize_ai_score,
    _safe_int, _split_into_batches
)

# ═══════════════════════════════════════════════════════════════════════════════
# BATCH PUANLAMA & AI İLETİŞİMİ
# ═══════════════════════════════════════════════════════════════════════════════

def _score_batch_once(batch: list, prompt: str, batch_num: int) -> tuple[Optional[list], str]:
    log(f"[SCORER] Batch {batch_num}: Calling AI (articles={len(batch)}, max_tokens={_SCORER_MAX_TOKENS})", "INFO")

    ai_response = ask_ai(
        prompt=f"{prompt}\n\n{_format_articles_numbered(batch)}", 
        stage="scoring", 
        max_tokens=_SCORER_MAX_TOKENS
    )

    if not ai_response:
        log(f"[SCORER] Batch {batch_num}: AI returned empty response", "ERROR")
        return None, "ai_empty"

    log(f"[SCORER] Batch {batch_num}: AI response preview: {ai_response[:300]}", "INFO")

    parsed = parse_ai_json(ai_response)
    ai_results = _normalize_ai_results(parsed)

    if not ai_results:
        log(f"[SCORER] Batch {batch_num}: JSON parse failed. Raw: {ai_response[:500]}", "ERROR")
        return None, "ai_parse_failed"

    log(f"[SCORER] Batch {batch_num}: Parsed {len(ai_results)} AI results (batch has {len(batch)} articles)", "INFO")

    if ai_results and len(ai_results) > 0:
        sample = ai_results[0]
        log(f"[SCORER] Batch {batch_num}: Sample keys: {list(sample.keys())}", "INFO")

    if len(ai_results) < len(batch):
        log(
            f"[SCORER] Batch {batch_num}: AI returned {len(ai_results)}/{len(batch)} results "
            f"(partial response - token limit?)",
            "WARNING",
        )

    matched_pairs = _match_ai_results_to_articles(ai_results, batch)

    if not matched_pairs and ai_results:
        log(
            f"[SCORER] Batch {batch_num}: AI matching failed. "
            f"AI results={len(ai_results)}, Articles={len(batch)}, Matched=0",
            "WARNING",
        )

    log(f"[SCORER] Batch {batch_num}: Matched {len(matched_pairs)}/{len(batch)} articles", "INFO")
    return matched_pairs, ""


def _score_batch(batch: list, prompt: str, batch_num: int) -> tuple[Optional[list], str]:
    matched_pairs, fail_reason = _score_batch_once(batch, prompt, batch_num)

    if not fail_reason and matched_pairs is not None:
        if matched_pairs:
            return matched_pairs, ""

        log(f"[SCORER] Batch {batch_num}: Attempting repair for unmatched results", "WARNING")
        retry_pairs, retry_reason = _score_batch_once(batch, prompt + _JSON_REPAIR_APPEND, batch_num)

        if not retry_reason and retry_pairs:
            log(f"[SCORER] Batch {batch_num}: Repair successful", "INFO")
            return retry_pairs, ""

        return matched_pairs, ""

    log(f"[SCORER] Batch {batch_num}: Attempting repair for {fail_reason}", "WARNING")
    repaired_pairs, repaired_reason = _score_batch_once(batch, prompt + _JSON_REPAIR_APPEND, batch_num)

    if not repaired_reason and repaired_pairs is not None:
        log(f"[SCORER] Batch {batch_num}: Repair successful (original error: {fail_reason})", "INFO")
        return repaired_pairs, ""

    log(f"[SCORER] Batch {batch_num}: All retry attempts failed. Final error: {repaired_reason or fail_reason}", "ERROR")
    return None, repaired_reason or fail_reason


def run_viral_scoring(articles: list) -> list:
    if not articles:
        log("[SCORER] No articles for scoring", "INFO")
        return []

    scorer_prompt = load_config("prompts").get("viral_scorer", "")
    if not scorer_prompt:
        log("[SCORER] viral_scorer prompt not found", "ERROR")
        for article in articles:
            article["base_ai_score"] = UNSCORED_DEFAULT
            article["score"] = UNSCORED_DEFAULT
            article["score_reason"] = "prompt_missing"
        return articles

    batches = _split_into_batches(articles)
    all_scored = []

    log(f"[SCORER] Starting viral scoring: {len(articles)} articles in {len(batches)} batches (batch_size={BATCH_SIZE})", "INFO")

    for batch_num, batch in enumerate(batches, start=1):
        log(f"[SCORER] Processing batch {batch_num}/{len(batches)} ({len(batch)} articles)", "INFO")

        matched_pairs, fail_reason = _score_batch(batch, scorer_prompt, batch_num)

        if fail_reason:
            log(f"[SCORER] Batch {batch_num} FAILED: {fail_reason}", "ERROR")
            _mark_unscored_batch(batch, fail_reason, all_scored)
            if batch_num < len(batches):
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        raw_scores = [_extract_raw_ai_score(ai_result) for ai_result, _ in matched_pairs]

        if _is_probably_ten_scale(raw_scores):
            log(f"[SCORER] Batch {batch_num}: 10-scale detected, retrying with strict prompt", "WARNING")
            matched_pairs_retry, retry_reason = _score_batch(
                batch, scorer_prompt + _STRICT_SCALE_APPEND, batch_num
            )

            if retry_reason:
                log(f"[SCORER] Batch {batch_num}: Strict retry failed: {retry_reason}", "ERROR")
                _mark_unscored_batch(batch, retry_reason, all_scored)
                if batch_num < len(batches):
                    time.sleep(BATCH_DELAY_SECONDS)
                continue

            retry_raw_scores = [_extract_raw_ai_score(ai_result) for ai_result, _ in matched_pairs_retry]
            if _is_probably_ten_scale(retry_raw_scores):
                log(f"[SCORER] Batch {batch_num}: Still 10-scale after strict retry", "ERROR")
                _mark_unscored_batch(batch, "ai_invalid_scale_10", all_scored)
                if batch_num < len(batches):
                    time.sleep(BATCH_DELAY_SECONDS)
                continue

            matched_pairs = matched_pairs_retry

        matched_ids = {id(art) for _, art in matched_pairs}

        for ai_result, article in matched_pairs:
            base_score = _normalize_ai_score(ai_result)
            breakdown = _extract_score_breakdown(ai_result, base_score)
            article["base_ai_score"] = base_score
            article["score_breakdown"] = breakdown
            article["score"] = _breakdown_total(breakdown)
            article["score_reason"] = "ai_scored"
            article["score_explanation"] = (ai_result.get("gerekce", "") or "").strip()
            all_scored.append(article)

        for article in batch:
            if id(article) in matched_ids:
                continue
            summary = (article.get("summary", "") or "").strip()
            if len(summary) > 100:
                fallback_base = FALLBACK_SCORE_HIGH
                fallback_breakdown = {
                    "guncellik": 7, "etkilesim_potansiyeli": 8, "benzersizlik": 7,
                    "gundem_gucu": 8, "paylasilabilirlik": 5,
                }
            elif len(summary) > 50:
                fallback_base = FALLBACK_SCORE_MEDIUM
                fallback_breakdown = {
                    "guncellik": 6, "etkilesim_potansiyeli": 7, "benzersizlik": 6,
                    "gundem_gucu": 6, "paylasilabilirlik": 5,
                }
            else:
                fallback_base = FALLBACK_SCORE_LOW
                fallback_breakdown = {
                    "guncellik": 5, "etkilesim_potansiyeli": 5, "benzersizlik": 5,
                    "gundem_gucu": 5, "paylasilabilirlik": 5,
                }
            article["base_ai_score"] = fallback_base
            article["score"] = fallback_base
            article["score_breakdown"] = fallback_breakdown
            article["score_reason"] = "ai_unmatched_fallback"
            all_scored.append(article)
            log(f"[SCORER] Batch {batch_num}: Article unmatched, fallback score={fallback_base}", "WARNING")

        if batch_num < len(batches):
            time.sleep(BATCH_DELAY_SECONDS)

    all_scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    log(f"[SCORER] Scoring complete: {len(all_scored)} articles scored", "INFO")
    return all_scored


# ═══════════════════════════════════════════════════════════════════════════════
# BONUS & EŞİK HESAPLAMALARI
# ═══════════════════════════════════════════════════════════════════════════════

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
        _apply_component_delta(article, "guncellik", bonus)
        if not isinstance(article.get("score_breakdown"), dict):
            article["score"] = _clamp_score(_safe_int(article.get("score", 0), 0) + bonus)
    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored_articles

def _trend_count_bonus(trend_count: int) -> int:
    if trend_count >= 6: return 12
    if trend_count >= 5: return 10
    if trend_count >= 4: return 8
    if trend_count >= 3: return 6
    if trend_count >= 2: return 3
    return 0

def _priority_bonus(source_priority: str) -> int:
    value = (source_priority or "").lower().strip()
    if value == "high": return 2
    if value == "medium": return 1
    return 0

def _confidence_multiplier(ai_score: int) -> float:
    if ai_score < 20: return 0.6
    if ai_score < 55: return 0.85
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
        _apply_component_delta(article, "gundem_gucu", effective_bonus)
        if not isinstance(article.get("score_breakdown"), dict):
            article["score"] = _clamp_score(ai_score + effective_bonus)

    scored_articles.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored_articles

def _get_active_threshold() -> int:
    scoring_config = load_config("scoring")
    thresholds = scoring_config.get("thresholds", {}) if isinstance(scoring_config, dict) else {}
    publish_score = _safe_int(thresholds.get("publish_score", 35), 35)
    slow_day_score = _safe_int(thresholds.get("slow_day_score", 25), 25)
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
        log("[SCORER] Score breakdown: no articles", "INFO")
        return
    log("=== SCORE BREAKDOWN (TOP 5) ===", "INFO")
    for idx, article in enumerate(scored_articles[:5], start=1):
        title = (article.get("title", "") or "")[:90]
        base_ai = _safe_int(article.get("base_ai_score", article.get("score", 0)), 0)
        freshness = _safe_int(article.get("freshness_bonus", 0), 0)
        trend_eff = _safe_int(article.get("trend_bonus", 0), 0)
        trend_raw = _safe_int(article.get("trend_bonus_raw", 0), 0)
        final_score = _safe_int(article.get("score", 0), 0)
        trend_count = _safe_int(article.get("trend_count", 1), 1)
        log(
            f"[SCORER] {idx}) score={final_score} (base={base_ai} + fresh={freshness} + trend={trend_eff}/{trend_raw}) "
            f"trend_count={trend_count} threshold={threshold} | {title}",
            "INFO",
        )

def _build_cooldown_candidates(scored: list, selected_article: Optional[dict] = None, limit: int = 50) -> list[dict]:
    selected_id = id(selected_article) if selected_article is not None else None
    candidates: list[dict] = []
    for article in scored:
        if selected_id is not None and id(article) == selected_id:
            continue
        candidates.append(
            {
                "title": article.get("title", ""),
                "link": article.get("link", ""),
                "score": _safe_int(article.get("score", 0), 0),
                "score_reason": article.get("score_reason", ""),
                "source_name": article.get("source_name", ""),
                "topic_fingerprint": article.get("topic_fingerprint", ""),
            }
        )
        if len(candidates) >= limit:
            break
    return candidates

def _derive_skip_reason_from_scores(scored: list) -> str:
    if not scored:
        return "no_scored_articles"
    reasons = {}
    for a in scored:
        r = (a.get("score_reason", "") or "").strip()
        if r:
            reasons[r] = reasons.get(r, 0) + 1
    if not reasons:
        return "No article above threshold"
    top_reason = max(reasons, key=reasons.get)
    if top_reason == "ai_invalid_scale_10":
        return "AI returned invalid 10-scale scores"
    if top_reason in {"ai_empty", "ai_parse_failed"}:
        return f"Scoring parse failed ({top_reason})"
    if top_reason in {"ai_unmatched", "ai_unmatched_fallback"}:
        return f"Scoring unmatched ({top_reason})"
    return "No article above threshold"

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

    top_articles = scored[:3]
    top_summary = [
        {
            "title": a.get("title", ""),
            "score": _safe_int(a.get("score", 0), 0),
            "score_breakdown": a.get("score_breakdown", {}),
        }
        for a in top_articles
    ]

    above_threshold = [a for a in scored if a.get("score", 0) >= threshold]

    if not above_threshold:
        top = scored[0] if scored else {}
        return None, {
            "skipped": True,
            "skip_reason": _derive_skip_reason_from_scores(scored),
            "threshold": threshold,
            "top_score": _safe_int(top.get("score", 0), 0),
            "top_title": top.get("title", ""),
            "top_articles": top_summary,
            "scored_count": len(scored),
            "cooldown_candidates": _build_cooldown_candidates(scored),
        }

    selected = above_threshold[0]
    return selected, {
        "skipped": False,
        "threshold": threshold,
        "top_articles": top_summary,
        "scored_count": len(scored),
        "cooldown_candidates": _build_cooldown_candidates(scored, selected),
    }
