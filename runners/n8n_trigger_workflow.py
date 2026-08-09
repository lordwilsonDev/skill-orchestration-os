"""Runner: n8n_trigger_workflow."""
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
    if len(sys.argv) < 2:
        print(json.dumps({"error": "missing workflow_id", "execution_id": None, "status": "error"}, ensure_ascii=False))
        return 2
    workflow_id = sys.argv[1]
    if not N8N_KEY:
        print(json.dumps({"execution_id": None, "status": "error", "error": "missing N8N_API_KEY"}, ensure_ascii=False))
        return 0
    import urllib.request
    url = f"{N8N_API}/workflows/{workflow_id}/executions"
    data = json.dumps({}).encode()
    req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {N8N_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except Exception as e:
        print(json.dumps({"execution_id": None, "status": "error", "error": str(e)}, ensure_ascii=False))
        return 0
    execution_id = body.get("id") if isinstance(body, dict) else None
    print(json.dumps({"execution_id": execution_id, "status": "created", "raw": body}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
