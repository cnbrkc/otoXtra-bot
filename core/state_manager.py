"""
core/state_manager.py — Pipeline Durum Yöneticisi

otoXtra Facebook Botu için ajanlar arası veri taşıma sistemini yönetir.
Her ajan işini bitirince sonucunu pipeline.json'a yazar.
Bir sonraki ajan oradan okur. Böylece ajanlar birbirini beklemez,
biri çökse bile diğerlerinin çıktıları kaybolmaz.

İçerdiği fonksiyonlar:
  - init_pipeline(run_id)                    : Yeni çalışma başlatır
  - get_stage(stage_name)                    : Aşama çıktısını döner
  - set_stage(stage_name, status, output)    : Aşama sonucunu kaydeder
  - get_status()                             : Genel pipeline durumunu döner
  - is_stage_done(stage_name)                : Aşama bitti mi?
  - get_pipeline()                           : pipeline.json'un tamamını döner

Kullanım:
    from core.state_manager import init_pipeline, get_stage, set_stage

    init_pipeline("2025-01-15-14:00")
    set_stage("fetch", "done", {"articles": [...]})
    data = get_stage("fetch")

YANLIŞ kullanım (YAPMA):
    pipeline.json dosyasını elle düzenleme
    Bu modülü bypass ederek direkt dosyaya yazma
"""

import os
import json
from datetime import datetime, timezone, timedelta
from core.logger import log


# ============================================================
# SABİTLER
# ============================================================

# Geçerli aşama isimleri — sıra önemli
VALID_STAGES = ["fetch", "score", "write", "image", "publish"]

# Geçerli durum değerleri
VALID_STATUSES = ["waiting", "running", "done", "error"]

# pipeline.json dosya yolu
# Bu dosya (state_manager.py) core/ klasöründe
# Bir üst dizin proje kökü → queue/pipeline.json
_PIPELINE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "queue",
    "pipeline.json"
)


# ============================================================
# YARDIMCI — DOSYA OKUMA / YAZMA
# ============================================================

def _load_pipeline() -> dict:
    """pipeline.json dosyasını okur.

    Dosya yoksa veya bozuksa boş yapı döner.
    Dışarıdan çağrılmaz, sadece bu modül içinde kullanılır.

    Returns:
        dict: Pipeline içeriği. Hata durumunda boş yapı.
    """
    try:
        with open(_PIPELINE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log(f"pipeline.json bulunamadı: {_PIPELINE_PATH}", "WARNING")
        return _empty_pipeline()
    except json.JSONDecodeError as e:
        log(f"pipeline.json parse hatası: {e}", "ERROR")
        return _empty_pipeline()
    except Exception as e:
        log(f"pipeline.json okuma hatası: {e}", "ERROR")
        return _empty_pipeline()


def _save_pipeline(data: dict) -> bool:
    """pipeline.json dosyasına yazar.

    Yazmadan önce queue/ klasörünün var olduğundan emin olur.
    Dışarıdan çağrılmaz, sadece bu modül içinde kullanılır.

    Args:
        data: Kaydedilecek pipeline dict'i.

    Returns:
        bool: Başarılıysa True, hata varsa False.
    """
    try:
        directory = os.path.dirname(_PIPELINE_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(_PIPELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log(f"pipeline.json yazma hatası: {e}", "ERROR")
        return False


def _empty_pipeline() -> dict:
    """Boş pipeline yapısını döner.

    Tüm ajanlar 'waiting' durumunda başlar.

    Returns:
        dict: Boş pipeline şablonu.
    """
    return {
        "run_id": None,
        "status": "idle",
        "started_at": None,
        "updated_at": None,
        "stages": {
            stage: {
                "status": "waiting",
                "output": None,
                "error": None,
                "updated_at": None
            }
            for stage in VALID_STAGES
        }
    }


def _get_now_str() -> str:
    """Şu anki zamanı ISO formatında döner (UTC+3).

    Returns:
        str: Örnek → "2025-01-15T14:32:00+03:00"
    """
    turkey_tz = timezone(timedelta(hours=3))
    return datetime.now(turkey_tz).isoformat()


# ============================================================
# 1. PIPELINE BAŞLATMA
# ============================================================

def init_pipeline(run_id: str) -> bool:
    """Yeni bir çalışma başlatır. pipeline.json'u sıfırlar.

    Her orchestrator.py çalışmasının başında çağrılır.
    Önceki çalışmanın verilerini temizler, tüm aşamaları
    'waiting' durumuna getirir.

    Args:
        run_id: Bu çalışmanın benzersiz kimliği.
                Örnek: "2025-01-15-14:00"
                orchestrator.py tarafından üretilir.

    Returns:
        bool: Başarılıysa True, hata varsa False.

    Örnek:
        init_pipeline("2025-01-15-14:00")
    """
    now = _get_now_str()

    pipeline = _empty_pipeline()
    pipeline["run_id"] = run_id
    pipeline["status"] = "running"
    pipeline["started_at"] = now
    pipeline["updated_at"] = now

    success = _save_pipeline(pipeline)

    if success:
        log(f"Pipeline başlatıldı → run_id: {run_id}")
    else:
        log(f"Pipeline başlatılamadı → run_id: {run_id}", "ERROR")

    return success


# ============================================================
# 2. AŞAMA ÇIKTISINI OKUMA
# ============================================================

def get_stage(stage_name: str) -> dict:
    """Bir aşamanın tüm verisini döner.

    Args:
        stage_name: Aşama adı. Geçerli değerler:
                    "fetch", "score", "write", "image", "publish"

    Returns:
        dict: Aşama verisi → {
                "status": "done",
                "output": {...},
                "error": None,
                "updated_at": "2025-01-15T14:32:00+03:00"
              }
              Aşama bulunamazsa boş aşama yapısı döner.

    Örnek:
        stage = get_stage("fetch")
        articles = stage["output"]
    """
    if stage_name not in VALID_STAGES:
        log(f"Geçersiz aşama adı: {stage_name}. Geçerliler: {VALID_STAGES}", "ERROR")
        return {"status": "error", "output": None, "error": "Geçersiz aşama", "updated_at": None}

    pipeline = _load_pipeline()
    stages = pipeline.get("stages", {})

    if stage_name not in stages:
        log(f"Aşama pipeline'da bulunamadı: {stage_name}", "WARNING")
        return {"status": "waiting", "output": None, "error": None, "updated_at": None}

    return stages[stage_name]


# ============================================================
# 3. AŞAMA SONUCUNU KAYDETME
# ============================================================

def set_stage(stage_name: str, status: str, output=None, error: str = None) -> bool:
    """Bir aşamanın sonucunu pipeline.json'a kaydeder.

    Her ajan işini bitirince bu fonksiyonu çağırır.

    Args:
        stage_name: Aşama adı. Geçerli değerler:
                    "fetch", "score", "write", "image", "publish"
        status:     Durum. Geçerli değerler:
                    "waiting" → henüz başlamadı
                    "running" → şu an çalışıyor
                    "done"    → başarıyla tamamlandı
                    "error"   → hata oluştu
        output:     Aşamanın çıktı verisi (herhangi bir tip).
                    "done" durumunda dolu olmalı.
                    "error" durumunda None olabilir.
        error:      Hata mesajı (sadece "error" durumunda dolu olur).

    Returns:
        bool: Başarılıysa True, hata varsa False.

    Örnek — başarılı:
        set_stage("fetch", "done", output={"articles": [...]})

    Örnek — hatalı:
        set_stage("fetch", "error", error="RSS bağlantısı kurulamadı")

    Örnek — çalışmaya başladı:
        set_stage("fetch", "running")
    """
    # Geçerlilik kontrolleri
    if stage_name not in VALID_STAGES:
        log(f"Geçersiz aşama adı: {stage_name}", "ERROR")
        return False

    if status not in VALID_STATUSES:
        log(f"Geçersiz durum: {status}. Geçerliler: {VALID_STATUSES}", "ERROR")
        return False

    # Mevcut pipeline'ı oku
    pipeline = _load_pipeline()

    # stages anahtarı yoksa oluştur
    if "stages" not in pipeline:
        pipeline["stages"] = {}

    # Aşamayı güncelle
    now = _get_now_str()
    pipeline["stages"][stage_name] = {
        "status": status,
        "output": output,
        "error": error,
        "updated_at": now
    }

    # Genel pipeline durumunu güncelle
    pipeline["updated_at"] = now

    # Tüm aşamalar bittiyse pipeline'ı kapat
    all_done = all(
        pipeline["stages"].get(s, {}).get("status") == "done"
        for s in VALID_STAGES
    )
    if all_done:
        pipeline["status"] = "completed"
        log("Pipeline tamamlandı → tüm aşamalar done")

    # Herhangi bir aşama hatalıysa pipeline'ı hatalı işaretle
    any_error = any(
        pipeline["stages"].get(s, {}).get("status") == "error"
        for s in VALID_STAGES
    )
    if any_error and pipeline["status"] == "running":
        pipeline["status"] = "error"

    # Kaydet
    success = _save_pipeline(pipeline)

    if success:
        log(f"Aşama güncellendi → {stage_name}: {status}")
    else:
        log(f"Aşama kaydedilemedi → {stage_name}: {status}", "ERROR")

    return success


# ============================================================
# 4. GENEL DURUM
# ============================================================

def get_status() -> str:
    """Genel pipeline durumunu döner.

    Returns:
        str: "idle" / "running" / "completed" / "error"
             pipeline.json okunamazsa "unknown" döner.

    Örnek:
        status = get_status()
        if status == "error":
            log("Önceki çalışma hatayla bitti", "WARNING")
    """
    pipeline = _load_pipeline()
    return pipeline.get("status", "unknown")


# ============================================================
# 5. AŞAMA BİTTİ Mİ?
# ============================================================

def is_stage_done(stage_name: str) -> bool:
    """Belirtilen aşamanın başarıyla tamamlanıp tamamlanmadığını kontrol eder.

    orchestrator.py'ın bir sonraki ajanı çalıştırmadan önce
    önceki ajanın bitip bitmediğini kontrol etmesi için kullanılır.

    Args:
        stage_name: Kontrol edilecek aşama adı.

    Returns:
        bool: status == "done" ise True, değilse False.

    Örnek:
        if not is_stage_done("fetch"):
            log("Fetch tamamlanmadı, scorer çalıştırılamaz", "ERROR")
            return False
    """
    stage = get_stage(stage_name)
    return stage.get("status") == "done"


# ============================================================
# 6. PIPELINE TAMAMINI GETIR
# ============================================================

def get_pipeline() -> dict:
    """pipeline.json'un tamamını döner.

    Hata ayıklama ve loglama için kullanılır.

    Returns:
        dict: Pipeline'ın tam içeriği.
    """
    return _load_pipeline()


# ============================================================
# MODÜL TESTİ (doğrudan çalıştırılırsa)
# ============================================================

if __name__ == "__main__":
    log("=== core/state_manager.py modül testi başlıyor ===")

    # 1. Pipeline başlat
    run_id = "test-2025-01-15-14:00"
    success = init_pipeline(run_id)
    log(f"Pipeline başlatıldı: {success}")

    # 2. Genel durum kontrol
    status = get_status()
    log(f"Pipeline durumu: {status}  → 'running' olmalı")

    # 3. Aşama bitti mi? (henüz bitmedi)
    done = is_stage_done("fetch")
    log(f"fetch bitti mi: {done}  → False olmalı")

    # 4. Aşamayı çalışıyor işaretle
    set_stage("fetch", "running")
    stage = get_stage("fetch")
    log(f"fetch durumu: {stage['status']}  → 'running' olmalı")

    # 5. Aşamayı tamamla
    fake_output = {
        "articles": [
            {"title": "Test Haberi 1", "url": "https://test.com/1", "score": 0},
            {"title": "Test Haberi 2", "url": "https://test.com/2", "score": 0}
        ],
        "count": 2
    }
    set_stage("fetch", "done", output=fake_output)

    # 6. Aşama bitti mi? (şimdi bitti)
    done = is_stage_done("fetch")
    log(f"fetch bitti mi: {done}  → True olmalı")

    # 7. Çıktıyı oku
    stage = get_stage("fetch")
    article_count = len(stage.get("output", {}).get("articles", []))
    log(f"fetch çıktısındaki haber sayısı: {article_count}  → 2 olmalı")

    # 8. Hata senaryosu
    set_stage("score", "error", error="YZ bağlantısı kurulamadı")
    stage = get_stage("score")
    log(f"score durumu: {stage['status']}  → 'error' olmalı")
    log(f"score hatası: {stage['error']}")

    # 9. Geçersiz aşama adı
    invalid = get_stage("gecersiz_asama")
    log(f"Geçersiz aşama sonucu: {invalid['status']}  → 'error' olmalı")

    # 10. Pipeline özeti
    pipeline = get_pipeline()
    log(f"Pipeline run_id: {pipeline.get('run_id')}")
    log(f"Pipeline durumu: {pipeline.get('status')}")

    log("=== core/state_manager.py modül testi tamamlandı ===")
