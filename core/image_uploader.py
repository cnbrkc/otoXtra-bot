"""
core/image_uploader.py - Ortak Gorsel Yukleme Servisleri (v1.0)

Instagram ve Threads platformlari icin ortak upload fonksiyonlari.
DRY prensibi ile kod tekrarini onler.
"""

import base64
import json
import os
from typing import Optional

import requests

from core.logger import log

# ═══════════════════════════════════════════════════════════════════════════════
# SABITLER (Tum platformlarca paylasilir)
# ═══════════════════════════════════════════════════════════════════════════════

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
# UPLOAD FONKSIYONLARI
# ═══════════════════════════════════════════════════════════════════════════════

def upload_imgbb(image_path: str) -> Optional[str]:
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


def upload_catbox(image_path: str) -> Optional[str]:
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


def upload_0x0(image_path: str) -> Optional[str]:
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


def upload_telegraph(image_path: str) -> Optional[str]:
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


def get_public_url_fallback(image_path: str, platform_name: str = "Platform") -> Optional[str]:
    """
    Local dosyayi public URL'ye cevirir (Fallback zinciri).
    
    Args:
        image_path: Yerel gorsel dosya yolu
        platform_name: Log icin platform adi (örn: "IG Story", "Threads")
    
    Returns:
        Public URL veya None
    """
    if not image_path or not os.path.exists(image_path):
        return None
        
    file_size = os.path.getsize(image_path)
    
    upload_services = [
        ("ImgBB", upload_imgbb, _IMGBB_MAX_FILE_SIZE),
        ("Catbox", upload_catbox, _CATBOX_MAX_FILE_SIZE),
        ("0x0.st", upload_0x0, _ZER0X_MAX_FILE_SIZE),
        ("Telegraph", upload_telegraph, _TELEGRAPH_MAX_FILE_SIZE),
    ]
    
    for name, fn, limit in upload_services:
        if file_size > limit:
            log(f"{platform_name} Upload: {name} atlandi (boyut limiti asildi: {file_size // 1024}KB)", "INFO")
            continue
        log(f"{platform_name} Upload: {name} deneniyor...")
        url = fn(image_path)
        if url:
            return url
    
    return None


# Export constants for backward compatibility
__all__ = [
    'upload_imgbb', 'upload_catbox', 'upload_0x0', 'upload_telegraph',
    'get_public_url_fallback',
    '_IMGBB_API_URL', '_CATBOX_API_URL', '_ZER0X_API_URL', '_TELEGRAPH_API_URL',
    '_IMGBB_MAX_FILE_SIZE', '_CATBOX_MAX_FILE_SIZE', '_ZER0X_MAX_FILE_SIZE', '_TELEGRAPH_MAX_FILE_SIZE',
    '_UPLOAD_USER_AGENT'
]
