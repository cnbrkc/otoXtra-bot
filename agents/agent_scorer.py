"""
agents/agent_scorer.py - Ana Scorer Ajanı Köprüsü (v5.3 - DRY Refactoring)

v5.3 GÜNCELLEME:
  - 943 satırlık dosya 3 modüle bölündü: 
    agent_scorer.py (köprü), scorer_helpers.py (eşleştirme/döküm), scorer_engine.py (puanlama motoru)
  - orchestrator.py ile uyumlu çalışacak şekilde sadece run() ve output builder fonksiyonları tutuldu.
"""
from typing import Optional
from core.logger import log
from core.state_manager import get_stage, set_stage
from agents.scorer_helpers import _allow_skip_as_success, _safe_int
from agents.scorer_engine import filter_and_score, _get_active_threshold

# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_skip_output(meta: dict) -> dict:
    active_threshold = _get_active_threshold()
    threshold = _safe_int(meta.get("threshold", active_threshold), active_threshold)
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
        "top_articles": meta.get("top_articles", []),
        "scored_count": _safe_int(meta.get("scored_count", 0), 0),
        "cooldown_candidates": meta.get("cooldown_candidates", []),
    }

def _build_success_output(best_article: dict, meta: dict) -> dict:
    return {
        "selected_article": best_article,
        "score": best_article.get("score", 0),
        "title": best_article.get("title", ""),
        "trend_count": best_article.get("trend_count", 1),
        "trend_bonus": best_article.get("trend_bonus", 0),
        "freshness_bonus": best_article.get("freshness_bonus", 0),
        "score_breakdown": best_article.get("score_breakdown", {}),
        "score_reason": best_article.get("score_reason", "unknown"),
        "score_explanation": best_article.get("score_explanation", ""),
        "skipped": False,
        "threshold": _safe_int(meta.get("threshold", 0), 0),
        "top_articles": meta.get("top_articles", []),
        "scored_count": _safe_int(meta.get("scored_count", 0), 0),
        "cooldown_candidates": meta.get("cooldown_candidates", []),
    }

# ═══════════════════════════════════════════════════════════════════════════════
# ANA ÇALIŞTIRICI
# ═══════════════════════════════════════════════════════════════════════════════

def run() -> bool:
    log("[SCORER] ===== AGENT_SCORER STARTING =====", "INFO")

    fetch_stage = get_stage("fetch")
    if fetch_stage.get("status") != "done":
        log("[SCORER] Fetch stage not done, cannot proceed", "ERROR")
        set_stage("score", "error", error="fetch stage not done")
        return False

    articles = (fetch_stage.get("output", {}) or {}).get("articles", [])
    if not articles:
        log("[SCORER] No articles in fetch output", "ERROR")
        set_stage("score", "error", error="No articles in fetch output")
        return False

    log(f"[SCORER] Received {len(articles)} articles from fetch stage", "INFO")
    set_stage("score", "running")

    try:
        best_article, meta = filter_and_score(articles)

        if best_article is None:
            skip_output = _build_skip_output(meta)
            if _allow_skip_as_success():
                set_stage("score", "done", output=skip_output)
                log(
                    f"[SCORER] Skipped: {skip_output['skip_reason']} "
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
            log(f"[SCORER] Failed: {skip_output['skip_reason']}", "ERROR")
            return False

        set_stage("score", "done", output=_build_success_output(best_article, meta))
        log(f"[SCORER] Success: Selected article score={best_article.get('score', 0)}", "INFO")
        return True

    except Exception as exc:
        log(f"[SCORER] Critical exception: {exc}", "ERROR")
        import traceback
        log(f"[SCORER] Traceback: {traceback.format_exc()}", "ERROR")
        set_stage("score", "error", error=str(exc))
        return False
