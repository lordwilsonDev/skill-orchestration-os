"""Runner: n8n_list_workflows."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SKILL_ROOT = Path.home() / ".hermes" / "skills" / "skill-orchestration-os"
sys.path.insert(0, str(SKILL_ROOT))

N8N_API = os.environ.get("N8N_API", "http://localhost:5678/api/v1")
N8N_KEY = os.environ.get("N8N_API_KEY", "")


def main() -> int:
    if not N8N_KEY:
        print(json.dumps({"workflows": [], "error": "missing N8N_API_KEY"}, ensure_ascii=False))
        return 0
    import urllib.request
    req = urllib.request.Request(f"{N8N_API}/workflows", headers={"Authorization": f"Bearer {N8N_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(json.dumps({"workflows": [], "error": str(e)}, ensure_ascii=False))
        return 0
    workflows = []
    for wf in data.get("data", []) if isinstance(data, dict) else data:
        workflows.append({"id": wf.get("id"), "name": wf.get("name"), "active": wf.get("active")})
    print(json.dumps({"workflows": workflows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
