"""Runner: ghl_send_message — WRITE. Sends an SMS or Email to a contact via the
GoHighLevel Conversations API. This is the funnel's outbound engine.

Input (argv[1] JSON): {contactId, type:"SMS"|"Email", message,
                       subject?(Email), confirm?: bool}
Safety: without "confirm": true it is a DRY RUN — no API call.
Note: real sending also requires a configured channel in GHL (a phone number for
SMS, a verified email sender for Email). The funnel audit reports whether those exist.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "runners"))
import _ghl  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"status": "error", "error": "missing input JSON (need contactId, type, message)"}))
        return 2
    args = json.loads(sys.argv[1])
    token, _ = _ghl.creds()
    if not token:
        print(json.dumps({"status": "error", "error": "missing GHL_PIT_TOKEN"}))
        return 0
    mtype = args.get("type", "SMS")
    for req in ("contactId", "message"):
        if not args.get(req):
            print(json.dumps({"status": "error", "error": f"missing required field: {req}"}))
            return 2

    # SMS uses `message`; Email uses `html` (+ `subject`). Wrong field => GHL 422
    # CONVERSATIONS_MSG_NO_CONTENT (found via a live test send).
    if mtype == "Email":
        body = {"type": "Email", "contactId": args["contactId"], "html": args["message"]}
        if args.get("subject"):
            body["subject"] = args["subject"]
    else:
        body = {"type": mtype, "contactId": args["contactId"], "message": args["message"]}

    if not args.get("confirm"):
        print(json.dumps({"status": "dry_run", "would": {"method": "POST", "path": "/conversations/messages",
                          "body": body}, "note": "re-run with \"confirm\": true to send"}, ensure_ascii=False))
        return 0

    status, resp = _ghl.request("POST", "/conversations/messages", body=body)
    if status not in (200, 201):
        print(json.dumps({"status": "error", "http": status, "error": resp}, ensure_ascii=False))
        return 0
    print(json.dumps({"status": "ok", "message_id": resp.get("messageId") if isinstance(resp, dict) else None,
                      "conversation_id": resp.get("conversationId") if isinstance(resp, dict) else None},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
