"""Runner: ghl_list_contacts — read-only. Lists contacts in the sub-account.
Input (argv[1], optional JSON): {"limit": int, "query": str}. No side effects."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "runners"))
import _ghl  # noqa: E402


def main() -> int:
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].strip() else {}
    token, location = _ghl.creds()
    if not token or not location:
        print(json.dumps({"status": "error", "error": "missing GHL_PIT_TOKEN or GHL_LOCATION_ID"}))
        return 0
    params = {"locationId": location, "limit": args.get("limit", 20)}
    if args.get("query"):
        params["query"] = args["query"]
    status, body = _ghl.request("GET", "/contacts/", params=params)
    if status != 200:
        print(json.dumps({"status": "error", "http": status, "error": body}, ensure_ascii=False))
        return 0
    contacts = [
        {"id": c.get("id"), "name": c.get("contactName") or c.get("name"),
         "email": c.get("email"), "company": c.get("companyName")}
        for c in (body.get("contacts", []) if isinstance(body, dict) else [])
    ]
    total = body.get("meta", {}).get("total") if isinstance(body, dict) else None
    print(json.dumps({"status": "ok", "total": total, "returned": len(contacts),
                      "contacts": contacts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
