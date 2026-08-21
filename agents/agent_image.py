"""
agents/agent_image.py - Görsel İşleme Ajanı Ana Köprüsü (v9.1 - Güvenli Yedek Görsel)
1809 satırlık devasa dosya 6 modüle bölündü:
  agent_image (köprü), image_utils (URL/Kontrol), image_nitter (Nitter),
  image_processor (PIL/Logo), image_scraper (HTML Parse), image_search (DDG/AI)

v9.1 UPDATE:
  - Yedek görsel araması artık FAIL-CLOSED: yalnızca Gemini VISION onayı
    (verdict=True) ile görsel kabul edilir; Vision kullanılamıyorsa görsel
    reddedilir ve text-only paylaşım yapılır (alakasız fotoğraf yayınlanmaz).
  - Yapısal kaynaklar önceliklendirildi: Wikipedia -> Commons -> CarAPI ->
    DDG (URL sinyaline göre sıralı). Sorgular 'marka model yıl' formatında.
  - Eski fail-open davranış istenirse: settings images.fallback_require_vision=false
    veya FALLBACK_REQUIRE_VISION=false.
"""
import os
from collections import Counter
from typing import Optional

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
from agents.image_search import (
    get_duckduckgo_image_candidates,
    _ai_search_image_url,
    build_image_search_queries,
    verify_image_relevance,
    get_wikipedia_image_candidates,
    get_commons_image_candidates,
    get_carapi_candidate,
    _url_signal_score,
    vision_gate_passed,
)

_DEFAULT_PERCEPTUAL_HASH_THRESHOLD = 6

# v9.1: Yedek arama görselleri için ortak limitler (tek noktadan yönetim)
_MAX_SEARCH_QUERIES = 3            # En fazla kaç arama sorgusu denenecek
_MAX_STRUCTURED_CANDIDATES = 8     # Wikipedia/Commons/CarAPI toplam aday üst sınırı
_MAX_DDG_CANDIDATES_PER_QUERY = 10 # Sorgu başına DDG aday sayısı
_MAX_TOTAL_SEARCH_CANDIDATES = 20  # Toplamda denenecek aday URL sayısı
_MAX_VISION_CHECKS = 12            # Vision doğrulama çağrısı üst sınırı


def _finalize_search_image(
    downloaded: str,
    feed_image_width: int,
    feed_image_height: int,
    resize_limits: dict,
    should_add_logo: bool,
    test_mode: bool,
    article: dict,
    label: str,
) -> Optional[str]:
    """Yedek aramadan inen görseli işler: resize + logo + (test modunda kart).

    v9.0: Eskiden 3 ayrı fallback bloğunda kopyalanmış ortak işleme adımı.
    Hata durumunda indirilen dosya temizlenir ve None döner.
    """
    try:
        processed = downloaded
        needs_resize, resize_reason = _should_resize_for_platform(downloaded, resize_limits)
        if needs_resize:
            log(f"{label} gorsel resize: {resize_reason}")
            processed = resize_and_crop(downloaded, feed_image_width, feed_image_height)
        else:
            log(f"{label} gorsel resize atlandi: {resize_reason}")

        if should_add_logo:
            processed = add_logo(processed)

        if test_mode:
            try:
                from core.image_generator import create_social_card
                if processed and os.path.exists(processed):
                    card_path = processed.replace(".jpg", "_card.jpg")
                    post_text = article.get("post_text_for_card", "Başlık yok")
                    create_social_card(
                        post_text=post_text,
                        image_path=processed,
                        output_path=card_path,
                        title_override=article.get("story_card_title") or None,
                        body_override=article.get("story_card_subtitle") or None,
                    )
                    if os.path.exists(card_path):
                        _safe_unlink(processed)
                        processed = card_path
                        log(f"Test Modu: Sosyal medya kartı başarıyla oluşturuldu ({label}).", "INFO")
            except Exception as exc:
                log(f"Kart oluşturma adımı atlandı ({label}): {exc}", "WARNING")

        return processed
    except Exception as exc:
        log(f"{label} gorsel isleme hatasi: {exc}", "WARNING")
        _safe_unlink(downloaded)
        return None


def _smart_search_fallback(
    article: dict,
    prepared_paths: list,
    used_sources: list,
    limits: dict,
    resize_limits: dict,
    feed_image_width: int,
    feed_image_height: int,
    should_add_logo: bool,
    test_mode: bool,
    require_vision: bool = True,
) -> None:
    """v9.1 AKILLI YEDEK GÖRSEL ARAMASI (fail-closed).

    Eski sistem haber başlığının ilk kelimelerini aratıp İLK inen görseli
    kabul ediyordu; sonuç: alakasız fotoğraflarla paylaşım.

    v9.1 akışı:
      1. AI'dan 'marka model yıl' odaklı HASSAS arama sorguları üretilir
         (AI yoksa deterministik marka/model/yıl çıkarımı).
      2. YAPISAL KAYNAKLAR önce taranır: Wikipedia lead image -> Wikimedia
         Commons (dosya + kategori) -> CarAPI (marka+model). Bu kaynaklardaki
         görseller etiketli ve modelle eşleşiktir.
      3. DDG adayları toplanır ve URL/filename içinde marka+model tokeni
         taşıyanlar öne alınır (sinyal sıralaması).
      4. Her aday indirildikten sonra Gemini VISION ile doğrulanır.
      5. VISION KAPISI (fail-closed): require_vision=True iken yalnızca
         verdict=True olan görseller kabul edilir. Vision kullanılamıyorsa
         (None) görsel REDDEDİLİR -> text-only paylaşım. Bu, alakasız
         fotoğraf yayınlamanın önüne geçer.
         require_vision=False ise eski fail-open davranış (yalnızca False red).

    Hiçbir aday kabul edilmezse prepared_paths boş kalır; publisher text-only
    paylaşım yapar (alakasız fotoğraftan iyidir).
    """
    log("Haberin kendi gorseli bulunamadi. Akilli yedek gorsel aramasi baslatiliyor...", "INFO")
    log(f"Yedek gorsel vizyon kapisi: require_vision={require_vision} "
        f"({'fail-closed' if require_vision else 'fail-open'})", "INFO")

    search_queries = build_image_search_queries(article)[:_MAX_SEARCH_QUERIES]
    if not search_queries:
        log("Akilli arama: sorgu uretilemedi", "WARNING")
        return

    log(f"Akilli arama sorgulari ({len(search_queries)}): {search_queries}")

    # 1) YAPISAL KAYNAKLAR (Wikipedia -> Commons -> CarAPI)
    structured_candidates: list = []
    try:
        structured_candidates += get_wikipedia_image_candidates(
            search_queries, max_candidates=4,
        )
        structured_candidates += get_commons_image_candidates(
            search_queries, max_candidates=6,
        )
        carapi = get_carapi_candidate(article.get("title", ""))
        if carapi:
            structured_candidates.insert(0, carapi)  # marka+model birebir eşleşme
    except Exception as exc:
        log(f"Yapisal gorsel kaynak hatasi: {exc}", "WARNING")
    structured_candidates = structured_candidates[:_MAX_STRUCTURED_CANDIDATES]
    if structured_candidates:
        log(f"Yapisal kaynaklardan {len(structured_candidates)} aday toplandi")

    # 2) DDG adayları (URL sinyaline göre sıralı)
    seen_urls = {c.get("url", "") for c in structured_candidates if c.get("url")}
    ddg_candidates: list = []
    for query in search_queries:
        for ddg_url in get_duckduckgo_image_candidates(query, max_results=_MAX_DDG_CANDIDATES_PER_QUERY):
            if ddg_url and ddg_url not in seen_urls:
                seen_urls.add(ddg_url)
                ddg_candidates.append(ddg_url)
        if len(ddg_candidates) + len(structured_candidates) >= _MAX_TOTAL_SEARCH_CANDIDATES:
            break

    # Sinyal sıralaması: URL/filename'de marka+model tokeni olanlar önce
    ddg_candidates = sorted(
        ddg_candidates,
        key=lambda u: _url_signal_score(u, search_queries),
        reverse=True,
    )
    ddg_quota = max(0, _MAX_TOTAL_SEARCH_CANDIDATES - len(structured_candidates))
    ddg_candidates = ddg_candidates[:ddg_quota]

    candidate_urls = [c.get("url") for c in structured_candidates] + ddg_candidates
    candidate_urls = [u for u in candidate_urls if u]
    if not candidate_urls:
        log("Akilli arama: hic aday URL bulunamadi", "WARNING")
        return

    log(f"Akilli arama: {len(candidate_urls)} aday URL toplandi "
        f"(yapisal={len(structured_candidates)}, ddg={len(ddg_candidates)})")

    vision_checks = 0
    rejected = 0
    for idx, cand_url in enumerate(candidate_urls, start=1):
        if len(prepared_paths) >= 1:  # Yedek aramadan tek görsel yeter
            break

        log(f"Yedek arama adayi ({idx}/{len(candidate_urls)}): {cand_url[:100]}")
        downloaded, reason = _download_image_with_reason(cand_url, limits)
        if not downloaded:
            log(f"Yedek arama adayi elendi: {reason}", "WARNING")
            continue

        verdict = None
        if vision_checks < _MAX_VISION_CHECKS:
            vision_checks += 1
            verdict = verify_image_relevance(downloaded, article)

        if not vision_gate_passed(verdict, require_vision):
            rejected += 1
            if verdict is False:
                log("Yedek arama adayi VISION tarafindan REDDEDILDI (alakasiz gorsel)", "WARNING")
            else:
                log("Yedek arama adayi vision dogrulanamadigi icin REDDEDILDI (fail-closed)", "WARNING")
            _safe_unlink(downloaded)
            continue

        processed = _finalize_search_image(
            downloaded, feed_image_width, feed_image_height, resize_limits,
            should_add_logo, test_mode, article, "smart_search",
        )
        if processed:
            prepared_paths.append(processed)
            used_sources.append("smart_search")
            article["image_source"] = "smart_search"
            log("Yedek arama gorseli VISION onayiyla kabul edildi!")
            return

    log(f"Akilli arama: hicbir aday kabul edilmedi (denenen={len(candidate_urls)}, red={rejected})", "WARNING")


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

    # v9.1: Yedek görsel kabulü için VISION kapısı (fail-closed).
    # settings: images.fallback_require_vision (varsayılan True)
    # env: FALLBACK_REQUIRE_VISION=false ile eski fail-open davranış.
    fallback_require_vision = _read_bool_env("FALLBACK_REQUIRE_VISION")
    if fallback_require_vision is None:
        fallback_require_vision = bool(images_settings.get("fallback_require_vision", True))

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
                        create_social_card(
                            post_text=post_text,
                            image_path=processed,
                            output_path=card_path,
                            title_override=article.get("story_card_title") or None,
                            body_override=article.get("story_card_subtitle") or None,
                        )
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
                            create_social_card(
                            post_text=post_text,
                            image_path=processed,
                            output_path=card_path,
                            title_override=article.get("story_card_title") or None,
                            body_override=article.get("story_card_subtitle") or None,
                        )
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

    # ── FALLBACK (v9.1): AKILLI YEDEK GÖRSEL ARAMASI ─────────────────────────
    # 1) Sorgular: AI / deterministik 'marka model yıl'
    # 2) Kaynak sırası: Wikipedia -> Commons -> CarAPI -> DDG (sinyal sıralı)
    # 3) Her aday VISION ile doğrulanır; require_vision=True iken yalnızca
    #    onaylananlar kabul edilir (fail-closed, alakasız fotoğraf yayınlanmaz)
    if not prepared_paths:
        _smart_search_fallback(
            article,
            prepared_paths,
            used_sources,
            limits,
            resize_limits,
            feed_image_width,
            feed_image_height,
            should_add_logo,
            test_mode,
            require_vision=fallback_require_vision,
        )

    # FALLBACK 2: AI URL araması (son çare; aynı VISION kapısından geçer)
    if not prepared_paths:
        log("Akilli arama sonuc vermedi. AI URL gorsel aramasi deneniyor...", "INFO")
        ai_url = _ai_search_image_url(article)
        if ai_url:
            log(f"AI gorsel arama: URL bulundu, deneniyor: {ai_url[:80]}...")
            downloaded, reason = _download_image_with_reason(ai_url, limits)
            if downloaded:
                verdict = verify_image_relevance(downloaded, article)
                if not vision_gate_passed(verdict, fallback_require_vision):
                    if verdict is False:
                        log("AI URL gorseli VISION tarafindan REDDEDILDI (alakasiz gorsel)", "WARNING")
                    else:
                        log("AI URL gorseli vision dogrulanamadigi icin REDDEDILDI (fail-closed)", "WARNING")
                    _safe_unlink(downloaded)
                else:
                    processed = _finalize_search_image(
                        downloaded, feed_image_width, feed_image_height, resize_limits,
                        should_add_logo, test_mode, article, "ai_search",
                    )
                    if processed:
                        prepared_paths.append(processed)
                        used_sources.append("ai_search")
                        article["image_source"] = "ai_search"
                        log("AI gorsel basarili! Gorsel hazirlandi.")
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
    # v5.5: Story card özel başlık/alt metin (writer aşamasında üretildi).
    story_card_title = write_output.get("story_card_title", "")
    story_card_subtitle = write_output.get("story_card_subtitle", "")
    article["story_card_title"] = story_card_title
    article["story_card_subtitle"] = story_card_subtitle
    set_stage("image", "running")
    try:
        image_paths = prepare_images(article)
        first_image_path = image_paths[0] if image_paths else ""
        output = {
            "article": article,
            "post_text": post_text,
            "story_card_title": story_card_title,
            "story_card_subtitle": story_card_subtitle,
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
