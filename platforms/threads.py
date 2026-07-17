"""
platforms/threads.py - Threads API katmani (v5.0 - Coklu Gorsel Fallback)
v5.0:
  - YENI: post_with_image() - Tam fallback zinciri ile gorsel paylasim
    1. Orijinal URL (article'dan, zaten indirdigimiz gorselin kaynagi)
    2. Catbox.moe upload (ucretsiz, API key gerektirmez)
    3. 0x0.st upload (ucretsiz, API key gerektirmez)
    4. Telegraph upload (ucretsiz, API key gerektirmez)
    5. ImgBB upload (ucretsiz tier, IMGBB_API_KEY opsiyonel)
    6. Metin-only fallback
  - YENI: _extract_original_urls() - Article'dan orijinal gorsel URL'leri cikarir
  - YENI: _upload_catbox() - Catbox.moe ucretsiz yukleme
  - YENI: _upload_0x0() - 0x0.st ucretsiz yukleme
  - YENI: _upload_telegraph() - Telegraph ucretsiz yukleme
  - YENI: _truncate_for_threads() - 500 karakter limiti
  - MEVCUT: post_text(), post_image() korundu (backward compatible)
v3.5:
  - "Cannot parse access token" (190) hatasi cozuldu (graph.threads.net'e gecildi).
"""
import base64
import json
import os
import time
import requests
from core.logger import log
# ── Threads API ──────────────────────────────────────────────────────────────
_THREADS_API_VERSION = "v1.0"
_BASE_URL = f"https://graph.threads.net/{_THREADS_API_VERSION}"
_REQUEST_TIMEOUT = 60
_RETRY_ATTEMPTS = 3
_RETRY_BASE_WAIT = 2.0
# ── Threads limitleri ────────────────────────────────────────────────────────
_THREADS_MAX_TEXT_LENGTH = 500
# ── Upload servis limitleri ──────────────────────────────────────────────────
_IMGBB_API_URL = "https://api.imgbb.com/1/upload"
_IMGBB_MAX_FILE_SIZE = 32 * 1024 * 1024
_CATBOX_API_URL = "https://catbox.moe/user/api.php"
_CATBOX_MAX_FILE_SIZE = 200 * 1024 * 1024
_ZER0X_API_URL = "https://0x0.st"
_ZER0X_MAX_FILE_SIZE = 512 * 1024 * 1024
_TELEGRAPH_API_URL = "https://telegra.ph/upload"
_TELEGRAPH_MAX_FILE_SIZE = 5 * 1024 * 1024
_UPLOAD_USER_AGENT = "otoXtraBot/5.0"
# ═══════════════════════════════════════════════════════════════════════════════
# CREDENTIALS & HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def _get_credentials():
    user_id = os.environ.get("THREADS_USER_ID", "").strip()
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    # TOKSIK TEMIZLEYICI: Gizli satir sonu, bosluk veya tirnak isaretlerini yok eder.
    token = (
        token.replace('"', "").replace("'", "")
        .replace("\n", "").replace("\r", "").replace(" ", "")
        .strip()
    )
    if not user_id:
        log("THREADS_USER_ID env bulunamadi", "ERROR")
    else:
        log(f"THREADS_USER_ID okundu: uzunluk={len(user_id)}")
    if not token:
        log("THREADS_ACCESS_TOKEN env bulunamadi", "ERROR")
    else:
        log(f"THREADS_ACCESS_TOKEN okundu: uzunluk={len(token)}, ilk_4={token[:4]}")
    return user_id, token
def _truncate_for_threads(text: str, max_length: int = _THREADS_MAX_TEXT_LENGTH) -> str:
    """
    Threads'in 500 karakter limitine uygun sekilde metni keser.
    Son kelimeyi tam kesmez, onceki bosluktan keser.
    """
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    truncated = text[: max_length - 3]
    last_space = truncated.rfind(" ")
    if last_space > max_length * 0.6:
        truncated = truncated[:last_space]
    return truncated + "..."
# ═══════════════════════════════════════════════════════════════════════════════
# HTTP RETRY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════
def _post_with_retry(url, data, context="threads", headers=None):
    last_error = ""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            started = time.time()
            resp = requests.post(url, data=data, headers=headers, timeout=_REQUEST_TIMEOUT)
            elapsed = int((time.time() - started) * 1000)
            try:
                result = resp.json()
            except Exception:
                result = {"error": {"message": resp.text, "code": resp.status_code}}
            log(
                f"{context} attempt={attempt}/{_RETRY_ATTEMPTS} "
                f"status={resp.status_code} elapsed_ms={elapsed}",
                "INFO",
            )
            if resp.status_code == 200 and "id" in result:
                return result
            if "error" in result:
                err = result["error"]
                msg = err.get("error_user_msg") or err.get("message", "")
                log(f"Threads API hatasi: {err.get('code')} - {msg}", "ERROR")
                if "temporarily" in msg.lower() or err.get("code") in (4, 17, 32, 80000, 80001, 80002):
                    last_error = f"retryable: {msg}"
                else:
                    return result
            else:
                last_error = f"http {resp.status_code}"
        except Exception as e:
            last_error = str(e)
            log(f"{context} request error: {e}", "WARNING")
        if attempt < _RETRY_ATTEMPTS:
            wait = _RETRY_BASE_WAIT * (2 ** (attempt - 1))
            log(f"{context} retry wait {wait:.1f}s", "INFO")
            time.sleep(wait)
    log(f"{context} all retries failed: {last_error}", "ERROR")
    return {}
def _get_with_retry(url, params, context="threads_get", headers=None):
    last_error = ""
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            started = time.time()
            resp = requests.get(url, params=params, headers=headers, timeout=_REQUEST_TIMEOUT)
            elapsed = int((time.time() - started) * 1000)
            try:
                result = resp.json()
            except Exception:
                result = {"error": {"message": resp.text, "code": resp.status_code}}
            log(
                f"{context} attempt={attempt}/{_RETRY_ATTEMPTS} "
                f"status={resp.status_code} elapsed_ms={elapsed}",
                "INFO",
            )
            if resp.status_code == 200 and "id" in result:
                return result
            if "error" in result:
                err = result["error"]
                msg = err.get("error_user_msg") or err.get("message", "")
                log(f"Threads API GET hatasi: {err.get('code')} - {msg}", "ERROR")
                return result
            else:
                last_error = f"http {resp.status_code}"
        except Exception as e:
            last_error = str(e)
            log(f"{context} request error: {e}", "WARNING")
        if attempt < _RETRY_ATTEMPTS:
            wait = _RETRY_BASE_WAIT * (2 ** (attempt - 1))
            log(f"{context} retry wait {wait:.1f}s", "INFO")
            time.sleep(wait)
    log(f"{context} all retries failed: {last_error}", "ERROR")
    return {}
# ═══════════════════════════════════════════════════════════════════════════════
# THREADS USER ID & PUBLISH
# ═══════════════════════════════════════════════════════════════════════════════
def _get_threads_user_id(ig_user_id, token):
    """Threads User ID'sini bulur."""
    url = f"{_BASE_URL}/me"
    params = {"fields": "id,username", "access_token": token}
    headers = {"Authorization": f"Bearer {token}"}
    log("Threads User ID '/me' uzerinden kontrol ediliyor...")
    result = _get_with_retry(url, params, "get_me_profile", headers=headers)
    me_id = result.get("id")
    if me_id:
        log(f"Threads User ID '/me' basarili: {me_id} (username: {result.get('username', 'N/A')})")
        return me_id
    if ig_user_id:
        log(f"/me basarisiz. Env THREADS_USER_ID ({ig_user_id}) kullanilacak.", "WARNING")
        return ig_user_id
    log("Threads User ID hicbir sekilde bulunamadi.", "ERROR")
    return None
def _publish_container(threads_user_id, container_id, token, media_type):
    """Container'i publish eder."""
    publish_url = f"{_BASE_URL}/{threads_user_id}/threads_publish"
    publish_data = {"creation_id": container_id, "access_token": token}
    headers = {"Authorization": f"Bearer {token}"}
    log(f"Threads {media_type} publish ediliyor...")
    publish_resp = _post_with_retry(publish_url, publish_data, f"publish_{media_type}", headers=headers)
    publish_id = publish_resp.get("id")
    if publish_id:
        log(f"Threads {media_type} basariyla publish edildi! ID={publish_id}")
    else:
        log(f"Threads {media_type} publish basarisiz", "ERROR")
    return publish_id
# ═══════════════════════════════════════════════════════════════════════════════
# UPLOAD SERVISLERI (Hepsi Ucretsiz)
# ═══════════════════════════════════════════════════════════════════════════════
def _upload_catbox(image_path: str) -> str | None:
    """
    Catbox.moe'a gorsel yukler. (Ucretsiz, API key gerektirmez, kalici depolama)
    Dosya limiti: 200MB
    """
    if not image_path or not os.path.exists(image_path):
        log("Catbox: Dosya bulunamadi", "ERROR")
        return None
    try:
        file_size = os.path.getsize(image_path)
        if file_size > _CATBOX_MAX_FILE_SIZE:
            log(f"Catbox: Dosya cok buyuk: {file_size // 1024}KB", "ERROR")
            return None
        log(f"Catbox: Yukleniyor... ({file_size // 1024}KB)")
        with open(image_path, "rb") as f:
            resp = requests.post(
                _CATBOX_API_URL,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": f},
                headers={"User-Agent": _UPLOAD_USER_AGENT},
                timeout=30,
            )
        if resp.status_code == 200 and resp.text.strip().startswith("https://"):
            url = resp.text.strip()
            log(f"Catbox: Basarili! URL uzunlugu={len(url)}")
            return url
        log(f"Catbox: Basarisiz status={resp.status_code} body={resp.text[:200]}", "WARNING")
        return None
    except requests.exceptions.Timeout:
        log("Catbox: Zaman asimi", "WARNING")
        return None
    except Exception as exc:
        log(f"Catbox: Hata: {exc}", "WARNING")
        return None
def _upload_0x0(image_path: str) -> str | None:
    """
    0x0.st'ye gorsel yukler. (Ucretsiz, API key gerektirmez, kalici depolama)
    Dosya limiti: 512MB
    """
    if not image_path or not os.path.exists(image_path):
        log("0x0.st: Dosya bulunamadi", "ERROR")
        return None
    try:
        file_size = os.path.getsize(image_path)
        if file_size > _ZER0X_MAX_FILE_SIZE:
            log(f"0x0.st: Dosya cok buyuk: {file_size // 1024}KB", "ERROR")
            return None
        log(f"0x0.st: Yukleniyor... ({file_size // 1024}KB)")
        with open(image_path, "rb") as f:
            resp = requests.post(
                _ZER0X_API_URL,
                files={"file": f},
                headers={"User-Agent": _UPLOAD_USER_AGENT},
                timeout=30,
            )
        if resp.status_code == 200 and resp.text.strip().startswith("https://"):
            url = resp.text.strip()
            log(f"0x0.st: Basarili! URL uzunlugu={len(url)}")
            return url
        log(f"0x0.st: Basarisiz status={resp.status_code} body={resp.text[:200]}", "WARNING")
        return None
    except requests.exceptions.Timeout:
        log("0x0.st: Zaman asimi", "WARNING")
        return None
    except Exception as exc:
        log(f"0x0.st: Hata: {exc}", "WARNING")
        return None
def _upload_telegraph(image_path: str) -> str | None:
    """
    Telegraph'a gorsel yukler. (Ucretsiz, API key gerektirmez)
    Dosya limiti: 5MB, format: JPEG/PNG
    """
    if not image_path or not os.path.exists(image_path):
        log("Telegraph: Dosya bulunamadi", "ERROR")
        return None
    try:
        file_size = os.path.getsize(image_path)
        if file_size > _TELEGRAPH_MAX_FILE_SIZE:
            log(f"Telegraph: Dosya cok buyuk: {file_size // 1024}KB (max 5MB)", "WARNING")
            return None
        log(f"Telegraph: Yukleniyor... ({file_size // 1024}KB)")
        with open(image_path, "rb") as f:
            resp = requests.post(
                _TELEGRAPH_API_URL,
                files={"file": ("image.jpg", f, "image/jpeg")},
                headers={"User-Agent": _UPLOAD_USER_AGENT},
                timeout=30,
            )
        if resp.status_code == 200:
            try:
                data = resp.json()
                if isinstance(data, list) and data and "src" in data[0]:
                    src = data[0]["src"]
                    url = f"https://telegra.ph{src}"
                    log(f"Telegraph: Basarili! URL uzunlugu={len(url)}")
                    return url
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
        log(f"Telegraph: Basarisiz status={resp.status_code} body={resp.text[:200]}", "WARNING")
        return None
    except requests.exceptions.Timeout:
        log("Telegraph: Zaman asimi", "WARNING")
        return None
    except Exception as exc:
        log(f"Telegraph: Hata: {exc}", "WARNING")
        return None
def _upload_imgbb(image_path: str) -> str | None:
    """
    ImgBB'ye gorsel yukler. (Ucretsiz tier, IMGBB_API_KEY gerekli)
    Dosya limiti: 32MB
    NOT: IMGBB_API_KEY env yoksa atlanir.
    """
    api_key = os.environ.get("IMGBB_API_KEY", "").strip()
    if not api_key:
        log("ImgBB: IMGBB_API_KEY env yok, atlaniyor", "INFO")
        return None
    if not image_path or not os.path.exists(image_path):
        log("ImgBB: Dosya bulunamadi", "ERROR")
        return None
    try:
        file_size = os.path.getsize(image_path)
        if file_size > _IMGBB_MAX_FILE_SIZE:
            log(f"ImgBB: Dosya cok buyuk: {file_size // 1024}KB", "ERROR")
            return None
        log(f"ImgBB: Yukleniyor... ({file_size // 1024}KB)")
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
        payload = {"key": api_key, "image": image_data}
        resp = requests.post(
            _IMGBB_API_URL,
            data=payload,
            headers={"User-Agent": _UPLOAD_USER_AGENT},
            timeout=30,
        )
        if resp.status_code != 200:
            log(f"ImgBB: HTTP hatasi: {resp.status_code} - {resp.text[:200]}", "WARNING")
            return None
        result = resp.json()
        if not result.get("success", False):
            log(f"ImgBB: Upload basarisiz: {result}", "WARNING")
            return None
        data = result.get("data", {})
        # En kaliteli versiyonu tercih et
        image_url = (
            data.get("medium", {}).get("url", "")
            or data.get("url", "")
            or data.get("display_url", "")
        )
        if image_url:
            log(f"ImgBB: Basarili! URL uzunlugu={len(image_url)}")
            return image_url
        log(f"ImgBB: URL bulunamadi: {list(data.keys())}", "WARNING")
        return None
    except requests.exceptions.Timeout:
        log("ImgBB: Zaman asimi", "WARNING")
        return None
    except Exception as exc:
        log(f"ImgBB: Hata: {exc}", "WARNING")
        return None
# ═══════════════════════════════════════════════════════════════════════════════
# ORIJINAL URL CIKARMA
# ═══════════════════════════════════════════════════════════════════════════════
def _extract_original_urls(article: dict, max_urls: int = 5) -> list[str]:
    """
    Article dict'inden orijinal gorsel URL'lerini cikarir.
    Oncelik sirasi:
    1. image_candidates (kaynak tipi bilgili, kalite sirali)
    2. image_url (dogrudan alan)
    3. rss_image_url (RSS'den gelen)
    4. link (haber linki - degil, bu sayfa URL'si, atla)
    Returns:
        En fazla max_urls adet benzersiz, gecerli HTTP(S) URL listesi
    """
    urls: list[str] = []
    seen: set[str] = set()
    def _add(url: str) -> None:
        if not url or not isinstance(url, str):
            return
        url = url.strip()
        if not url.startswith("http"):
            return
        if url in seen:
            return
        seen.add(url)
        urls.append(url)
    # 1. image_candidates (en kaliteli adaylar once)
    candidates = article.get("image_candidates", [])
    if isinstance(candidates, list):
        for candidate in candidates[:max_urls * 2]:
            if isinstance(candidate, dict):
                _add(candidate.get("url", ""))
            elif isinstance(candidate, str):
                _add(candidate)
    # 2. Direkt alanlar
    _add(article.get("image_url", ""))
    _add(article.get("rss_image_url", ""))
    return urls[:max_urls]
# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API - TEMEL FONKSIYONLAR
# ═══════════════════════════════════════════════════════════════════════════════
def post_text(message: str) -> str | None:
    """Threads'a sadece metin paylasimi yapar."""
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None
    threads_user_id = _get_threads_user_id(ig_user_id, token)
    if not threads_user_id:
        return None
    truncated_message = _truncate_for_threads(message)
    if len(truncated_message) < len(message):
        log(f"Threads: Metin {len(message)} -> {len(truncated_message)} karaktere kisildi", "WARNING")
    container_url = f"{_BASE_URL}/{threads_user_id}/threads"
    container_data = {
        "media_type": "TEXT",
        "text": truncated_message,
        "access_token": token,
    }
    headers = {"Authorization": f"Bearer {token}"}
    log("Threads TEXT container olusturuluyor...")
    container_resp = _post_with_retry(container_url, container_data, "create_text_container", headers=headers)
    container_id = container_resp.get("id")
    if not container_id:
        log("Threads text container olusturulamadi", "ERROR")
        return None
    return _publish_container(threads_user_id, container_id, token, "text")
def post_image(message: str, image_url: str, auto_fallback: bool = True) -> str | None:
    """
    Threads'a public URL ile gorsel paylasimi yapar.
    Args:
        message: Paylasim metni
        image_url: KAMUYA ACIK gorsel URL (yerel dosya YOLU DEGIL!)
        auto_fallback: True ise basarisiz olunca otomatik metne fallback yapar
    DİKKAT: image_url KAMUYA ACIK bir URL olmalidir!
    Yerel dosya yolu (/tmp/...) KULLANILAMAZ.
    Yerel dosya icin post_with_image() kullanin.
    """
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None
    threads_user_id = _get_threads_user_id(ig_user_id, token)
    if not threads_user_id:
        return None
    if not image_url:
        log("post_image: image_url bos", "WARNING")
        if auto_fallback:
            return post_text(message)
        return None
    # Yerel dosya yolu kontrolu - yanlisliksa uyari ver
    if image_url.startswith("/") or (len(image_path := image_url) > 2 and image_path[1] == ":"):
        log(f"post_image: YEREL DOSYA YOLU TESPIT EDILDI! URL={image_url}", "ERROR")
        log("post_image: Yerel dosyalar icin post_with_image() kullanin!", "ERROR")
        if auto_fallback:
            return post_text(message)
        return None
    truncated_message = _truncate_for_threads(message)
    if len(truncated_message) < len(message):
        log(f"Threads: Metin {len(message)} -> {len(truncated_message)} karaktere kisildi", "WARNING")
    container_url = f"{_BASE_URL}/{threads_user_id}/threads"
    container_data = {
        "media_type": "IMAGE",
        "text": truncated_message,
        "image_url": image_url,
        "access_token": token,
    }
    headers = {"Authorization": f"Bearer {token}"}
    log(f"Threads IMAGE container olusturuluyor (public URL ile)...")
    container_resp = _post_with_retry(container_url, container_data, "create_image_container", headers=headers)
    container_id = container_resp.get("id")
    if not container_id:
        log("Threads image container olusturulamadi", "WARNING")
        if auto_fallback:
            log("Threads: Metin fallback yapiliyor...", "WARNING")
            return post_text(message)
        return None
    return _publish_container(threads_user_id, container_id, token, "image")
# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API - FALLBACK ZINCIRI (ANA FONKSIYON)
# ═══════════════════════════════════════════════════════════════════════════════
def post_with_image(message: str, image_path: str, article: dict = None) -> str | None:
    """
    Threads gorsel paylasimi - TAM FALLBACK ZINCIRI.
    Akis sirasi:
    1. Article'daki orijinal gorsel URL'lerini dene (RSS/kaynak)
       -> En hizli yol, upload gerektirmez!
    2. Catbox.moe upload (ucretsiz, API key gerektirmez)
    3. 0x0.st upload (ucretsiz, API key gerektirmez)
    4. Telegraph upload (ucretsiz, API key gerektirmez)
    5. ImgBB upload (ucretsiz tier, IMGBB_API_KEY opsiyonel)
    6. Metin-only fallback (son care)
    Args:
        message: Paylasim metni (otomatik 500 karaktere kisilir)
        image_path: Yerel gorsel dosya yolu (orn: /tmp/otoxtra_img_xxx.jpg)
        article: Haber dict (orijinal URL'leri cikarmak icin, opsiyonel)
    Returns:
        Threads post ID, basarisiz olursa None
    """
    log("=" * 50)
    log("Threads GORSEL PAYLASIM: Fallback zinciri basliyor...")
    log("=" * 50)
    # ── ADIM 1: Orijinal URL'leri dene ──────────────────────────────────────
    if article:
        original_urls = _extract_original_urls(article)
        if original_urls:
            log(f"Threads ADIM 1: {len(original_urls)} orijinal URL denenecek...")
            for idx, url in enumerate(original_urls, 1):
                log(f"  Orijinal URL {idx}/{len(original_urls)}: {url[:80]}...")
                result = post_image(message, url, auto_fallback=False)
                if result:
                    log(f"  ✅ Orijinal URL basarili! (deneme {idx})")
                    return result
                log(f"  ❌ Orijinal URL basarisiz (deneme {idx})", "WARNING")
        else:
            log("Threads ADIM 1: Article'da orijinal URL yok, atlanıyor")
    else:
        log("Threads ADIM 1: Article dict yok, orijinal URL atlanıyor")
    # ── ADIM 2-5: Upload servisleri ─────────────────────────────────────────
    if image_path and os.path.exists(image_path):
        upload_services = [
            ("Catbox.moe", _upload_catbox),
            ("0x0.st", _upload_0x0),
            ("Telegraph", _upload_telegraph),
            ("ImgBB", _upload_imgbb),
        ]
        for step_num, (service_name, upload_fn) in enumerate(upload_services, start=2):
            log(f"Threads ADIM {step_num}: {service_name} deneniyor...")
            public_url = upload_fn(image_path)
            if not public_url:
                log(f"  ❌ {service_name} upload basarisiz", "WARNING")
                continue
            log(f"  Upload basarili! URL: {public_url[:60]}...")
            result = post_image(message, public_url, auto_fallback=False)
            if result:
                log(f"  ✅ {service_name} ile gorsel paylasim basarili!")
                return result
            log(f"  ❌ {service_name} URL ile Threads paylasim basarisiz", "WARNING")
    else:
        if image_path:
            log(f"Threads: Gorsel dosyasi bulunamadi: {image_path}", "WARNING")
        else:
            log("Threads: Gorsel dosya yolu bos", "WARNING")
    # ── ADIM 6: Metin fallback ──────────────────────────────────────────────
    log("Threads ADIM 6: Tum gorsel yontemleri basarisiz, metin-only fallback!", "WARNING")
    return post_text(message)
# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API - CAROUSEL
# ═══════════════════════════════════════════════════════════════════════════════
def _resolve_public_url(image_path: str, article: dict = None, image_index: int = 0) -> str | None:
    """
    Bir yerel gorsel dosyasini public URL'ye donusturur.
    Orijinal URL'leri once dener, sonra upload servislerini kullanir.
    """
    # Orijinal URL'leri dene
    if article:
        original_urls = _extract_original_urls(article)
        if image_index < len(original_urls):
            url = original_urls[image_index]
            if url:
                log(f"Carousel gorsel {image_index + 1}: Orijinal URL kullanilacak")
                return url
    # Upload servisleri
    if image_path and os.path.exists(image_path):
        for upload_fn in [_upload_catbox, _upload_0x0, _upload_telegraph, _upload_imgbb]:
            url = upload_fn(image_path)
            if url:
                return url
    return None
def post_carousel(message: str, image_paths: list[str], article: dict = None) -> str | None:
    """
    Threads'a carousel (coklu gorsel) paylasim yapar.
    Orijinal URL'leri once dener, sonra upload servislerini kullanir.
    Args:
        message: Paylasim metni
        image_paths: Yerel gorsel dosya yollari listesi (2-10 arasi)
        article: Haber dict (orijinal URL'leri cikarmak icin, opsiyonel)
    Returns:
        Threads post ID, basarisiz olursa None
    """
    if not image_paths or len(image_paths) < 2:
        log("post_carousel: En az 2 gorsel gerekli", "WARNING")
        if len(image_paths) == 1:
            return post_with_image(message, image_paths[0], article=article)
        return post_text(message)
    # Max 10 gorsel (Threads carousel limiti)
    image_paths = image_paths[:10]
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return None
    threads_user_id = _get_threads_user_id(ig_user_id, token)
    if not threads_user_id:
        return None
    # 1. Her gorsel icin public URL cozumle
    item_ids = []
    for idx, img_path in enumerate(image_paths):
        if not os.path.exists(img_path):
            log(f"post_carousel: Gorsel bulunamadi, atlanıyor: {img_path}", "WARNING")
            continue
        public_url = _resolve_public_url(img_path, article=article, image_index=idx)
        if not public_url:
            log(f"post_carousel: Gorsel {idx + 1} icin URL elde edilemedi, atlanıyor", "WARNING")
            continue
        # Carousel item container olustur
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
            log(f"Carousel item {idx + 1} basarisiz, atlanıyor", "WARNING")
    if len(item_ids) < 2:
        log(f"post_carousel: Yeterli item olusturulamadi ({len(item_ids)}/2)", "WARNING")
        if item_ids and image_paths:
            return post_with_image(message, image_paths[0], article=article)
        return post_text(message)
    # 2. Carousel container olustur
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
