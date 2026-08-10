"""Runner: ghl_list_pipelines — read-only. Lists the sub-account's opportunity
pipelines and their stages. No side effects."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_ROOT = Path.home() / ".hermes" / "skills" / "skill-orchestration-os"
sys.path.insert(0, str(SKILL_ROOT / "runners"))

import _ghl  # noqa: E402


def main() -> int:
    token, location = _ghl.creds()
    if not token or not location:
        print(json.dumps({"status": "error",
                          "error": "missing GHL_PIT_TOKEN or GHL_LOCATION_ID"}, ensure_ascii=False))
        return 0
    status, body = _ghl.request("GET", "/opportunities/pipelines", params={"locationId": location})
    if status != 200:
        print(json.dumps({"status": "error", "http": status, "error": body}, ensure_ascii=False))
        return 0
    pipelines = [
        {"id": p.get("id"), "name": p.get("name"),
         "stages": [s.get("name") for s in p.get("stages", [])]}
        for p in (body.get("pipelines", []) if isinstance(body, dict) else [])
    ]
    print(json.dumps({"status": "ok", "count": len(pipelines), "pipelines": pipelines}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
