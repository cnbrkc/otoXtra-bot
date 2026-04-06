"""
core/config_loader.py — Merkezi Config Okuma Sistemi

otoXtra Facebook Botu için tüm modüller tarafından kullanılan
config okuma fonksiyonlarını içerir.

Kullanım:
    from core.config_loader import load_config, get_project_root

    settings = load_config("settings")   → config/settings.json
    keywords = load_config("keywords")   → config/keywords.json
    sources  = load_config("sources")    → config/sources.json

YANLIŞ kullanım (YAPMA):
    from src.utils import load_config
"""

import os
import json
from core.logger import log


# ============================================================
# 1. PROJE KÖK DİZİNİ
# ============================================================

def get_project_root() -> str:
    """Proje kök dizininin mutlak yolunu döner.

    Bu dosya (config_loader.py) core/ klasöründe bulunur.
    Bir üst dizin proje kök dizinidir (otoXtra-bot/).

    Returns:
        str: Proje kök dizininin mutlak yolu.
             Örnek: /home/runner/work/otoXtra-bot/otoXtra-bot
    """
    # __file__        → .../otoXtra-bot/core/config_loader.py
    # dirname 1. kez  → .../otoXtra-bot/core
    # dirname 2. kez  → .../otoXtra-bot          ← proje kökü
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# 2. JSON OKUMA / YAZMA
# ============================================================

def load_json(filepath: str) -> dict:
    """JSON dosyası okur ve dict olarak döner.

    Args:
        filepath: Okunacak JSON dosyasının tam yolu.

    Returns:
        dict: JSON içeriği. Dosya yoksa veya bozuksa boş dict {}.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log(f"JSON dosyası bulunamadı: {filepath}", "WARNING")
        return {}
    except json.JSONDecodeError as e:
        log(f"JSON parse hatası ({filepath}): {e}", "ERROR")
        return {}
    except Exception as e:
        log(f"JSON okuma hatası ({filepath}): {e}", "ERROR")
        return {}


def save_json(filepath: str, data: dict) -> bool:
    """Dict'i JSON dosyasına yazar.

    Yazmadan önce hedef klasörün var olduğundan emin olur.
    Klasör yoksa otomatik oluşturur.

    Args:
        filepath: Yazılacak JSON dosyasının tam yolu.
        data: Kaydedilecek dict.

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    try:
        directory = os.path.dirname(filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log(f"JSON yazma hatası ({filepath}): {e}", "ERROR")
        return False


# ============================================================
# 3. CONFIG YÜKLEME
# ============================================================

def load_config(config_name: str) -> dict:
    """Config klasöründen belirtilen JSON ayar dosyasını okur.

    Proje kök dizinindeki config/ klasöründen dosyayı bulur.
    Bu sayede bot hangi dizinden çalıştırılırsa çalıştırılsın
    doğru config dosyasını okur.

    Args:
        config_name: Dosya adı (uzantısız).
                     Örnek: "settings" → config/settings.json
                            "sources"  → config/sources.json
                            "keywords" → config/keywords.json
                            "scoring"  → config/scoring.json
                            "prompts"  → config/prompts.json

    Returns:
        dict: Config içeriği. Dosya yoksa boş dict {}.
    """
    filepath = os.path.join(get_project_root(), "config", f"{config_name}.json")
    data = load_json(filepath)
    if not data:
        log(f"Config yüklenemedi: {config_name}.json", "WARNING")
    return data


# ============================================================
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ============================================================

if __name__ == "__main__":
    log("=== core/config_loader.py modül testi başlıyor ===")

    # Proje kökü testi
    root = get_project_root()
    log(f"Proje kökü: {root}")

    # Her config dosyasını sırayla test et
    config_files = ["settings", "sources", "keywords", "scoring", "prompts"]

    for name in config_files:
        data = load_config(name)
        if data:
            log(f"{name}.json yüklendi — {len(data)} anahtar")
        else:
            log(f"{name}.json yüklenemedi", "WARNING")

    log("=== core/config_loader.py modül testi tamamlandı ===")
