"""Runner: ghl_delete_contact — WRITE / DESTRUCTIVE. Permanently deletes a
GoHighLevel contact (GHL has no archive/undo for contacts).

Input (argv[1] JSON): {contactId, confirm?: bool}
Safety: without "confirm": true it is a DRY RUN — no API call. Deletion is
irreversible; the confirm flag must be set by a human decision, not defaulted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "runners"))
import _ghl  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"status": "error", "error": "missing input JSON (need contactId)"}))
        return 2
    args = json.loads(sys.argv[1])
    token, _ = _ghl.creds()
    if not token:
        print(json.dumps({"status": "error", "error": "missing GHL_PIT_TOKEN"}))
        return 0
    cid = args.get("contactId")
    if not cid:
        print(json.dumps({"status": "error", "error": "need contactId"}))
        return 2

    if not args.get("confirm"):
        print(json.dumps({"status": "dry_run", "would": {"method": "DELETE", "path": f"/contacts/{cid}"},
                          "warning": "PERMANENT delete, no undo", "note": "re-run with \"confirm\": true"},
                         ensure_ascii=False))
        return 0

    status, resp = _ghl.request("DELETE", f"/contacts/{cid}")
    if status not in (200, 201):
        print(json.dumps({"status": "error", "http": status, "error": resp}, ensure_ascii=False))
        return 0
    print(json.dumps({"status": "ok", "deleted": cid}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
