"""
core/logger.py — Merkezi Log Sistemi

otoXtra Facebook Botu için tüm modüller tarafından kullanılan
log fonksiyonunu içerir.

Kullanım:
    from core.logger import log

    log("Haberler çekildi")
    log("Dosya bulunamadı", "WARNING")
    log("API bağlantısı başarısız", "ERROR")

Format:
    [2025-01-15 14:32:00] [INFO] Mesaj metni
"""

from datetime import datetime, timezone, timedelta


# ============================================================
# YARDIMCI — TÜRK SAATİ
# ============================================================

def _get_turkey_now() -> datetime:
    """Türkiye saatinde (UTC+3) şu anki zamanı döner.

    Bu fonksiyon SADECE log() tarafından kullanılır.
    Dışarıdan çağırmak istersen core/helpers.py içindeki
    get_turkey_now() fonksiyonunu kullan.

    Returns:
        datetime: Türkiye saat diliminde şu anki zaman.
    """
    turkey_tz = timezone(timedelta(hours=3))
    return datetime.now(turkey_tz)


# ============================================================
# ANA FONKSİYON
# ============================================================

def log(message: str, level: str = "INFO") -> None:
    """Konsola formatlı log yazar. GitHub Actions loglarında görünür.

    Format: [2025-01-15 14:32:00] [INFO] Mesaj metni

    Args:
        message: Log mesajı.
        level:   Seviye — "INFO", "WARNING" veya "ERROR".
                 Varsayılan: "INFO"

    Örnek çıktılar:
        [2025-01-15 14:32:00] [INFO]    Haberler çekildi
        [2025-01-15 14:32:01] [WARNING] Dosya bulunamadı
        [2025-01-15 14:32:02] [ERROR]   API bağlantısı başarısız
    """
    now_str = _get_turkey_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] [{level}] {message}")


# ============================================================
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ============================================================

if __name__ == "__main__":
    log("=== core/logger.py modül testi başlıyor ===")
    log("Bu bir INFO mesajıdır")
    log("Bu bir WARNING mesajıdır", "WARNING")
    log("Bu bir ERROR mesajıdır", "ERROR")
    log("=== core/logger.py modül testi tamamlandı ===")
