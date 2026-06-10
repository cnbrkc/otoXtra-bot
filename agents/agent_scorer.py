"""
Viral scoring agent. (v4.3)

v4.3:
  - AI-haber eslesmesi guclendirildi.
  - Dusuk coverage durumunda "tum haberleri zorunlu puanla" retry eklendi.
  - Hala eslesmeyenler icin sinirli tekli rescue scoring eklendi.
  - 24 saat duplicate topic elemesi korunuyor.
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
    is_topic_recently_posted,
)
from core.logger import log
from core.state_manager import get_stage, set_stage


BATCH_SIZE: int = 20
BATCH_DELAY_SECONDS: int = 3
UNSCORED_DEFAULT: int = 0
CROSS_VALIDATE_THRESHOLD: float = 0.4
MATCH_MIN_COVERAGE: float = 0.60

SINGLE_RESCUE_LIMIT_DEFAULT: int = 8

FRESHNESS_TIERS = [
    (1, 10),
    (3, 7),
    (6, 4),
    (12, 1),
]
FRESHNESS_OLD_MALUS: int = -4
TREND_BONUS_CAP: int = 18

_STRICT_SCALE_APPEND = (
    "\n\nKRITIK EK KURAL:\n"
    "- puan alani SADECE 0-100 arasi TAM SAYI olmali.\n"
    "- 1-10 olcegi KESINLIKLE kullanma.\n"
    "- Ondalik puan kullanma.\n"
    "- Bu kurala uymazsan cevap gecersiz sayilacak.\n"
)

_FORCE_FULL_COVERAGE_APPEND = (
    "\n\nEK ZORUNLU KURAL:\n"
    "- Listede kac haber varsa O KADAR sonuc don.\n"
    "- Her madde icin ayri sonuc yaz.\n"
    "- Hicbir maddeyi atlama.\n"
    "- Cevap JSON listesi olmali.\n"
)

_RESULT_LIST_KEYS = ("results", "sonuclar", "haberler", "items", "data", "list")


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


def _safe_score_number(value, default=0) -> int:
    try:
        if isinstance(value, str):
            cleaned = value.strip().replace(",", ".")
            if cleaned == "":
                return default
            return int(round(float(cleaned)))
        if isinstance(value, (int, float)):
            return int(round(float(value)))
    except Exception:
        pass
    return default


def _clamp_score(value: int) -> int:
    return max(0, min(100, value))


def _extract_raw_ai_score(ai_result: dict) -> int:
    raw = None
    for key in ("puan", "score", "skor"):
        if key in ai_result:
            raw = ai_result.get(key)
            break
    return _safe_score_number(raw, UNSCORED_DEFAULT)


def _normalize_ai_score(ai_result: dict) -> int:
    numeric = _extract_raw_ai_score(ai_result)
    return _clamp_score(numeric)


def _is_probably_ten_scale(scores: list[int]) -> bool:
    positives = [s for s in scores if s > 0]
    if not positives:
        return False
    return max(positives) <= 10


def _format_articles_numbered(articles: list) -> str:
    lines = []
    for i, article in enumerate(articles, start=1):
        title = article.get("title", "No title").strip()
        summary = article.get("summary", "No summary").strip()
        if len(summary) > 300:
            summary = summary[:297] + "..."
        lines.append(f"{i}. Baslik: {title} | Ozet: {summary}")
    return "\n".join(lines)



def _verify_automotive_relevance(articles: list) -> set:
    """AI ile otomotiv konusu kontrolu. Otomotiv disi olanlarin index'lerini dondurur."""
    if not articles:
        return set()
    
    verify_prompt = (
        "Sen bir icerik siniflandirma uzmanisin. "
        "Sana verilen haber basliklarinin otomotiv/arac/motorlu tasit sektoruyle ilgili olup olmadigini degerlendir.\n\n"
        "OTOMOTIV KAPSAMI: Otomobil, motosiklet, kamyon, otobus, traktor, elektrikli arac, "
        "hibrit, otonom surus, arac teknolojileri, otomotiv endustrisi, yedek parca, "
        "lastik, akaryakit/sarj, arac testleri, trafik duzenlemeleri, otomotiv fuarlari, "
        "arac tasarimi, motorsporlari.\n\n"
        "OTOMOTIV DEGIL (0 puan): Market/indirim katalogu (Bim, A101, Sok, Migros vb.), "
        "elektronik, bilgisayar, telefon, beyaz esya, mobilya, gida, emlak, tatil, "
        "genel saglik, genel egitim, spor (motor sporu haric), finans, kripto, "
        "alisveris, kampanya, brosur.\n\n"
        "Her haber icin JSON don: [{\"sira\": 1, \"otomotiv\": true/false}]\n"
        "SADECE JSON DONDUR."
    )
    
    verify_batches = [articles[i:i + 30] for i in range(0, len(articles), 30)]
    non_auto_indices: set[int] = set()
    global_offset = 0
    
    for batch in verify_batches:
        ai_response = ask_ai(f"{verify_prompt}\n\n{_format_articles_numbered(batch)}")
        if not ai_response:
            log("Automotive verification AI call failed, batch passes through", "WARNING")
            global_offset += len(batch)
            continue
        
        parsed = parse_ai_json(ai_response)
        results = _normalize_ai_results(parsed)
        if not results:
            global_offset += len(batch)
            continue
        
        for result in results:
            if not isinstance(result, dict):
                continue
            sira = result.get("sira")
            is_auto = result.get("otomotiv", True)
            if sira is not None and not is_auto:
                try:
                    index = global_offset + int(sira) - 1
                    if 0 <= index < len(articles):
                        non_auto_indices.add(index)
                except (ValueError, TypeError):
                    pass
        
        global_offset += len(batch)
    
    return non_auto_indices

def _split_into_batches(articles: list) -> list:
    return [articles[i: i + BATCH_SIZE] for i in range(0, len(articles), BATCH_SIZE)]


def _mark_unscored_batch(batch: list, reason: str, all_scored: list) -> None:
    for article in batch:
        article["base_ai_score"] = UNSCORED_DEFAULT
        article["score"] = UNSCORED_DEFAULT
        article["score_breakdown"] = {key: 0 for key in _SCORE_COMPONENTS}
        article["score_reason"] = reason
        all_scored.append(article)


def _extract_ai_result_list(parsed: object) -> Optional[list]:
    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        for key in _RESULT_LIST_KEYS:
            value = parsed.get(key)
            if isinstance(value, list):
                return value
        if any(k in parsed for k in ("sira", "baslik", "puan", "score", "skor")):
            return [parsed]

    return None


def _normalize_ai_results(parsed: object) -> Optional[list]:
    return _extract_ai_result_list(parsed)


def _match_by_order(
    ai_result: dict,
    articles: list,
    used_indices: set,
    trust_order: bool = False,
) -> tuple[Optional[dict], Optional[int]]:
    sira = ai_result.get("sira")
    if sira is None:
        return None, None

    try:
        index = int(sira) - 1
    except (ValueError, TypeError):
        return None, None

    if not (0 <= index < len(articles)) or index in used_indices:
        return None, None

    if trust_order:
        return articles[index], index

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


def _match_ai_results_to_articles(ai_results: list, articles: list, relaxed: bool = False) -> list:
    matched = []
    used_indices = set()

    for ai_result in ai_results:
        if not isinstance(ai_result, dict):
            continue

        matched_article, matched_index = _match_by_order(
            ai_result,
            articles,
            used_indices,
            trust_order=relaxed,
        )
        if matched_article is None and not relaxed:
            matched_article, matched_index = _match_by_exact_title(ai_result, articles, used_indices)
        if matched_article is None and not relaxed:
            matched_article, matched_index = _match_by_fuzzy_title(ai_result, articles, used_indices)

        if matched_article is not None and matched_index is not None:
            matched.append((ai_result, matched_article))
            used_indices.add(matched_index)

    return matched


def _zip_fallback_pairs(ai_results: list, batch: list, already_matched: list) -> list:
    matched_article_ids = {id(article) for _, article in already_matched}
    remaining_articles = [a for a in batch if id(a) not in matched_article_ids]
    if not remaining_articles:
        return []

    def _sort_key(item: dict):
        sira = _safe_int(item.get("sira"), 10_000)
        return sira

    ai_candidates = [r for r in ai_results if isinstance(r, dict)]
    ai_candidates.sort(key=_sort_key)

    pairs = []
    used_ai = set()
    for article in remaining_articles:
        pick = None
        for idx, ai_item in enumerate(ai_candidates):
            if idx in used_ai:
                continue
            score = _extract_raw_ai_score(ai_item)
            if score > 0:
                pick = (idx, ai_item)
                break
        if pick is None:
            break
        used_ai.add(pick[0])
        pairs.append((pick[1], article))

    return pairs


def _ask_and_parse_batch(batch: list, prompt: str) -> tuple[Optional[list], Optional[list], str]:
    ai_response = ask_ai(f"{prompt}\n\n{_format_articles_numbered(batch)}")
    if not ai_response:
        return None, None, "ai_empty"

    parsed = parse_ai_json(ai_response)
    ai_results = _normalize_ai_results(parsed)
    if not ai_results:
        return None, None, "ai_parse_failed"

    matched_pairs = _match_ai_results_to_articles(ai_results, batch, relaxed=False)
    return matched_pairs, ai_results, ""


def _score_batch(batch: list, prompt: str) -> tuple[Optional[list], str]:
    matched_pairs, ai_results, fail_reason = _ask_and_parse_batch(batch, prompt)
    if fail_reason:
        return None, fail_reason

    coverage = (len(matched_pairs) / len(batch)) if batch else 0.0

    if coverage < MATCH_MIN_COVERAGE:
        matched_pairs_retry, ai_results_retry, retry_reason = _ask_and_parse_batch(
            batch,
            prompt + _FORCE_FULL_COVERAGE_APPEND,
        )
        if not retry_reason and matched_pairs_retry is not None:
            if len(matched_pairs_retry) > len(matched_pairs):
                matched_pairs = matched_pairs_retry
                ai_results = ai_results_retry

    coverage = (len(matched_pairs) / len(batch)) if batch else 0.0
    if coverage < MATCH_MIN_COVERAGE and ai_results:
        relaxed_pairs = _match_ai_results_to_articles(ai_results, batch, relaxed=True)
        if len(relaxed_pairs) > len(matched_pairs):
            matched_pairs = relaxed_pairs

    coverage = (len(matched_pairs) / len(batch)) if batch else 0.0
    if coverage < MATCH_MIN_COVERAGE and ai_results:
        extra = _zip_fallback_pairs(ai_results, batch, matched_pairs)
        if extra:
            matched_pairs.extend(extra)

    return matched_pairs, ""


def _extract_first_result_dict(parsed: object) -> Optional[dict]:
    if isinstance(parsed, dict):
        nested = _extract_ai_result_list(parsed)
        if nested and isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    return item
        if any(k in parsed for k in ("puan", "score", "skor", "baslik", "sira")):
            return parsed
    elif isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
    return None


def _single_article_prompt(base_prompt: str, article: dict) -> str:
    title = (article.get("title", "") or "").strip()
    summary = (article.get("summary", "") or "").strip()
    if len(summary) > 300:
        summary = summary[:297] + "..."

    return (
        f"{base_prompt}\n\n"
        "TEK HABER DEGERLENDIRMESI:\n"
        "Asagidaki tek haberi 0-100 arasi puanla ve JSON don.\n"
        "JSON alani: baslik, puan, gerekce, detay\n"
        f"Baslik: {title}\n"
        f"Ozet: {summary}\n"
    )


def _score_single_article(article: dict, prompt: str) -> Optional[dict]:
    response = ask_ai(_single_article_prompt(prompt, article))
    if not response:
        return None

    parsed = parse_ai_json(response)
    return _extract_first_result_dict(parsed)


def _rescue_unmatched_articles(unmatched: list, prompt: str, limit: int) -> list:
    rescued = []
    if limit <= 0 or not unmatched:
        return rescued

    target = unmatched[:limit]
    for article in target:
        ai_result = _score_single_article(article, prompt)
        if not isinstance(ai_result, dict):
            continue
        if _extract_raw_ai_score(ai_result) <= 0:
            continue
        rescued.append((ai_result, article))
    return rescued


_SCORE_COMPONENTS = {
    "guncellik": 20,
    "etkilesim_potansiyeli": 25,
    "benzersizlik": 20,
    "gundem_gucu": 20,
    "paylasilabilirlik": 15,
}

_LEGACY_DETAIL_MAP = {
    "guncel": "guncellik",
    "paylasim": "paylasilabilirlik",
    "ozgunluk": "benzersizlik",
    "etki": "gundem_gucu",
    "duygu": "etkilesim_potansiyeli",
}


def _clamp_component(value: int, maximum: int) -> int:
    return max(0, min(maximum, value))


def _normalize_detail_key(raw_key: str) -> str:
    key = (raw_key or "").strip().lower()
    replacements = {"ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"}
    for src, dst in replacements.items():
        key = key.replace(src, dst)
    key = key.replace(" ", "_").replace("-", "_")
    return _LEGACY_DETAIL_MAP.get(key, key)


def _extract_score_breakdown(ai_result: dict, base_score: int) -> dict:
    detail = ai_result.get("detay") or ai_result.get("alt_skorlar") or ai_result.get("breakdown") or {}
    normalized: dict[str, int] = {}

    if isinstance(detail, dict):
        for raw_key, raw_value in detail.items():
            key = _normalize_detail_key(str(raw_key))
            if key not in _SCORE_COMPONENTS:
                continue
            normalized[key] = _clamp_component(_safe_score_number(raw_value, 0), _SCORE_COMPONENTS[key])

    if normalized:
        for key in _SCORE_COMPONENTS:
            normalized.setdefault(key, 0)
        return normalized

    weighted = {}
    remaining = _clamp_score(base_score)
    for idx, (key, maximum) in enumerate(_SCORE_COMPONENTS.items()):
        if idx == len(_SCORE_COMPONENTS) - 1:
            value = min(maximum, remaining)
        else:
            value = int(round(base_score * (maximum / 100)))
            value = min(maximum, value, remaining)
        weighted[key] = value
        remaining -= value
    return weighted


def _breakdown_total(breakdown: dict) -> int:
    return _clamp_score(sum(_safe_int(breakdown.get(k, 0), 0) for k in _SCORE_COMPONENTS))


def _apply_component_delta(article: dict, key: str, delta: int) -> None:
    breakdown = article.get("score_breakdown")
    if not isinstance(breakdown, dict) or key not in _SCORE_COMPONENTS:
        return
    breakdown[key] = _clamp_component(_safe_int(breakdown.get(key, 0), 0) + delta, _SCORE_COMPONENTS[key])
    article["score_breakdown"] = breakdown
    article["score"] = _breakdown_total(breakdown)


def run_viral_scoring(articles: list) -> list:
    if not articles:
        log("No articles for scoring", "INFO")
        return []

    scorer_prompt = load_config("prompts").get("viral_scorer", "")
    if not scorer_prompt:
        log("viral_scorer prompt not found", "WARNING")
        for article in articles:
            article["base_ai_score"] = UNSCORED_DEFAULT
            article["score"] = UNSCORED_DEFAULT
            article["score_reason"] = "prompt_missing"
        return articles

    rescue_limit = _safe_int(
        os.environ.get("SCORER_SINGLE_RESCUE_LIMIT", str(SINGLE_RESCUE_LIMIT_DEFAULT)),
        SINGLE_RESCUE_LIMIT_DEFAULT,
    )
    rescue_limit = max(0, rescue_limit)

    batches = _split_into_batches(articles)
    all_scored = []

    # --- YENI: Otomotiv konu dogrulamasi ---
    non_auto_indices = _verify_automotive_relevance(articles)
    if non_auto_indices:
        log(
            f"Automotive verification: {len(non_auto_indices)}/{len(articles)} "
            f"non-automotive articles filtered to score 0",
            "INFO",
        )
    # ----------------------------------------

    for batch_num, batch in enumerate(batches, start=1):
        # --- YENI: Non-automotive artikelleri ayir, skor 0 yap ---
        auto_batch = []
        for article in batch:
            # Find global index
            global_idx = None
            for gi, ga in enumerate(articles):
                if id(ga) == id(article):
                    global_idx = gi
                    break
            if global_idx is not None and global_idx in non_auto_indices:
                article["base_ai_score"] = UNSCORED_DEFAULT
                article["score"] = UNSCORED_DEFAULT
                article["score_breakdown"] = {key: 0 for key in _SCORE_COMPONENTS}
                article["score_reason"] = "non_automotive"
                all_scored.append(article)
            else:
                auto_batch.append(article)
        
        if not auto_batch:
            if batch_num < len(batches):
                time.sleep(BATCH_DELAY_SECONDS)
            continue
        
        matched_pairs, fail_reason = _score_batch(auto_batch, scorer_prompt)
        if fail_reason:
            _mark_unscored_batch(auto_batch, fail_reason, all_scored)
            _mark_unscored_batch(batch, fail_reason, all_scored)
            if batch_num < len(batches):
                time.sleep(BATCH_DELAY_SECONDS)
            continue

        raw_scores = [_extract_raw_ai_score(ai_result) for ai_result, _ in matched_pairs]
        if _is_probably_ten_scale(raw_scores):
            log("Scorer 10'luk olcek supheleri tespit edildi, strict retry deneniyor", "WARNING")
            matched_pairs_retry, retry_reason = _score_batch(auto_batch, scorer_prompt + _STRICT_SCALE_APPEND)
            if retry_reason:
                _mark_unscored_batch(auto_batch, retry_reason, all_scored)
                if batch_num < len(batches):
                    time.sleep(BATCH_DELAY_SECONDS)
                continue

            retry_raw_scores = [_extract_raw_ai_score(ai_result) for ai_result, _ in matched_pairs_retry]
            if _is_probably_ten_scale(retry_raw_scores):
                _mark_unscored_batch(auto_batch, "ai_invalid_scale_10", all_scored)
                if batch_num < len(batches):
                    time.sleep(BATCH_DELAY_SECONDS)
                continue

            matched_pairs = matched_pairs_retry

        matched_ids = {id(art) for _, art in matched_pairs}
        unmatched = [article for article in auto_batch if id(article) not in matched_ids]

        rescued_pairs = _rescue_unmatched_articles(unmatched, scorer_prompt, rescue_limit)
        if rescued_pairs:
            log(f"scorer rescue: {len(rescued_pairs)}/{len(unmatched)} unmatched article scored", "INFO")
            matched_pairs.extend(rescued_pairs)
            for _, rescued_article in rescued_pairs:
                matched_ids.add(id(rescued_article))

        for ai_result, article in matched_pairs:
            base_score = _normalize_ai_score(ai_result)
            breakdown = _extract_score_breakdown(ai_result, base_score)
            article["base_ai_score"] = base_score
            article["score_breakdown"] = breakdown
            article["score"] = _breakdown_total(breakdown)
            prev_reason = article.get("score_reason", "")
            if prev_reason == "ai_unmatched":
                article["score_reason"] = "ai_scored_rescue"
            else:
                article["score_reason"] = "ai_scored"
            article["score_explanation"] = (ai_result.get("gerekce", "") or "").strip()
            all_scored.append(article)

        for article in batch:
            if id(article) in matched_ids:
                continue
            article["base_ai_score"] = UNSCORED_DEFAULT
            article["score"] = UNSCORED_DEFAULT
            article["score_breakdown"] = {key: 0 for key in _SCORE_COMPONENTS}
            article["score_reason"] = "ai_unmatched"
            all_scored.append(article)

        coverage = (len(matched_ids) / len(batch)) if batch else 0.0
        log(
            f"scorer batch {batch_num}/{len(batches)} match coverage: "
            f"{len(matched_ids)}/{len(batch)} ({coverage:.2f})"
        )

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
        _apply_component_delta(article, "guncellik", bonus)
        if not isinstance(article.get("score_breakdown"), dict):
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
        _apply_component_delta(article, "gundem_gucu", effective_bonus)
        if not isinstance(article.get("score_breakdown"), dict):
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
    if top_reason in {"ai_empty", "ai_parse_failed", "ai_unmatched"}:
        return f"Scoring failed ({top_reason})"
    return "No article above threshold"


def _select_first_non_duplicate_topic(above_threshold: list[dict]) -> tuple[Optional[dict], list[dict]]:
    posted_data = get_posted_news()
    rejected: list[dict] = []

    for article in above_threshold:
        is_dup, match_info = is_topic_recently_posted(article, posted_data)
        if is_dup:
            rejected.append(
                {
                    "title": article.get("title", ""),
                    "score": _safe_int(article.get("score", 0), 0),
                    "matched_title": match_info.get("matched_title", ""),
                    "matched_url": match_info.get("matched_url", ""),
                    "matched_posted_at": match_info.get("matched_posted_at", ""),
                    "similarity": match_info.get("similarity", 0.0),
                    "overlap": _safe_int(match_info.get("overlap", 0), 0),
                    "window_hours": _safe_int(match_info.get("window_hours", 24), 24),
                }
            )
            continue
        return article, rejected

    return None, rejected


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
            "duplicate_topic_filtered": 0,
            "duplicate_topic_examples": [],
        }

    selected, duplicate_rejections = _select_first_non_duplicate_topic(above_threshold)

    if selected is None:
        top = above_threshold[0] if above_threshold else {}
        return None, {
            "skipped": True,
            "skip_reason": "All threshold-passing candidates are duplicates in last 24h",
            "threshold": threshold,
            "top_score": _safe_int(top.get("score", 0), 0),
            "top_title": top.get("title", ""),
            "top_articles": top_summary,
            "scored_count": len(scored),
            "cooldown_candidates": _build_cooldown_candidates(scored),
            "duplicate_topic_filtered": len(duplicate_rejections),
            "duplicate_topic_examples": duplicate_rejections[:5],
        }

    return selected, {
        "skipped": False,
        "threshold": threshold,
        "top_articles": top_summary,
        "scored_count": len(scored),
        "cooldown_candidates": _build_cooldown_candidates(scored, selected),
        "duplicate_topic_filtered": len(duplicate_rejections),
        "duplicate_topic_examples": duplicate_rejections[:5],
    }


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
        "duplicate_topic_filtered": _safe_int(meta.get("duplicate_topic_filtered", 0), 0),
        "duplicate_topic_examples": meta.get("duplicate_topic_examples", []),
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
        "duplicate_topic_filtered": _safe_int(meta.get("duplicate_topic_filtered", 0), 0),
        "duplicate_topic_examples": meta.get("duplicate_topic_examples", []),
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
            skip_reason_lower = (skip_output.get("skip_reason", "") or "").lower()
            force_soft_skip = "duplicates in last 24h" in skip_reason_lower

            if _allow_skip_as_success() or force_soft_skip:
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
