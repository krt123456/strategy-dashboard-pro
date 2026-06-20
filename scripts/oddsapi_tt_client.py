#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Dict

import requests


API_BASE = "https://api.odds-api.io/v3"
KEY_LOG_ENV = "ODDS_API_KEY_LOG"


def _extract_keys(text: str) -> list[str]:
    keys = []
    for token in text.split():
        token = token.strip()
        if len(token) >= 16 and all(ch.isalnum() or ch in "-_" for ch in token):
            keys.append(token)
    return keys


def load_api_keys(api_key: str | None, api_key_file: str | None) -> list[str]:
    if api_key:
        return [api_key.strip()]
    if api_key_file:
        path = Path(api_key_file)
        if path.is_dir():
            keys: list[str] = []
            for file in sorted(path.iterdir()):
                if not file.is_file():
                    continue
                try:
                    text = file.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                keys.extend(_extract_keys(text))
            if keys:
                return keys
        else:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            keys = _extract_keys(text)
            if keys:
                return keys
    env_key = os.environ.get("ODDS_API_IO_KEY", "").strip()
    if env_key:
        return [env_key]
    raise RuntimeError("Missing Odds-API.io key. Use --api-key/--api-key-file or set ODDS_API_IO_KEY.")


def load_api_key(api_key: str | None, api_key_file: str | None) -> str:
    keys = load_api_keys(api_key, api_key_file)
    return keys[0]


def get_json(
    path: str,
    params: Dict[str, str],
    *,
    api_keys: list[str] | None = None,
    timeout: int = 20,
    retries: int = 6,
    backoff: float = 3.0,
):
    url = f"{API_BASE}/{path.lstrip('/')}"
    attempt = 0
    keys = api_keys or []
    while True:
        attempt += 1
        if keys:
            # Rotate only on 401/429; keep primary key until it fails.
            for key in keys:
                params_with_key = dict(params)
                params_with_key["apiKey"] = key
                resp = requests.get(url, params=params_with_key, timeout=timeout)
                if resp.status_code == 200:
                    log_path = os.environ.get(KEY_LOG_ENV, "").strip()
                    if log_path:
                        try:
                            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                            with open(log_path, "a", encoding="utf-8") as f:
                                f.write(f"{key}\n")
                        except Exception:
                            pass
                    return resp.json()
                if resp.status_code in (401, 429):
                    continue
                raise RuntimeError(f"Odds-API request failed with status {resp.status_code}")
        else:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (401, 429) and attempt <= retries:
                sleep_s = backoff * attempt
                time.sleep(sleep_s)
                continue
            raise RuntimeError(f"Odds-API request failed with status {resp.status_code}")
        if attempt <= retries:
            time.sleep(backoff * attempt)
            continue
        raise RuntimeError("Odds-API request failed: all keys exhausted")
