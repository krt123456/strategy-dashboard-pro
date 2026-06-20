#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict

import requests


API_BASE = "https://api.oddspapi.io/v4"
KEY_LOG_ENV = "ODDSPAPI_KEY_LOG"


def _extract_keys(text: str) -> list[str]:
    keys: list[str] = []
    for token in text.replace(",", " ").split():
        token = token.strip()
        if len(token) >= 8 and all(ch.isalnum() or ch in "-_" for ch in token):
            keys.append(token)
    return keys


def load_api_keys(api_key: str | None, api_key_file: str | None) -> list[str]:
    if api_key:
        keys = _extract_keys(api_key)
        if keys:
            return keys
    if api_key_file:
        path = Path(api_key_file)
        if path.is_dir():
            keys: list[str] = []
            seen: set[str] = set()
            for file in sorted(path.iterdir()):
                if not file.is_file():
                    continue
                try:
                    text = file.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for key in _extract_keys(text):
                    if key in seen:
                        continue
                    seen.add(key)
                    keys.append(key)
            if keys:
                return keys
        elif path.exists():
            keys = _extract_keys(path.read_text(encoding="utf-8", errors="ignore"))
            if keys:
                return keys
    env_key = os.getenv("ODDSPAPI_KEY", "").strip()
    if env_key:
        keys = _extract_keys(env_key)
        if keys:
            return keys
    raise RuntimeError("Missing OddsPapi API key. Use --api-key/--api-key-file or set ODDSPAPI_KEY.")


def load_api_key(api_key: str | None, api_key_file: str | None) -> str:
    return load_api_keys(api_key, api_key_file)[0]


def get_json(
    path: str,
    params: Dict[str, str],
    *,
    api_keys: list[str] | None = None,
    timeout: int = 25,
    retries: int = 6,
    backoff: float = 2.0,
):
    url = f"{API_BASE}/{path.lstrip('/')}"
    attempt = 0
    while True:
        attempt += 1
        if api_keys:
            for key in api_keys:
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
                if resp.status_code in (401, 403, 429):
                    continue
                if resp.status_code == 503 and attempt <= retries:
                    break
                raise RuntimeError(f"OddsPapi request failed with status {resp.status_code}")
            if attempt <= retries:
                time.sleep(backoff * attempt)
                continue
            raise RuntimeError("OddsPapi request failed: all keys exhausted")

        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 503) and attempt <= retries:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = max(1.0, float(retry_after))
                except ValueError:
                    wait = backoff * attempt
            else:
                wait = backoff * attempt
            time.sleep(wait)
            continue
        raise RuntimeError(f"OddsPapi request failed with status {resp.status_code}")
