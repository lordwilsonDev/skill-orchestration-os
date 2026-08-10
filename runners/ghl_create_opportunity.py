"""Runner: ghl_create_opportunity — WRITE. Adds a deal to a pipeline stage.

Input (argv[1] JSON): {name, pipelineId, pipelineStageId, contactId?,
                       monetaryValue?, status?("open"), confirm?: bool}
pipelineId / pipelineStageId come from ghl_list_pipelines.
Safety: without "confirm": true it is a DRY RUN — no API call.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "runners"))
import _ghl  # noqa: E402

REQUIRED = ("name", "pipelineId", "pipelineStageId")


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"status": "error", "error": f"missing input JSON (need {REQUIRED})"}))
        return 2
    args = json.loads(sys.argv[1])
    token, location = _ghl.creds()
    if not token or not location:
        print(json.dumps({"status": "error", "error": "missing GHL_PIT_TOKEN or GHL_LOCATION_ID"}))
        return 0
    missing = [k for k in REQUIRED if not args.get(k)]
    if missing:
        print(json.dumps({"status": "error", "error": f"missing required fields: {missing}"}))
        return 2

    body = {"locationId": location, "name": args["name"],
            "pipelineId": args["pipelineId"], "pipelineStageId": args["pipelineStageId"],
            "status": args.get("status", "open")}
    for k in ("contactId", "monetaryValue"):
        if args.get(k) is not None:
            body[k] = args[k]

    if not args.get("confirm"):
        print(json.dumps({"status": "dry_run", "would": {"method": "POST", "path": "/opportunities/",
                          "body": body}, "note": "re-run with \"confirm\": true to write"}, ensure_ascii=False))
        return 0

    status, resp = _ghl.request("POST", "/opportunities/", body=body)
    if status not in (200, 201):
        print(json.dumps({"status": "error", "http": status, "error": resp}, ensure_ascii=False))
        return 0
    o = resp.get("opportunity", resp) if isinstance(resp, dict) else {}
    print(json.dumps({"status": "ok", "opportunity_id": o.get("id"), "name": o.get("name")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
