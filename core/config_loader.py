"""
core/config_loader.py
Merkezi config ve JSON okuma/yazma modulu.
"""

import json
import os
import tempfile
from typing import Any

from core.logger import log


def get_project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _empty_for_config(config_name: str) -> Any:
    if config_name == "sources":
        return {"feeds": []}
    return {}


def _normalize_sources(data: Any) -> dict:
    # 1) sources.json direkt listeyse -> {"feeds": [...]}
    if isinstance(data, list):
        return {"feeds": data}

    # 2) dict ise yaygin alan adlarini "feeds"e normalize et
    if isinstance(data, dict):
        if isinstance(data.get("feeds"), list):
            return {"feeds": data.get("feeds", [])}
        if isinstance(data.get("sources"), list):
            return {"feeds": data.get("sources", [])}
        if isinstance(data.get("rss"), list):
            return {"feeds": data.get("rss", [])}
        if isinstance(data.get("rss_feeds"), list):
            return {"feeds": data.get("rss_feeds", [])}
        if isinstance(data.get("items"), list):
            return {"feeds": data.get("items", [])}

    # 3) taninmayan format -> bos
    return {"feeds": []}


def _validate_config(config_name: str, data: Any) -> bool:
    # sources icin sade ve guvenli kontrol
    if config_name == "sources":
        return isinstance(data, dict) and isinstance(data.get("feeds"), list)

    if not isinstance(data, dict):
        return False

    required_keys = {
        "settings": ["posting", "images", "news", "ai"],
        "keywords": ["include_keywords", "exclude_keywords"],
        "scoring": ["thresholds"],
        "prompts": ["viral_scorer", "post_writer"],
    }

    if config_name not in required_keys:
        return True

    for key in required_keys[config_name]:
        if key not in data:
            return False

    if config_name == "keywords":
        if not isinstance(data.get("include_keywords"), list):
            return False
        if not isinstance(data.get("exclude_keywords"), list):
            return False

    return True


def load_json(filepath: str) -> Any:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log(f"JSON file not found: {filepath}", "WARNING")
        return {}
    except json.JSONDecodeError as exc:
        log(f"JSON parse error ({filepath}): {exc}", "ERROR")
        return {}
    except Exception as exc:
        log(f"JSON read error ({filepath}): {exc}", "ERROR")
        return {}


def save_json(filepath: str, data: Any) -> bool:
    directory = os.path.dirname(filepath) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            delete=False,
        ) as tmp_file:
            json.dump(data, tmp_file, indent=2, ensure_ascii=False)
            tmp_path = tmp_file.name

        os.replace(tmp_path, filepath)
        return True
    except Exception as exc:
        log(f"JSON write error ({filepath}): {exc}", "ERROR")
        return False


def load_config(config_name: str) -> Any:
    filepath = os.path.join(get_project_root(), "config", f"{config_name}.json")
    data = load_json(filepath)

    if config_name == "sources":
        data = _normalize_sources(data)

    if data in ({}, None):
        log(f"Config could not be loaded: {config_name}.json", "WARNING")
        return _empty_for_config(config_name)

    if not _validate_config(config_name, data):
        log(f"Invalid config schema: {config_name}.json", "ERROR")
        return _empty_for_config(config_name)

    return data
