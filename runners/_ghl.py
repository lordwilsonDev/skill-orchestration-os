"""Shared GoHighLevel (LeadConnector) API helper for Hermes GHL runners.

stdlib-only. Loads creds from the environment, falling back to the Hermes
credential store (~/.hermes/.env). Sends a browser User-Agent because the
LeadConnector API sits behind Cloudflare, which 1010-blocks default clients.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

BASE = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
# Cloudflare in front of the API rejects default urllib UA with Error 1010.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def _load_env() -> dict[str, str]:
    """os.environ, backfilled from ~/.hermes/.env for anything unset."""
    env = dict(os.environ)
    dotenv = Path.home() / ".hermes" / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def creds() -> tuple[str, str]:
    """(token, location_id). Empty strings if unset — callers report the error."""
    env = _load_env()
    return env.get("GHL_PIT_TOKEN", ""), env.get("GHL_LOCATION_ID", "")


def request(method: str, path: str, params: dict | None = None,
            body: dict | None = None) -> tuple[int, object]:
    """(status_code, parsed_json). status 0 = transport failure."""
    token, _ = creds()
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Version": VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}
