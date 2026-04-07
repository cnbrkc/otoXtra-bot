"""
core/state_manager.py - Pipeline durum yoneticisi
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from core.logger import log


VALID_STAGES = ["fetch", "score", "write", "image", "publish"]
VALID_STATUSES = ["waiting", "running", "done", "error"]

_PIPELINE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "queue",
    "pipeline.json",
)


def _get_now_str() -> str:
    turkey_tz = timezone(timedelta(hours=3))
    return datetime.now(turkey_tz).isoformat()


def _empty_stage() -> Dict[str, Any]:
    return {
        "status": "waiting",
        "output": None,
        "error": None,
        "updated_at": None,
    }


def _empty_pipeline() -> Dict[str, Any]:
    return {
        "run_id": None,
        "status": "idle",
        "started_at": None,
        "updated_at": None,
        "stages": {stage: _empty_stage() for stage in VALID_STAGES},
    }


def _compute_pipeline_status(pipeline: Dict[str, Any]) -> str:
    stages = pipeline.get("stages", {})
    if not isinstance(stages, dict):
        return "idle"

    statuses = [stages.get(s, {}).get("status", "waiting") for s in VALID_STAGES]

    if any(s == "error" for s in statuses):
        return "error"
    if all(s == "done" for s in statuses):
        return "completed"
    if any(s in ("running", "done") for s in statuses):
        return "running"

    # Hepsi waiting ise ama aktif bir run baslatilmissa running kalsin
    if pipeline.get("run_id") and pipeline.get("started_at") and pipeline.get("status") == "running":
        return "running"

    return "idle"


def _normalize_pipeline(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return _empty_pipeline()

    normalized = _empty_pipeline()
    normalized["run_id"] = data.get("run_id")
    normalized["started_at"] = data.get("started_at")
    normalized["updated_at"] = data.get("updated_at")
    normalized["status"] = data.get("status", "idle")

    incoming_stages = data.get("stages", {})
    if not isinstance(incoming_stages, dict):
        incoming_stages = {}

    for stage in VALID_STAGES:
        raw_stage = incoming_stages.get(stage, {})
        if not isinstance(raw_stage, dict):
            raw_stage = {}

        status = raw_stage.get("status", "waiting")
        if status not in VALID_STATUSES:
            status = "waiting"

        normalized["stages"][stage] = {
            "status": status,
            "output": raw_stage.get("output"),
            "error": raw_stage.get("error"),
            "updated_at": raw_stage.get("updated_at"),
        }

    normalized["status"] = _compute_pipeline_status(normalized)
    return normalized


def _load_pipeline() -> Dict[str, Any]:
    try:
        with open(_PIPELINE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return _normalize_pipeline(data)
    except FileNotFoundError:
        log(f"pipeline.json bulunamadi: {_PIPELINE_PATH}", "WARNING")
        return _empty_pipeline()
    except json.JSONDecodeError as exc:
        log(f"pipeline.json parse hatasi: {exc}", "ERROR")
        return _empty_pipeline()
    except Exception as exc:
        log(f"pipeline.json okuma hatasi: {exc}", "ERROR")
        return _empty_pipeline()


def _save_pipeline(data: Dict[str, Any]) -> bool:
    try:
        from core.config_loader import save_json

        normalized = _normalize_pipeline(data)
        return save_json(_PIPELINE_PATH, normalized)
    except Exception as exc:
        log(f"pipeline.json write error: {exc}", "ERROR")
        return False


def init_pipeline(run_id: str) -> bool:
    if not isinstance(run_id, str) or not run_id.strip():
        log("init_pipeline: run_id bos veya gecersiz", "ERROR")
        return False

    now = _get_now_str()
    pipeline = _empty_pipeline()
    pipeline["run_id"] = run_id.strip()
    pipeline["started_at"] = now
    pipeline["updated_at"] = now
    pipeline["status"] = "running"

    success = _save_pipeline(pipeline)
    if success:
        log(f"Pipeline baslatildi -> run_id: {pipeline['run_id']}")
    else:
        log(f"Pipeline baslatilamadi -> run_id: {pipeline['run_id']}", "ERROR")
    return success


def get_stage(stage_name: str) -> Dict[str, Any]:
    if stage_name not in VALID_STAGES:
        log(f"Gecersiz asama adi: {stage_name}. Gecerliler: {VALID_STAGES}", "ERROR")
        return {
            "status": "error",
            "output": None,
            "error": "Gecersiz asama",
            "updated_at": None,
        }

    pipeline = _load_pipeline()
    return pipeline.get("stages", {}).get(stage_name, _empty_stage())


def set_stage(stage_name: str, status: str, output: Any = None, error: str = None) -> bool:
    if stage_name not in VALID_STAGES:
        log(f"Gecersiz asama adi: {stage_name}", "ERROR")
        return False

    if status not in VALID_STATUSES:
        log(f"Gecersiz durum: {status}. Gecerliler: {VALID_STATUSES}", "ERROR")
        return False

    pipeline = _load_pipeline()
    now = _get_now_str()

    if "stages" not in pipeline or not isinstance(pipeline["stages"], dict):
        pipeline["stages"] = {stage: _empty_stage() for stage in VALID_STAGES}

    stage_payload = {
        "status": status,
        "output": output,
        "error": error,
        "updated_at": now,
    }

    if status == "error" and not stage_payload["error"]:
        stage_payload["error"] = f"{stage_name} asamasinda hata"

    if status in ("waiting", "running"):
        stage_payload["error"] = None
        if status == "waiting":
            stage_payload["output"] = None

    pipeline["stages"][stage_name] = stage_payload
    pipeline["updated_at"] = now
    pipeline["status"] = _compute_pipeline_status(pipeline)

    success = _save_pipeline(pipeline)
    if success:
        log(f"Asama guncellendi -> {stage_name}: {status}")
    else:
        log(f"Asama kaydedilemedi -> {stage_name}: {status}", "ERROR")
    return success


def get_status() -> str:
    pipeline = _load_pipeline()
    status = pipeline.get("status", "unknown")
    if status not in ["idle", "running", "completed", "error", "unknown"]:
        return "unknown"
    return status


def is_stage_done(stage_name: str) -> bool:
    stage = get_stage(stage_name)
    return stage.get("status") == "done"


def get_pipeline() -> Dict[str, Any]:
    return _load_pipeline()


if __name__ == "__main__":
    log("state_manager smoke test basladi")
    init_pipeline("test-2026-01-01-10:00")
    log(f"status: {get_status()}")
    set_stage("fetch", "running")
    set_stage("fetch", "done", output={"articles": [{"title": "Test"}]})
    set_stage("score", "error", error="Model timeout")
    log(f"pipeline status: {get_status()}")
    log("state_manager smoke test bitti")
