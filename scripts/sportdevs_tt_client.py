#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import requests


API_BASE = "https://table-tennis.sportdevs.com"


def load_api_key(api_key: Optional[str], api_key_file: Optional[str]) -> str:
    if api_key:
        return api_key.strip()
    if api_key_file:
        key = Path(api_key_file).read_text(encoding="utf-8").strip()
        if key:
            return key
    env_key = os.getenv("SPORTDEVS_API_KEY", "").strip()
    if env_key:
        return env_key
    raise RuntimeError("Missing SportDevs API key. Use --api-key/--api-key-file or set SPORTDEVS_API_KEY.")


def build_headers(api_key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def get_json(url: str, *, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None, timeout: int = 30) -> Any:
    resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
