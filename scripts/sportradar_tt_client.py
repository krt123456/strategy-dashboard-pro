#!/usr/bin/env python3
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests


def load_keys(files: Iterable[str]) -> List[str]:
    keys: List[str] = []
    for path in files:
        p = Path(path)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8").strip()
        if text:
            keys.append(text)
    if not keys:
        raise RuntimeError("No Sportradar API keys found.")
    return keys


def build_url(access_level: str, language: str, endpoint: str, fmt: str = "json") -> str:
    endpoint = endpoint.lstrip("/")
    return f"https://api.sportradar.com/tabletennis/{access_level}/v2/{language}/{endpoint}.{fmt}"


def get_json(
    endpoint: str,
    *,
    access_level: str,
    language: str,
    keys: List[str],
    params: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    retries: int = 2,
    backoff: float = 1.5,
) -> dict:
    url = build_url(access_level, language, endpoint)
    last_status = None
    for attempt in range(retries + 1):
        for key in keys:
            resp = requests.get(url, headers={"x-api-key": key}, params=params, timeout=timeout)
            last_status = resp.status_code
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (401, 403, 429, 500, 503):
                continue
            # other errors: break early
            break
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Sportradar request failed with status {last_status} for {endpoint}")
