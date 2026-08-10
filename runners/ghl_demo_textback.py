"""Runner: ghl_demo_textback — WRITE. Live sales-demo of missed-call text-back.

On a call you say "what's your cell? watch —", run this with their number, and
their phone buzzes with the instant text-back a customer would get. Upserts the
number as a contact, then fires the demo SMS.

Input (argv[1] JSON): {phone, name?, businessName?, message?, confirm?: bool}
Safety: without "confirm": true it is a DRY RUN (no contact, no send). Use only
with the prospect's real-time consent on the call — do NOT batch/blast (carrier
compliance).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "runners"))
import _ghl  # noqa: E402

DEFAULT_MSG = (
    "\U0001F44B Sorry we missed your call — we'll be right back with you! "
    "(This instant text-back is what your customers get the moment you can't pick "
    "up — so that flooded-basement caller never reaches your voicemail. "
    "— BlackSwanLabz)"
)


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(json.dumps({"status": "error", "error": "missing input JSON (need phone)"}))
        return 2
    args = json.loads(sys.argv[1])
    token, location = _ghl.creds()
    if not token or not location:
        print(json.dumps({"status": "error", "error": "missing GHL_PIT_TOKEN or GHL_LOCATION_ID"}))
        return 0
    phone = args.get("phone")
    if not phone:
        print(json.dumps({"status": "error", "error": "need phone (E.164, e.g. +1920...)"}))
        return 2
    msg = args.get("message") or DEFAULT_MSG
    if args.get("businessName"):
        msg = msg.replace("BlackSwanLabz", args["businessName"])

    if not args.get("confirm"):
        print(json.dumps({"status": "dry_run",
                          "would": {"1_upsert_contact": {"phone": phone, "name": args.get("name")},
                                    "2_send_sms": {"to": phone, "message": msg}},
                          "note": "re-run with \"confirm\": true (use only with live consent)"},
                         ensure_ascii=False))
        return 0

    # 1) upsert contact for the number
    cbody = {"locationId": location, "phone": phone, "source": "BSL live demo"}
    if args.get("name"):
        cbody["name"] = args["name"]
    s1, r1 = _ghl.request("POST", "/contacts/upsert", body=cbody)
    if s1 not in (200, 201):
        print(json.dumps({"status": "error", "step": "upsert", "http": s1, "error": r1}, ensure_ascii=False))
        return 0
    cid = (r1.get("contact", r1) if isinstance(r1, dict) else {}).get("id")
    if not cid:
        print(json.dumps({"status": "error", "step": "upsert", "error": "no contact_id", "raw": r1}, ensure_ascii=False))
        return 0

    # 2) send the demo text-back
    s2, r2 = _ghl.request("POST", "/conversations/messages",
                          body={"type": "SMS", "contactId": cid, "message": msg})
    if s2 not in (200, 201):
        print(json.dumps({"status": "error", "step": "send", "http": s2, "error": r2}, ensure_ascii=False))
        return 0
    print(json.dumps({"status": "ok", "sent_to": phone, "contact_id": cid,
                      "message_id": r2.get("messageId") if isinstance(r2, dict) else None},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
