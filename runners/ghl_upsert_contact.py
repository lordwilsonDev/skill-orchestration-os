"""Runner: ghl_upsert_contact — WRITE (idempotent upsert by email/phone).

Input (argv[1] JSON): {email?, phone?, firstName?, lastName?, name?, companyName?,
                       tags?: [str], source?, confirm?: bool}
Safety: without "confirm": true it is a DRY RUN — prints the intended request and
makes NO API call.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "runners"))
import _ghl  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"status": "error", "error": "missing input JSON (need email or phone)"}))
        return 2
    args = json.loads(sys.argv[1])
    token, location = _ghl.creds()
    if not token or not location:
        print(json.dumps({"status": "error", "error": "missing GHL_PIT_TOKEN or GHL_LOCATION_ID"}))
        return 0
    if not (args.get("email") or args.get("phone")):
        print(json.dumps({"status": "error", "error": "need at least email or phone to upsert"}))
        return 2

    body = {"locationId": location}
    for k in ("email", "phone", "firstName", "lastName", "name", "companyName", "source", "tags"):
        if args.get(k) is not None:
            body[k] = args[k]

    if not args.get("confirm"):
        print(json.dumps({"status": "dry_run", "would": {"method": "POST", "path": "/contacts/upsert",
                          "body": body}, "note": "re-run with \"confirm\": true to write"}, ensure_ascii=False))
        return 0

    status, resp = _ghl.request("POST", "/contacts/upsert", body=body)
    if status not in (200, 201):
        print(json.dumps({"status": "error", "http": status, "error": resp}, ensure_ascii=False))
        return 0
    c = resp.get("contact", resp) if isinstance(resp, dict) else {}
    print(json.dumps({"status": "ok", "contact_id": c.get("id"),
                      "new": resp.get("new") if isinstance(resp, dict) else None,
                      "email": c.get("email")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
