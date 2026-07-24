"""
agents/agent_image.py - Görsel İşleme Ajanı Ana Köprüsü (v8.9 - DRY Refactoring)
1809 satırlık devasa dosya 6 modüle bölündü:
  agent_image (köprü), image_utils (URL/Kontrol), image_nitter (Nitter), 
  image_processor (PIL/Logo), image_scraper (HTML Parse), image_search (DDG/AI)
"""
import os
from collections import Counter
from core.config_loader import load_config
from core.logger import log
from core.state_manager import get_stage, set_stage

from agents.image_utils import (
    _read_bool_env, _read_int_env, _get_image_validation_limits, _get_platform_resize_limits,
    _build_relaxed_limits, _is_nitter_url, _is_test_mode, _download_image_with_reason,
    _read_image_meta, _file_sha256, _visual_signature, _dhash, _hamming,
    _adaptive_perceptual_threshold, _should_resize_for_platform, _score_image_quality,
    _safe_unlink, _upsert_candidate
)
from agents.image_processor import resize_and_crop, add_logo
from agents.image_scraper import scrape_article_image_urls, _collect_article_candidates
from agents.image_search import get_duckduckgo_image_candidates, _ai_search_image_url

_DEFAULT_PERCEPTUAL_HASH_THRESHOLD = 6

def prepare_images(article: dict) -> list[str]:
    settings_config = load_config("settings")
    images_settings = settings_config.get("images", {})

    should_add_logo = bool(images_settings.get("add_logo", True))
    feed_image_width = int(images_settings.get("feed_image_width", 1200))
    feed_image_height = int(images_settings.get("feed_image_height", 630))
    max_candidates_to_try = int(images_settings.get("max_candidates_per_article", 10))
    enable_selected_article_scrape = bool(images_settings.get("enable_article_image_scrape", True))
    env_selected_article_scrape = _read_bool_env("ENABLE_ARTICLE_IMAGE_SCRAPE")
    if env_selected_article_scrape is not None:
        enable_selected_article_scrape = env_selected_article_scrape
    perceptual_threshold = int(images_settings.get("perceptual_hash_threshold", _DEFAULT_PERCEPTUAL_HASH_THRESHOLD))
    
    limits = _get_image_validation_limits()
    resize_limits = _get_platform_resize_limits()
    target_ratio = feed_image_width / feed_image_height

    env_max_images = _read_int_env("MAX_IMAGES_PER_NEWS")
    if env_max_images is not None and env_max_images > 0:
        max_images_per_news = env_max_images
        source = "env"
    else:
        max_images_per_news = int(images_settings.get("max_images_per_news", 1))
        source = "settings"

    if max_images_per_news < 1: max_images_per_news = 1
    effective_try_limit = max_candidates_to_try * max(1, min(max_images_per_news, 4))
    effective_try_limit = max(effective_try_limit, max_candidates_to_try)
    effective_try_limit = min(effective_try_limit, 60)

    article_title = article.get("title", "")[:120]
    article_link = article.get("link", "")
    is_nitter_article = _is_nitter_url(article_link)
    effective_scrape = True  

    log("-" * 40)
    log(f"Gorsel hazirlama basladi: {article_title}")
    log(f"Image limits: max_images_per_news={max_images_per_news} ({source}), max_candidates_to_try={max_candidates_to_try}, effective_try_limit={effective_try_limit}, perceptual_threshold={perceptual_threshold}, selected_article_scrape={effective_scrape} (nitter={is_nitter_article})")
    log(f"Validation limits: min_width={limits['min_width']}, min_height={limits['min_height']}, min_area={limits['min_area']}, ratio={limits['min_aspect']:.2f}-{limits['max_aspect']:.2f}")
    log(f"Resize limits: max_width={resize_limits['max_width']}, max_height={resize_limits['max_height']}, max_area={resize_limits['max_area']}, max_bytes={resize_limits['max_bytes']}")

    prepared_paths = []
    used_sources = []

    candidate_pool = _collect_article_candidates(article, effective_try_limit)
    if effective_scrape and article.get("can_scrape_image", True) and article_link:
        log(f"Secilen haber icin sayfa gorsel scrape aktif (nitter={is_nitter_article})")
        for c in scrape_article_image_urls(article_link, max_candidates=effective_try_limit):
            _upsert_candidate(candidate_pool, c)
    elif not effective_scrape:
        log("Secilen haber sayfa gorsel scrape kapali", "INFO")

    candidate_pool = sorted(candidate_pool, key=lambda x: (int(x.get("priority", 99)), x.get("url", "")))
    candidate_pool = candidate_pool[:effective_try_limit]
    log(f"Toplam aday URL (canonical): {len(candidate_pool)}")

    tried_keys = set()
    seen_content_hashes = set()
    seen_perceptual_records = []
    fail_reasons = Counter()
    tried_count = 0
    accepted = []
    retry_relaxed_pool = []
    test_mode = _is_test_mode()

    for idx, candidate in enumerate(candidate_pool, start=1):
        candidate_url = candidate.get("url", "")
        source_type = candidate.get("source_type", "unknown")
        key = candidate.get("key", "") 
        if not candidate_url: continue
        if key in tried_keys:
            fail_reasons["duplicate_candidate_key"] += 1
            continue
        tried_keys.add(key)
        tried_count += 1
        log(f"Aday deneniyor ({idx}/{len(candidate_pool)}): {candidate_url[:120]} | source={source_type}")

        downloaded, reason = _download_image_with_reason(candidate_url, limits)
        if not downloaded:
            fail_reasons[reason] += 1
            log(f"Aday elendi: {reason}", "WARNING")
            if reason.startswith("too_small:") or reason.startswith("bad_aspect:"):
                retry_relaxed_pool.append(candidate)
            continue

        try:
            width, height, size_kb = _read_image_meta(downloaded)
            content_hash = _file_sha256(downloaded)
            if content_hash in seen_content_hashes:
                fail_reasons["duplicate_image_content"] += 1
                log("Aday elendi: duplicate_image_content", "WARNING")
                _safe_unlink(downloaded)
                continue

            current_signature = _visual_signature(candidate_url)
            try:
                current_phash = _dhash(downloaded)
                is_near_dup = False
                for prev_phash, prev_signature in seen_perceptual_records:
                    dynamic_threshold = _adaptive_perceptual_threshold(perceptual_threshold, current_signature, prev_signature)
                    if _hamming(current_phash, prev_phash) <= dynamic_threshold:
                        is_near_dup = True
                        break
                if is_near_dup:
                    fail_reasons["near_duplicate_perceptual"] += 1
                    log("Aday elendi: near_duplicate_perceptual", "WARNING")
                    _safe_unlink(downloaded)
                    continue
            except Exception as ph_exc:
                fail_reasons["perceptual_hash_error"] += 1
                log(f"Perceptual hash atlandi: {ph_exc}", "WARNING")
                current_phash = None

            processed = downloaded
            needs_resize, resize_reason = _should_resize_for_platform(downloaded, resize_limits)
            if needs_resize:
                log(f"Resize uygulanacak: {resize_reason}")
                processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
            else:
                log(f"Resize atlandi: {resize_reason}")

            if should_add_logo:
                processed = add_logo(processed)

            if test_mode:
                try:
                    from core.image_generator import create_social_card
                    if processed and os.path.exists(processed):
                        card_path = processed.replace(".jpg", "_card.jpg")
                        post_text = article.get("post_text_for_card", "Başlık yok")
                        create_social_card(post_text=post_text, image_path=processed, output_path=card_path)
                        if os.path.exists(card_path):
                            _safe_unlink(processed)
                            processed = card_path
                            log("Test Modu: Sosyal medya kartı başarıyla oluşturuldu.", "INFO")
                except Exception as exc:
                    log(f"Kart oluşturma adımı atlandı: {exc}", "WARNING")

            score, score_detail = _score_image_quality(width=width, height=height, size_kb=size_kb, source_type=source_type, target_ratio=target_ratio)
            accepted.append({"path": processed, "url": candidate_url, "source_type": source_type, "score": score, "score_detail": score_detail, "phash": current_phash, "signature": current_signature, "content_hash": content_hash})
            seen_content_hashes.add(content_hash)
            if current_phash is not None:
                seen_perceptual_records.append((current_phash, current_signature))
            log(f"Aday basarili: {reason} -> quality={score:.1f} ({score_detail})")
        except Exception as exc:
            fail_reasons[f"processing_error:{exc}"] += 1
            log(f"Aday islenemedi: {exc}", "WARNING")
            _safe_unlink(downloaded)

    if len(accepted) < max_images_per_news and retry_relaxed_pool:
        relaxed_limits = _build_relaxed_limits(limits)
        relaxed_threshold = max(2, perceptual_threshold - 2)
        log(f"Relaxed pass devrede: need={max_images_per_news - len(accepted)}, retry_candidates={len(retry_relaxed_pool)}, ratio={relaxed_limits['min_aspect']:.2f}-{relaxed_limits['max_aspect']:.2f}, min={relaxed_limits['min_width']}x{relaxed_limits['min_height']}, area={relaxed_limits['min_area']}")

        for candidate in retry_relaxed_pool:
            if len(accepted) >= max_images_per_news: break
            candidate_url = candidate.get("url", "")
            source_type = candidate.get("source_type", "unknown")
            if not candidate_url: continue
            downloaded, reason = _download_image_with_reason(candidate_url, relaxed_limits)
            if not downloaded:
                fail_reasons[f"relaxed_{reason}"] += 1
                continue
            try:
                width, height, size_kb = _read_image_meta(downloaded)
                content_hash = _file_sha256(downloaded)
                if content_hash in seen_content_hashes:
                    fail_reasons["relaxed_duplicate_image_content"] += 1
                    _safe_unlink(downloaded)
                    continue
                current_signature = _visual_signature(candidate_url)
                try:
                    current_phash = _dhash(downloaded)
                    is_near_dup = False
                    for prev_phash, prev_signature in seen_perceptual_records:
                        dynamic_threshold = _adaptive_perceptual_threshold(relaxed_threshold, current_signature, prev_signature)
                        if _hamming(current_phash, prev_phash) <= dynamic_threshold:
                            is_near_dup = True
                            break
                    if is_near_dup:
                        fail_reasons["relaxed_near_duplicate_perceptual"] += 1
                        _safe_unlink(downloaded)
                        continue
                except Exception:
                    current_phash = None
                processed = downloaded
                needs_resize, resize_reason = _should_resize_for_platform(downloaded, resize_limits)
                if needs_resize:
                    log(f"Resize uygulanacak (relaxed): {resize_reason}")
                    processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
                else:
                    log(f"Resize atlandi (relaxed): {resize_reason}")
                if should_add_logo:
                    processed = add_logo(processed)
                if test_mode:
                    try:
                        from core.image_generator import create_social_card
                        if processed and os.path.exists(processed):
                            card_path = processed.replace(".jpg", "_card.jpg")
                            post_text = article.get("post_text_for_card", "Başlık yok")
                            create_social_card(post_text=post_text, image_path=processed, output_path=card_path)
                            if os.path.exists(card_path):
                                _safe_unlink(processed)
                                processed = card_path
                                log("Test Modu: Sosyal medya kartı başarıyla oluşturuldu (relaxed).", "INFO")
                    except Exception as exc:
                        log(f"Kart oluşturma adımı atlandı (relaxed): {exc}", "WARNING")
                score, score_detail = _score_image_quality(width=width, height=height, size_kb=size_kb, source_type=source_type, target_ratio=target_ratio)
                score = max(0.0, score - 7.0)
                accepted.append({"path": processed, "url": candidate_url, "source_type": source_type, "score": score, "score_detail": f"{score_detail}, relaxed_penalty=7.0", "phash": current_phash, "signature": current_signature, "content_hash": content_hash})
                seen_content_hashes.add(content_hash)
                if current_phash is not None:
                    seen_perceptual_records.append((current_phash, current_signature))
                log(f"Relaxed aday basarili: {reason} -> quality={score:.1f}")
            except Exception as exc:
                fail_reasons[f"relaxed_processing_error:{exc}"] += 1
                _safe_unlink(downloaded)

    if accepted:
        accepted_sorted = sorted(accepted, key=lambda x: x.get("score", 0.0), reverse=True)
        selected = accepted_sorted[:max_images_per_news]
        discarded = accepted_sorted[max_images_per_news:]
        for item in selected:
            prepared_paths.append(item["path"])
            used_sources.append(item.get("source_type", "unknown"))
            log(f"Secilen gorsel: score={item.get('score', 0.0):.1f} source={item.get('source_type', 'unknown')} url={item.get('url', '')[:110]}")
        for item in discarded:
            path = item.get("path", "")
            if path and os.path.exists(path):
                _safe_unlink(path)

    if not prepared_paths:
        log("Haberin kendi gorseli bulunamadi. DuckDuckGo görsel araması başlatılıyor...", "INFO")
        ddg_urls = get_duckduckgo_image_candidates(article.get("title", ""))
        for ddg_url in ddg_urls:
            if len(prepared_paths) >= max_images_per_news: break
            log(f"DuckDuckGo adayı deneniyor: {ddg_url[:100]}")
            downloaded, reason = _download_image_with_reason(ddg_url, limits)
            if downloaded:
                try:
                    processed = downloaded
                    needs_resize, resize_reason = _should_resize_for_platform(downloaded, resize_limits)
                    if needs_resize:
                        log(f"DDG gorsel resize: {resize_reason}")
                        processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
                    else:
                        log(f"DDG gorsel resize atlandi: {resize_reason}")
                    if should_add_logo:
                        processed = add_logo(processed)
                    if test_mode:
                        try:
                            from core.image_generator import create_social_card
                            if processed and os.path.exists(processed):
                                card_path = processed.replace(".jpg", "_card.jpg")
                                post_text = article.get("post_text_for_card", "Başlık yok")
                                create_social_card(post_text=post_text, image_path=processed, output_path=card_path)
                                if os.path.exists(card_path):
                                    _safe_unlink(processed)
                                    processed = card_path
                                    log("Test Modu: Sosyal medya kartı başarıyla oluşturuldu (DDG).", "INFO")
                        except Exception as exc:
                            log(f"Kart oluşturma adımı atlandı (DDG): {exc}", "WARNING")
                    prepared_paths.append(processed)
                    used_sources.append("duckduckgo")
                    article["image_source"] = "duckduckgo"
                    log("DuckDuckGo gorsel basarili! Gorsel hazirlandi.")
                    break 
                except Exception as exc:
                    log(f"DuckDuckGo gorsel isleme hatasi: {exc}", "WARNING")
                    _safe_unlink(downloaded)
            else:
                log(f"DuckDuckGo adayi elendi: {reason}", "WARNING")

    if not prepared_paths:
        ai_url = _ai_search_image_url(article)
        if ai_url:
            log(f"AI gorsel arama: URL bulundu, deneniyor: {ai_url[:80]}...")
            downloaded, reason = _download_image_with_reason(ai_url, limits)
            if downloaded:
                try:
                    processed = downloaded
                    needs_resize, resize_reason = _should_resize_for_platform(downloaded, resize_limits)
                    if needs_resize:
                        log(f"AI gorsel resize: {resize_reason}")
                        processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
                    else:
                        log(f"AI gorsel resize atlandi: {resize_reason}")
                    if should_add_logo:
                        processed = add_logo(processed)
                    if test_mode:
                        try:
                            from core.image_generator import create_social_card
                            if processed and os.path.exists(processed):
                                card_path = processed.replace(".jpg", "_card.jpg")
                                post_text = article.get("post_text_for_card", "Başlık yok")
                                create_social_card(post_text=post_text, image_path=processed, output_path=card_path)
                                if os.path.exists(card_path):
                                    _safe_unlink(processed)
                                    processed = card_path
                                    log("Test Modu: Sosyal medya kartı başarıyla oluşturuldu (AI).", "INFO")
                        except Exception as exc:
                            log(f"Kart oluşturma adımı atlandı (AI): {exc}", "WARNING")
                    prepared_paths.append(processed)
                    used_sources.append("ai_search")
                    article["image_source"] = "ai_search"
                    log(f"AI gorsel basarili! Gorsel hazirlandi.")
                except Exception as exc:
                    log(f"AI gorsel isleme hatasi: {exc}", "WARNING")
                    _safe_unlink(downloaded)
            else:
                log(f"AI gorsel indirilemedi: {reason}", "WARNING")

    if not prepared_paths:
        log("GORSEL YOK: Bu haber icin hicbir gorsel bulunamadi. Text-only paylasim yapilacak.", "WARNING")
        article["image_source"] = "no_image"
        article["image_sources"] = ["no_image"]
        article["prepared_image_count"] = 0
        article["original_image_urls"] = []
        article["image_candidates"] = []
        article["image_url"] = ""
        article["rss_image_url"] = ""
        log(f"Gorsel hazirlama bitti. Adet=0 kaynak=no_image (text-only paylasim)")
        log("-" * 40)
        return []  

    article["image_source"] = used_sources[0] if used_sources else "unknown"
    article["image_sources"] = used_sources
    article["prepared_image_count"] = len(prepared_paths)

    original_urls = []
    if accepted:
        accepted_sorted_for_urls = sorted(accepted, key=lambda x: x.get("score", 0.0), reverse=True)
        for item in accepted_sorted_for_urls[:max_images_per_news]:
            url = item.get("url", "")
            if url and url.startswith("http"):
                original_urls.append(url)
    article["original_image_urls"] = original_urls
    if original_urls:
        log(f"Orijinal URL'ler kaydedildi: {len(original_urls)} adet")

    if fail_reasons:
        fail_summary = ", ".join([f"{k}={v}" for k, v in fail_reasons.items()])
        log(f"Gorsel deneme ozeti: tried={tried_count}, success={len(prepared_paths)}, fails=({fail_summary})")
    else:
        log(f"Gorsel deneme ozeti: tried={tried_count}, success={len(prepared_paths)}, fails=(yok)")

    log(f"Gorsel hazirlama bitti. Adet={len(prepared_paths)} kaynak={article.get('image_source')}")
    log("-" * 40)
    return prepared_paths

def prepare_image(article: dict) -> str:
    paths = prepare_images(article)
    return paths[0]

def run() -> bool:
    log("-" * 55)
    log("agent_image basliyor")
    log("-" * 55)
    write_stage = get_stage("write")
    if write_stage.get("status") != "done":
        log("write asamasi tamamlanmamis, image calistirilamaz", "ERROR")
        set_stage("image", "error", error="write asamasi tamamlanmamis")
        return False
    write_output = write_stage.get("output", {})
    article = write_output.get("article", {})
    post_text = write_output.get("post_text", "")
    if not article:
        log("Write ciktisinda haber yok", "WARNING")
        set_stage("image", "error", error="Write ciktisinda haber yok")
        return False
    article["post_text_for_card"] = post_text
    set_stage("image", "running")
    try:
        image_paths = prepare_images(article)
        first_image_path = image_paths[0] if image_paths else ""
        output = {
            "article": article,
            "post_text": post_text,
            "image_path": first_image_path,
            "image_paths": image_paths,
            "image_source": article.get("image_source", "unknown"),
            "image_count": len(image_paths),
        }
        set_stage("image", "done", output=output)
        log(f"agent_image tamamlandi -> kaynak={article.get('image_source', '?')} adet={len(image_paths)}")
        return True
    except Exception as exc:
        log(f"agent_image kritik hata: {exc}", "ERROR")
        set_stage("image", "error", error=str(exc))
        return False
