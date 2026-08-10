"""Runner: ghl_move_opportunity — WRITE. Advances a deal to a new stage/status.

Input (argv[1] JSON): {opportunityId, pipelineStageId?, status?, confirm?: bool}
Safety: without "confirm": true it is a DRY RUN — no API call.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "runners"))
import _ghl  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"status": "error", "error": "missing input JSON (need opportunityId)"}))
        return 2
    args = json.loads(sys.argv[1])
    token, _ = _ghl.creds()
    if not token:
        print(json.dumps({"status": "error", "error": "missing GHL_PIT_TOKEN"}))
        return 0
    opp = args.get("opportunityId")
    if not opp:
        print(json.dumps({"status": "error", "error": "need opportunityId"}))
        return 2
    if not (args.get("pipelineStageId") or args.get("status")):
        print(json.dumps({"status": "error", "error": "need pipelineStageId and/or status to change"}))
        return 2

    body = {}
    for k in ("pipelineStageId", "status"):
        if args.get(k) is not None:
            body[k] = args[k]

    if not args.get("confirm"):
        print(json.dumps({"status": "dry_run", "would": {"method": "PUT", "path": f"/opportunities/{opp}",
                          "body": body}, "note": "re-run with \"confirm\": true to write"}, ensure_ascii=False))
        return 0

    status, resp = _ghl.request("PUT", f"/opportunities/{opp}", body=body)
    if status not in (200, 201):
        print(json.dumps({"status": "error", "http": status, "error": resp}, ensure_ascii=False))
        return 0
    o = resp.get("opportunity", resp) if isinstance(resp, dict) else {}
    print(json.dumps({"status": "ok", "opportunity_id": o.get("id"),
                      "stage": o.get("pipelineStageId"), "state": o.get("status")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
