"""
agents/scorer_helpers.py - Skorer Yardımcı Fonksiyonlar ve Eşleştirme (v5.3)
AI cevaplarını makalelerle eşleştirme, puan doğrulama ve matematiksel yardımcılar burada.
v1.1: Tip güvenliği (Type hints) eklendi.
"""
import os
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple
from core.helpers import is_similar_title
from core.logger import log

# ── Sabitler ─────────────────────────────────────────────────────────────────
BATCH_SIZE: int = 8
BATCH_DELAY_SECONDS: int = 3
UNSCORED_DEFAULT: int = 0

CROSS_VALIDATE_THRESHOLD: float = 0.35

FALLBACK_SCORE_HIGH: int = 35
FALLBACK_SCORE_MEDIUM: int = 30
FALLBACK_SCORE_LOW: int = 25

FRESHNESS_TIERS: List[Tuple[int, int]] = [
    (1, 10),
    (3, 7),
    (6, 4),
    (12, 1),
]
FRESHNESS_OLD_MALUS: int = -4
TREND_BONUS_CAP: int = 18

_STRICT_SCALE_APPEND: str = (
    "\n\nKRITIK EK KURAL:\n"
    "- puan alani SADECE 0-100 arasi TAM SAYI olmali.\n"
    "- 1-10 olcegi KESINLIKLE kullanma.\n"
    "- Ondalik puan kullanma.\n"
    "- Bu kurala uymazsan cevap gecersiz sayilacak.\n"
)
_JSON_REPAIR_APPEND: str = (
    "\n\nKRITIK CEVAP FORMATI:\n"
    "- SADECE GECERLI JSON don.\n"
    "- Markdown, aciklama, kod blogu kullanma.\n"
    "- Cikti yalnizca JSON dizi olmali.\n"
    "- Her ogede: sira, baslik, puan, gerekce, detay alanlari olmali.\n"
    "- detay icinde su anahtarlar olmali: guncellik, etkilesim_potansiyeli, benzersizlik, gundem_gucu, paylasilabilirlik.\n"
)

_SCORER_MAX_TOKENS: int = 4000

_SCORE_COMPONENTS: Dict[str, int] = {
    "guncellik": 20,
    "etkilesim_potansiyeli": 25,
    "benzersizlik": 20,
    "gundem_gucu": 20,
    "paylasilabilirlik": 15,
}
_LEGACY_DETAIL_MAP: Dict[str, str] = {
    "guncel": "guncellik",
    "paylasim": "paylasilabilirlik",
    "ozgunluk": "benzersizlik",
    "etki": "gundem_gucu",
    "duygu": "etkilesim_potansiyeli",
}

# ═══════════════════════════════════════════════════════════════════════════════
# ÇEVRESEL KONTROL & MATEMATİK YARDIMCILARI
# ═══════════════════════════════════════════════════════════════════════════════

def _is_score_breakdown_enabled() -> bool:
    value: str = os.environ.get("DEBUG_SCORE_BREAKDOWN", "false").strip().lower()
    return value in ("1", "true", "yes", "on")

def _allow_skip_as_success() -> bool:
    return False

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def _safe_score_number(value: Any, default: int = 0) -> int:
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

def _extract_raw_ai_score(ai_result: Dict[str, Any]) -> int:
    raw: Any = None
    for key in ("puan", "score", "skor"):
        if key in ai_result:
            raw = ai_result.get(key)
            break
    return _safe_score_number(raw, UNSCORED_DEFAULT)

def _normalize_ai_score(ai_result: Dict[str, Any]) -> int:
    numeric: int = _extract_raw_ai_score(ai_result)
    return _clamp_score(numeric)

def _is_probably_ten_scale(scores: List[int]) -> bool:
    positives: List[int] = [s for s in scores if s > 0]
    if not positives:
        return False
    return max(positives) <= 10

def _format_articles_numbered(articles: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for i, article in enumerate(articles, start=1):
        title = article.get("title", "No title").strip()
        summary = article.get("summary", "No summary").strip()
        if len(summary) > 150:
            summary = summary[:147] + "..."
        lines.append(f"{i}. Baslik: {title} | Ozet: {summary}")
    return "\n".join(lines)

def _split_into_batches(articles: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    return [articles[i: i + BATCH_SIZE] for i in range(0, len(articles), BATCH_SIZE)]

def _mark_unscored_batch(batch: List[Dict[str, Any]], reason: str, all_scored: List[Dict[str, Any]]) -> None:
    for article in batch:
        article["base_ai_score"] = UNSCORED_DEFAULT
        article["score"] = UNSCORED_DEFAULT
        article["score_breakdown"] = {key: 0 for key in _SCORE_COMPONENTS}
        article["score_reason"] = reason
        all_scored.append(article)

def _normalize_ai_results(parsed: Any) -> Optional[List[Any]]:
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# AI EŞLEŞTİRME FONKSİYONLARI
# ═══════════════════════════════════════════════════════════════════════════════

def _match_by_order(ai_result: Dict[str, Any], articles: List[Dict[str, Any]], used_indices: Set[int]) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    sira: Any = ai_result.get("sira")
    if sira is None:
        return None, None

    try:
        index: int = int(sira) - 1
    except (ValueError, TypeError):
        log(f"[SCORER] Invalid sira value: {sira}", "WARNING")
        return None, None

    if not (0 <= index < len(articles)) or index in used_indices:
        return None, None

    ai_title: str = (ai_result.get("baslik", "") or "").strip()
    article_title: str = (articles[index].get("title", "") or "").strip()

    if not ai_title:
        log(f"[SCORER] Order match accepted (no AI title): sira={sira} -> {article_title[:60]}", "INFO")
        return articles[index], index

    if is_similar_title(ai_title, article_title, threshold=CROSS_VALIDATE_THRESHOLD):
        log(f"[SCORER] Order match accepted: sira={sira}", "INFO")
        return articles[index], index

    def _normalize_str(s: str) -> str:
        return unicodedata.normalize("NFC", s).lower().strip()

    if _normalize_str(ai_title) == _normalize_str(article_title):
        log(f"[SCORER] Order match accepted (unicode normalized): sira={sira}", "INFO")
        return articles[index], index

    log(
        f"[SCORER] Order match REJECTED: sira={sira}, "
        f"AI='{ai_title[:50]}' vs Article='{article_title[:50]}'",
        "WARNING",
    )
    return None, None

def _match_by_exact_title(ai_result: Dict[str, Any], articles: List[Dict[str, Any]], used_indices: Set[int]) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    ai_title: str = (ai_result.get("baslik", "") or "").strip()
    if not ai_title:
        return None, None

    def _norm(s: str) -> str:
        return unicodedata.normalize("NFC", s).lower().strip()

    ai_norm: str = _norm(ai_title)
    for i, article in enumerate(articles):
        if i in used_indices:
            continue
        article_norm: str = _norm(article.get("title", ""))
        if ai_norm == article_norm:
            log(f"[SCORER] Exact title match: '{ai_title[:40]}'", "INFO")
            return article, i
    return None, None

def _calculate_title_similarity(title1: str, title2: str) -> float:
    if not title1 or not title2:
        return 0.0
    words1: Set[str] = set(title1.lower().split())
    words2: Set[str] = set(title2.lower().split())
    if not words1 or not words2:
        return 0.0
    intersection: Set[str] = words1.intersection(words2)
    union: Set[str] = words1.union(words2)
    return len(intersection) / len(union) if union else 0.0

def _match_by_fuzzy_title(ai_result: Dict[str, Any], articles: List[Dict[str, Any]], used_indices: Set[int]) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    ai_title: str = (ai_result.get("baslik", "") or "").strip()
    if not ai_title:
        return None, None

    FUZZY_THRESHOLD: float = 0.5
    best_match: Optional[Dict[str, Any]] = None
    best_index: Optional[int] = None
    best_similarity: float = 0.0

    for i, article in enumerate(articles):
        if i in used_indices:
            continue
        article_title: str = article.get("title", "").strip()
        if is_similar_title(ai_title, article_title, threshold=FUZZY_THRESHOLD):
            similarity: float = _calculate_title_similarity(ai_title, article_title)
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = article
                best_index = i

    if best_match:
        log(
            f"[SCORER] Fuzzy match: similarity={best_similarity:.2f}, "
            f"AI='{ai_title[:40]}' -> Article='{best_match.get('title', '')[:40]}'",
            "INFO",
        )
        return best_match, best_index

    return None, None

def _match_ai_results_to_articles(ai_results: List[Dict[str, Any]], articles: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    matched: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    used_indices: Set[int] = set()
    log(f"[SCORER] Matching {len(ai_results)} AI results to {len(articles)} articles", "INFO")

    for idx, ai_result in enumerate(ai_results, start=1):
        if not isinstance(ai_result, dict):
            log(f"[SCORER] Skipping non-dict AI result #{idx}", "WARNING")
            continue

        matched_article, matched_index = _match_by_order(ai_result, articles, used_indices)

        if matched_article is None:
            matched_article, matched_index = _match_by_exact_title(ai_result, articles, used_indices)

        if matched_article is None:
            matched_article, matched_index = _match_by_fuzzy_title(ai_result, articles, used_indices)

        if matched_article is not None and matched_index is not None:
            matched.append((ai_result, matched_article))
            used_indices.add(matched_index)
        else:
            ai_title: str = (ai_result.get("baslik", "") or "")[:60]
            ai_sira: Any = ai_result.get("sira", "?")
            log(f"[SCORER] UNMATCHED AI result #{idx}: sira={ai_sira}, title='{ai_title}'", "WARNING")

    match_rate: float = (len(matched) / len(ai_results) * 100) if ai_results else 0
    log(f"[SCORER] Match rate: {len(matched)}/{len(ai_results)} ({match_rate:.1f}%)", "INFO")
    return matched

# ═══════════════════════════════════════════════════════════════════════════════
# PUAN DÖKÜMÜ (BREAKDOWN) FONKSİYONLARI
# ═══════════════════════════════════════════════════════════════════════════════

def _clamp_component(value: int, maximum: int) -> int:
    return max(0, min(maximum, value))

def _normalize_detail_key(raw_key: str) -> str:
    key: str = (raw_key or "").strip().lower()
    replacements: Dict[str, str] = {"ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c"}
    for src, dst in replacements.items():
        key = key.replace(src, dst)
    key = key.replace(" ", "_").replace("-", "_")
    return _LEGACY_DETAIL_MAP.get(key, key)

def _extract_score_breakdown(ai_result: Dict[str, Any], base_score: int) -> Dict[str, int]:
    detail: Any = ai_result.get("detay") or ai_result.get("alt_skorlar") or ai_result.get("breakdown") or {}
    normalized: Dict[str, int] = {}
    if isinstance(detail, dict):
        for raw_key, raw_value in detail.items():
            key: str = _normalize_detail_key(str(raw_key))
            if key not in _SCORE_COMPONENTS:
                continue
            normalized[key] = _clamp_component(_safe_score_number(raw_value, 0), _SCORE_COMPONENTS[key])
    if normalized:
        for key, maximum in _SCORE_COMPONENTS.items():
            normalized.setdefault(key, 0)
        return normalized
    weighted: Dict[str, int] = {}
    remaining: int = _clamp_score(base_score)
    for idx, (key, maximum) in enumerate(_SCORE_COMPONENTS.items()):
        if idx == len(_SCORE_COMPONENTS) - 1:
            value: int = min(maximum, remaining)
        else:
            value = int(round(base_score * (maximum / 100)))
            value = min(maximum, value, remaining)
        weighted[key] = value
        remaining -= value
    return weighted

def _breakdown_total(breakdown: Dict[str, int]) -> int:
    return _clamp_score(sum(_safe_int(breakdown.get(k, 0), 0) for k in _SCORE_COMPONENTS))

def _apply_component_delta(article: Dict[str, Any], key: str, delta: int) -> None:
    breakdown: Any = article.get("score_breakdown")
    if not isinstance(breakdown, dict) or key not in _SCORE_COMPONENTS:
        return
    breakdown[key] = _clamp_component(_safe_int(breakdown.get(key, 0), 0) + delta, _SCORE_COMPONENTS[key])
    article["score_breakdown"] = breakdown
    article["score"] = _breakdown_total(breakdown)
