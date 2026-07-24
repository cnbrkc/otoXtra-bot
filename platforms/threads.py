"""
platforms/threads.py - Threads Ana Köprü Dosyası (v5.1 - DRY Refactoring)

v5.1 GÜNCELLEME:
  - Dosya 3 modüle bölündü: threads.py (köprü), threads_api.py (API), threads_uploader.py (Görsel URL)
  - Tüm diğer agent'lar (örn: agent_publisher) eski çağrı şekilleriyle kullanmaya devam edebilir.
"""
import os
from core.logger import log
from platforms.threads_api import post_text, post_image
from platforms.threads_uploader import _extract_original_urls, _resolve_public_url
from core.image_uploader import get_public_url_fallback

# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API - FALLBACK ZİNCİRİ (ANA FONKSİYON)
# ═══════════════════════════════════════════════════════════════════════════════

def post_with_image(message: str, image_path: str, article: dict = None) -> str | None:
    """
    Threads gorsel paylasimi - TAM FALLBACK ZINCIRI.
    """
    log("=" * 50)
    log("Threads GORSEL PAYLASIM: Fallback zinciri basliyor...")
    log("=" * 50)

    # ADIM 1: Orijinal URL'leri dene
    if article:
        original_urls = _extract_original_urls(article)
        if original_urls:
            log(f"Threads ADIM 1: {len(original_urls)} orijinal URL denenecek...")
            for idx, url in enumerate(original_urls, 1):
                log(f"  Orijinal URL {idx}/{len(original_urls)}: {url[:80]}...")
                result = post_image(message, url, auto_fallback=False)
                if result:
                    log(f"  Orijinal URL basarili! (deneme {idx})")
                    return result
                log(f"  Orijinal URL basarisiz (deneme {idx})", "WARNING")
        else:
            log("Threads ADIM 1: Article'da orijinal URL yok, atlaniyor")
    else:
        log("Threads ADIM 1: Article dict yok, orijinal URL atlaniyor")

    # ADIM 2-5: Upload servisleri
    if image_path and os.path.exists(image_path):
        upload_services = [
            ("Catbox.moe", get_public_url_fallback),
        ]

        for step_num, (service_name, upload_fn) in enumerate(upload_services, start=2):
            log(f"Threads ADIM {step_num}: {service_name} deneniyor...")
            public_url = upload_fn(image_path)

            if not public_url:
                log(f"  {service_name} upload basarisiz", "WARNING")
                continue

            log(f"  Upload basarili! URL: {public_url[:60]}...")
            result = post_image(message, public_url, auto_fallback=False)

            if result:
                log(f"  {service_name} ile gorsel paylasim basarili!")
                return result

            log(f"  {service_name} URL ile Threads paylasim basarisiz", "WARNING")
    else:
        if image_path:
            log(f"Threads: Gorsel dosyasi bulunamadi: {image_path}", "WARNING")
        else:
            log("Threads: Gorsel dosya yolu bos", "WARNING")

    # ADIM 6: Metin fallback
    log("Threads ADIM 6: Tum gorsel yontemleri basarisiz, metin-only fallback!", "WARNING")
    return post_text(message)


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API - CAROUSEL
# ═══════════════════════════════════════════════════════════════════════════════

def post_local_image(message: str, image_path: str, article: dict = None) -> str | None:
    """
    post_with_image() icin backward-compatible alias.
    """
    return post_with_image(message, image_path, article=article)


def post_carousel(message: str, image_paths: list[str], article: dict = None) -> str | None:
    """
    Threads'a carousel (coklu gorsel) paylasim yapar.
    """
    if not image_paths or len(image_paths) < 2:
        log("post_carousel: En az 2 gorsel gerekli", "WARNING")
        if len(image_paths) == 1:
            return post_with_image(message, image_paths[0], article=article)
        return post_text(message)

    image_paths = image_paths[:10]

    from platforms.threads_api import _get_credentials, _get_threads_user_id, _post_with_retry, _publish_container, _truncate_for_threads
    
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None

    threads_user_id = _get_threads_user_id(ig_user_id, token)
    if not threads_user_id:
        return None

    _BASE_URL = f"https://graph.threads.net/v1.0"

    item_ids = []
    for idx, img_path in enumerate(image_paths):
        if not os.path.exists(img_path):
            log(f"post_carousel: Gorsel bulunamadi, atlaniyor: {img_path}", "WARNING")
            continue

        public_url = _resolve_public_url(img_path, article=article, image_index=idx)
        if not public_url:
            log(f"post_carousel: Gorsel {idx + 1} icin URL elde edilemedi, atlaniyor", "WARNING")
            continue

        container_url = f"{_BASE_URL}/{threads_user_id}/threads"
        container_data = {
            "media_type": "IMAGE",
            "image_url": public_url,
            "is_carousel_item": "true",
            "access_token": token,
        }
        headers = {"Authorization": f"Bearer {token}"}

        log(f"Threads carousel item {idx + 1}/{len(image_paths)} container olusturuluyor...")
        resp = _post_with_retry(container_url, container_data, f"carousel_item_{idx + 1}", headers=headers)
        item_id = resp.get("id")

        if item_id:
            item_ids.append(item_id)
            log(f"Carousel item {idx + 1} basarili: ID={item_id}")
        else:
            log(f"Carousel item {idx + 1} basarisiz, atlaniyor", "WARNING")

    if len(item_ids) < 2:
        log(f"post_carousel: Yeterli item olusturulamadi ({len(item_ids)}/2)", "WARNING")
        if item_ids and image_paths:
            return post_with_image(message, image_paths[0], article=article)
        return post_text(message)

    truncated_message = _truncate_for_threads(message)

    carousel_url = f"{_BASE_URL}/{threads_user_id}/threads"
    carousel_data = {
        "media_type": "CAROUSEL",
        "children": ",".join(item_ids),
        "text": truncated_message,
        "access_token": token,
    }
    headers = {"Authorization": f"Bearer {token}"}

    log(f"Threads CAROUSEL container olusturuluyor ({len(item_ids)} gorsel)...")
    carousel_resp = _post_with_retry(carousel_url, carousel_data, "create_carousel_container", headers=headers)
    carousel_id = carousel_resp.get("id")

    if not carousel_id:
        log("Threads carousel container olusturulamadi, metin fallback", "ERROR")
        return post_text(message)

    return _publish_container(threads_user_id, carousel_id, token, "carousel")
