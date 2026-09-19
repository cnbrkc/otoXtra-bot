"""
core/image_uploader.py - Ortak Gorsel Yukleme Servisleri (v2.1 - Instagram JPEG Fix)
Instagram ve Threads platformlari icin ortak upload fonksiyonlari.
- 0x0.st (iflas etti) kaldirildi.
- tmpfiles.org ve freeimage.host (API keysiz) eklendi.
- Catbox ve ImgBB icin User-Agent guncellemesi yapildi (GitHub IP ban'i asmak icin).
- v2.1: Instagram Graph API sadece JPEG kabul ettigi icin, upload'tan once
  PNG/diger formatlar otomatik JPEG'e cevriliyor (Story publish hatasi fix).
"""

import base64
import json
import mimetypes
import os
import tempfile
from typing import Optional

import requests

from core.logger import log

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


_IMGBB_API_URL = "https://api.imgbb.com/1/upload"
_IMGBB_MAX_FILE_SIZE = 32 * 1024 * 1024

_CATBOX_API_URL = "https://catbox.moe/user/api.php"
_CATBOX_MAX_FILE_SIZE = 200 * 1024 * 1024

_TMPFILES_API_URL = "https://tmpfiles.org/api/v1/upload"
_FREEIMAGE_API_URL = "https://freeimage.host/api/1/upload"
_FREEIMAGE_API_KEY = "6d207e02198a847aa98d0a2a901485a5" # Anonim public key

_TELEGRAPH_API_URL = "https://telegra.ph/upload"
_TELEGRAPH_MAX_FILE_SIZE = 5 * 1024 * 1024

# GitHub IP banlarini asmak icin tarayici User-Agent'i
_UPLOAD_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 otoXtraBot/1.0"


def _is_valid_file(path: str) -> bool:
    return bool(path and isinstance(path, str) and os.path.exists(path) and os.path.isfile(path))


def _safe_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return -1


def _is_http_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def _guess_content_type(path: str) -> str:
    ctype, _ = mimetypes.guess_type(path)
    return ctype or "application/octet-stream"


def _url_returns_image(url: str) -> bool:
    """
    URL'nin gercekten bir gorsel dondurup dondurmedigini kontrol eder
    (Content-Type basliginin image/ ile basladigini dogrular). Bazi
    servisler (ornegin tmpfiles.org) direkt dosya yerine bir HTML
    onizleme sayfasi dondurebiliyor; bu Meta tarafinda "format desteklenmiyor"
    hatasina yol aciyor. Bu kontrol o durumu erken (Instagram'a gondermeden)
    yakalar.
    """
    try:
        resp = requests.head(
            url,
            headers={"User-Agent": _UPLOAD_USER_AGENT},
            timeout=15,
            allow_redirects=True,
        )
        ctype = resp.headers.get("Content-Type", "")
        if resp.status_code == 200 and ctype.lower().startswith("image/"):
            return True

        # Bazi sunucular HEAD'e duzgun cevap vermiyor, GET ile tekrar dene
        resp = requests.get(
            url,
            headers={"User-Agent": _UPLOAD_USER_AGENT},
            timeout=15,
            stream=True,
        )
        ctype = resp.headers.get("Content-Type", "")
        return resp.status_code == 200 and ctype.lower().startswith("image/")
    except Exception as exc:
        log(f"Image: URL dogrulama hatasi ({url[:60]}...): {exc}", "WARNING")
        return False


def _ensure_jpeg(image_path: str) -> str:
    """
    Instagram Graph API sadece JPEG destekliyor. PNG/diger formatlari
    JPEG'e cevirir; zaten JPEG ise dokunmadan orijinal path'i doner.
    Donusturme basarisiz olursa orijinal path'e geri doner (fail-safe).
    """
    ext = os.path.splitext(image_path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        return image_path

    if not _PIL_AVAILABLE:
        log("Image: Pillow (PIL) yuklu degil, JPEG donusumu atlanıyor", "WARNING")
        return image_path

    try:
        img = Image.open(image_path)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        fd, jpeg_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        img.save(jpeg_path, "JPEG", quality=90)
        log(f"Image: {ext} -> JPEG donusturuldu ({jpeg_path})")
        return jpeg_path
    except Exception as exc:
        log(f"Image: JPEG donusturme hatasi: {exc}", "WARNING")
        return image_path


def upload_imgbb(image_path: str) -> Optional[str]:
    api_key = os.environ.get("IMGBB_API_KEY", "").strip()
    if not api_key:
        log("ImgBB: IMGBB_API_KEY env yok, atlaniyor", "INFO")
        return None

    if not _is_valid_file(image_path):
        return None

    try:
        file_size = _safe_size(image_path)
        if file_size <= 0 or file_size > _IMGBB_MAX_FILE_SIZE:
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
            return None

        data = result.get("data", {})
        image_url = data.get("url", "") or data.get("display_url", "")

        if _is_http_url(image_url):
            log(f"ImgBB: Basarili! URL uzunlugu={len(image_url)}")
            return image_url
        return None

    except Exception as exc:
        log(f"ImgBB: Hata: {exc}", "WARNING")
        return None


def upload_catbox(image_path: str) -> Optional[str]:
    if not _is_valid_file(image_path):
        return None

    try:
        file_size = _safe_size(image_path)
        if file_size <= 0 or file_size > _CATBOX_MAX_FILE_SIZE:
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

        text = (resp.text or "").strip()
        if resp.status_code == 200 and _is_http_url(text):
            log(f"Catbox: Basarili! URL uzunlugu={len(text)}")
            return text

        log(f"Catbox: Basarisiz status={resp.status_code} body={text[:200]}", "WARNING")
        return None

    except Exception as exc:
        log(f"Catbox: Hata: {exc}", "WARNING")
        return None


def upload_tmpfiles(image_path: str) -> Optional[str]:
    if not _is_valid_file(image_path):
        return None

    try:
        file_size = _safe_size(image_path)
        if file_size <= 0:
            return None

        log(f"tmpfiles: Yukleniyor... ({file_size // 1024}KB)")
        with open(image_path, "rb") as f:
            resp = requests.post(
                _TMPFILES_API_URL,
                files={"file": f},
                headers={"User-Agent": _UPLOAD_USER_AGENT},
                timeout=30,
            )

        if resp.status_code == 200:
            url = resp.json().get("data", {}).get("url", "")
            if _is_http_url(url):
                log(f"tmpfiles: Basarili! URL={url}")
                return url

        log(f"tmpfiles: Basarisiz status={resp.status_code}", "WARNING")
        return None

    except Exception as exc:
        log(f"tmpfiles: Hata: {exc}", "WARNING")
        return None


def upload_freeimage(image_path: str) -> Optional[str]:
    if not _is_valid_file(image_path):
        return None

    try:
        file_size = _safe_size(image_path)
        if file_size <= 0:
            return None

        log(f"freeimage: Yukleniyor... ({file_size // 1024}KB)")
        with open(image_path, "rb") as f:
            resp = requests.post(
                _FREEIMAGE_API_URL,
                params={"key": _FREEIMAGE_API_KEY},
                files={"source": f},
                headers={"User-Agent": _UPLOAD_USER_AGENT},
                timeout=30,
            )

        if resp.status_code == 200:
            url = resp.json().get("image", {}).get("url", "")
            if _is_http_url(url):
                log(f"freeimage: Basarili! URL={url}")
                return url

        log(f"freeimage: Basarisiz status={resp.status_code}", "WARNING")
        return None

    except Exception as exc:
        log(f"freeimage: Hata: {exc}", "WARNING")
        return None


def upload_telegraph(image_path: str) -> Optional[str]:
    if not _is_valid_file(image_path):
        return None

    try:
        file_size = _safe_size(image_path)
        if file_size <= 0 or file_size > _TELEGRAPH_MAX_FILE_SIZE:
            return None

        ctype = _guess_content_type(image_path)
        fname = os.path.basename(image_path) or "image"

        log(f"Telegraph: Yukleniyor... ({file_size // 1024}KB)")
        with open(image_path, "rb") as f:
            resp = requests.post(
                _TELEGRAPH_API_URL,
                files={"file": (fname, f, ctype)},
                headers={"User-Agent": _UPLOAD_USER_AGENT},
                timeout=30,
            )

        if resp.status_code == 200:
            try:
                data = resp.json()
                if isinstance(data, list) and data and "src" in data[0]:
                    src = data[0]["src"]
                    url = f"https://telegra.ph{src}"
                    if _is_http_url(url):
                        log(f"Telegraph: Basarili! URL={url}")
                        return url
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        log(f"Telegraph: Basarisiz status={resp.status_code}", "WARNING")
        return None

    except Exception as exc:
        log(f"Telegraph: Hata: {exc}", "WARNING")
        return None


def _upload_with_host_tracking(
    image_path: str,
    platform_name: str = "Platform",
    exclude: Optional[set] = None,
) -> Optional[tuple[str, str]]:
    """
    Local dosyayi public URL'ye cevirir (fallback zinciri) ve hangi
    servisin kullanildigini da doner. `exclude` icindeki servis isimleri
    atlanir - bu sayede bir servisin verdigi URL uzak platform tarafindan
    (ornegin Instagram) reddedilirse, cagiran kod ayni servisi tekrar
    denemeden bir sonrakine gecebilir.
    """
    if not _is_valid_file(image_path):
        log(f"{platform_name} Upload: Dosya yok/gecersiz", "ERROR")
        return None

    # Instagram (ve genel uyumluluk icin) sadece JPEG kabul ediyor.
    image_path = _ensure_jpeg(image_path)

    file_size = _safe_size(image_path)
    if file_size <= 0:
        log(f"{platform_name} Upload: Dosya boyutu okunamadi", "ERROR")
        return None

    exclude = exclude or set()

    # Siralama: Meta'nin gorsel cekme konusunda daha guvenilir bulundugu
    # servisler once denenir. tmpfiles.org direkt image yerine HTML
    # onizleme sayfasi dondurebildigi icin en sona alindi.
    upload_services = [
        ("Catbox", upload_catbox, _CATBOX_MAX_FILE_SIZE),
        ("freeimage", upload_freeimage, 30 * 1024 * 1024),
        ("ImgBB", upload_imgbb, _IMGBB_MAX_FILE_SIZE),
        ("Telegraph", upload_telegraph, _TELEGRAPH_MAX_FILE_SIZE),
        ("tmpfiles", upload_tmpfiles, 50 * 1024 * 1024),
    ]

    for name, fn, limit in upload_services:
        if name in exclude:
            continue
        if file_size > limit:
            continue

        log(f"{platform_name} Upload: {name} deneniyor...")
        url = fn(image_path)
        if not _is_http_url(url):
            continue

        if not _url_returns_image(url):
            log(f"{platform_name} Upload: {name} gorsel degil (HTML/redirect donduruyor), atlaniyor", "WARNING")
            continue

        return (url, name)

    log(f"{platform_name} Upload: Tum servisler basarisiz (exclude={exclude})", "ERROR")
    return None


def get_public_url_fallback(image_path: str, platform_name: str = "Platform") -> Optional[str]:
    """
    Geriye-donuk uyumluluk icin: sadece URL doner (Threads vb. mevcut
    kullanimlar icin degismedi).
    """
    result = _upload_with_host_tracking(image_path, platform_name=platform_name)
    return result[0] if result else None


def get_public_url_with_host(
    image_path: str,
    platform_name: str = "Platform",
    exclude: Optional[set] = None,
) -> Optional[tuple[str, str]]:
    """
    (url, servis_adi) tuple'i doner. Bir servisin verdigi URL hedef
    platform tarafindan reddedilirse, servis adini `exclude` setine
    ekleyip tekrar cagirarak bir sonraki servisi deneyebilirsiniz.
    """
    return _upload_with_host_tracking(image_path, platform_name=platform_name, exclude=exclude)
