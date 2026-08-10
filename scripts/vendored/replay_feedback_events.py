#!/usr/bin/env python3
"""replay_feedback_events.py — the TASK_FAILED replay consumer (producer adapter).

Reads the reality-loop's TASK_FAILED event stream
(skill-orchestration-os/logs/feedback_events.jsonl) and feeds the ledger's
claim registry as NEGATIVE evidence (polarity CONTRADICTING) — the
sovereign-verification blueprint's "negative evidence is first-class"
invariant made operational.

Every failure event becomes:
  1. a derived claim  — "<subject> completes successfully" (the claim reality
     contradicted), registered in the ledger claim registry (ledger/claims.json)
     with a deterministic verdict:
       - previously VERIFIED/SUPPORTED/VALIDATED/REGRESSED  -> REGRESSED
         (historical green + fresh contradicting evidence)
       - previously CONTESTED                               -> CONTESTED
         (supporting and contradicting evidence still coexist)
       - otherwise                                         -> UNVERIFIED
         (negative evidence preserved; nothing was ever supported)
  2. a canonical evidence artifact — ledger/evidence/negative/<ts>_<ev_id>.json
     with polarity CONTRADICTING and full provenance (execution / environment /
     input / verifier / dependency), content-hashed so tampering is detectable.

Idempotent by design: a replay cursor (ledger/replay_cursor.json) records every
processed event identity (ts + task_id + failed_step); re-runs skip them, so
the consumer is safe to run daily or after every event write.

Zero-spend and deterministic: stdlib-only, no LLM, no network.

CLI:
  --events PATH     the TASK_FAILED jsonl stream (default: the canonical
                    skill-orchestration-os logs/feedback_events.jsonl)
  --ledger-dir DIR  ledger root (default: <skill>/ledger)
  --git-head SHA    bind the artifacts to a git identity (default: unknown)
  --dry-run         print the ingest plan, write nothing
  --self-test       in-memory synthetic events -> temp ledger; asserts the
                    full path (ingest, dedupe, verdicts, cursor). Exit 0/1.

Exit codes: 0 = success (including "nothing to replay" — an absent stream
is not an incident); 1 = any malformed line or ledger write failure (a
corrupt event stream is itself a ledger incident — we fail loud, never
silently replay the clean prefix).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

TOOLCHAIN = "sovereign-verification/replay_feedback_events"
TOOL_VERSION = "1.0"
EVENT_TYPE = "TASK_FAILED"
POLARITY_CONTRADICTING = "CONTRADICTING"
TIER = "T3"  # real execution produced the failure — EXECUTED-level evidence

# Claim ids that were supported at some point; fresh negative evidence against
# them means REGRESSED (historical green + current red), per the blueprint.
_PREVIOUSLY_VERIFIED = ("VERIFIED", "SUPPORTED", "VALIDATED", "REGRESSED")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize(name: str) -> str:
    return "".join("_" if ch in ":/+\\ " else ch for ch in name)


def _canonical_event_text(ev: dict) -> str:
    """Canonical serialization of an event for hashing/deduping."""
    return json.dumps(ev, sort_keys=True, ensure_ascii=False)


def event_identity(ev: dict) -> str:
    """Stable dedupe identity: the content hash of the canonical event.

    Content-addressed rather than (ts|task_id|failed_step): two distinct
    failures of the same task/step within the same second — and router
    events, which carry no task id — would collide under a timestamp key,
    silently conflating distinct evidence. Identical content is a true
    duplicate (same ts, same everything); different content is a distinct
    event, regardless of when it was written.
    """
    return _sha256(_canonical_event_text(ev))


def derive_claim(ev: dict) -> Dict[str, str]:
    """Deterministic claim derivation from a failure event.

    subject: task:<task_id> when the event has a task id (reality loop);
             otherwise the failed step (router dispatch failures carry no
             task id, so the subject is the failed route itself).
    """
    task_id = ev.get("task_id")
    subject = f"task:{task_id}" if task_id else str(ev.get("failed_step") or "unknown")
    claim_id = f"claim:ok:{subject}"
    text = f'"{subject}" completes without failure'
    return {"subject": subject, "claim_id": claim_id, "text": text}


def build_evidence_artifact(ev: dict, claim: Dict[str, str], events_source: str,
                            git_head: str) -> Dict[str, Any]:
    """The blueprint §8 canonical evidence object, polarity CONTRADICTING."""
    event_hash = _sha256(_canonical_event_text(ev))
    evidence_id = f"ev_{event_hash[:12]}"
    dag = ev.get("dag") or []
    revised = ev.get("revised_dag")
    return {
        "evidence_id": evidence_id,
        "subject_id": claim["subject"],
        "claim_id": claim["claim_id"],
        "evidence_type": "task_failed",
        "polarity": POLARITY_CONTRADICTING,
        "git_head": git_head,
        "artifact_hash": event_hash[:16],
        "toolchain": TOOLCHAIN,
        "timestamp": ev.get("ts", ""),
        "result": "FAIL",
        "provenance": {
            "execution": {
                "decision": ev.get("decision"),
                "attempt": ev.get("attempt"),
                "max_attempts": ev.get("max_attempts"),
                "revised_dag_count": len(revised) if revised is not None else None,
            },
            "environment": {"events_source": events_source},
            "input": {
                "goal": ev.get("goal"),
                "failed_step": ev.get("failed_step"),
                "failed_index": ev.get("failed_index"),
                "error": ev.get("error"),
                "dag": dag,
            },
            "verifier": {"tool": TOOLCHAIN, "version": TOOL_VERSION},
            "dependency": {"event_version": ev.get("version")},
        },
        "freshness": "FRESH",
    }


def _empty_registry() -> Dict[str, Any]:
    return {"claims": [], "generated_at": None}


def load_registry(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _empty_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_registry()
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        return _empty_registry()
    return data


def load_cursor(path: Path) -> set:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("processed", []))
    except (json.JSONDecodeError, OSError, AttributeError):
        return set()


def _verdict_for(prior: Optional[dict]) -> str:
    if not prior:
        return "UNVERIFIED"
    prior_verdict = str(prior.get("verdict", "UNVERIFIED"))
    if prior_verdict in _PREVIOUSLY_VERIFIED:
        return "REGRESSED"
    if prior_verdict == "CONTESTED":
        return "CONTESTED"
    return "UNVERIFIED"


def ingest(events_path: Path, ledger_dir: Path, git_head: str,
           dry_run: bool = False) -> Dict[str, Any]:
    """Replay the event stream into the ledger. Returns a run summary.

    An ABSENT stream is not an incident (nothing has failed yet) — status
    "no_events", no errors, exit 0 upstream. A MALFORMED stream is an
    incident — status "error", and nothing is ingested."""
    if not events_path.exists():
        return {"status": "no_events", "events": 0, "ingested": 0, "skipped": 0,
                "errors": [], "claims": 0}
    lines = events_path.read_text(encoding="utf-8").splitlines()
    events: List[dict] = []
    errors: List[str] = []
    for i, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {i}: malformed event: {exc}")
            continue
        if ev.get("event") != EVENT_TYPE:
            continue  # only TASK_FAILED events are negative evidence here
        events.append(ev)
    if errors:
        # A corrupt stream is a ledger incident — fail loud, never silently
        # replay the clean prefix as if nothing happened. (If we swallowed
        # malformed lines, a torn write could silently erase evidence.)
        return {"status": "error", "events": len(events), "ingested": 0,
                "skipped": 0, "errors": errors, "claims": 0}

    return _ingest_valid(events, events_path, ledger_dir, git_head, dry_run)


def _ingest_valid(events: List[dict], events_path: Path, ledger_dir: Path,
                  git_head: str, dry_run: bool) -> Dict[str, Any]:
    """Ingest a parsed, validated event list into the ledger (idempotent)."""
    if not events:
        return {"status": "no_new_events", "events": 0, "ingested": 0,
                "skipped": 0, "errors": [], "claims": 0}

    # Load ledger state (cursor + registry). In dry-run these are loaded read-only.
    cursor_file = ledger_dir / "replay_cursor.json"
    claims_file = ledger_dir / "claims.json"
    negative_dir = ledger_dir / "evidence" / "negative"
    processed = load_cursor(cursor_file)
    registry = load_registry(claims_file)
    by_id = {c.get("claim_id"): c for c in registry["claims"]}

    ingested = 0
    skipped = 0
    for ev in events:
        ident = event_identity(ev)
        if ident in processed:
            skipped += 1
            continue
        claim = derive_claim(ev)
        artifact = build_evidence_artifact(ev, claim, str(events_path), git_head)

        if not dry_run:
            negative_dir.mkdir(parents=True, exist_ok=True)
            ts = _sanitize(str(ev.get("ts", "")))
            out = negative_dir / f"{ts}_{artifact['evidence_id']}.json"
            if not out.exists():
                out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

        prior = by_id.get(claim["claim_id"])
        verdict = _verdict_for(prior)
        entry = {
            "claim_id": claim["claim_id"],
            "subject": claim["subject"],
            "text": claim["text"],
            "verification_tier": TIER,
            "verdict": verdict,
            "negative_evidence": sorted({*(prior or {}).get("negative_evidence", []), artifact["evidence_id"]}),
            # First-ever negative evidence on a previously-clean claim must
            # record the event ts, not stay null (or falls back correctly).
            "first_negative_evidence_at": (prior or {}).get("first_negative_evidence_at") or ev.get("ts"),
            "last_negative_evidence_at": ev.get("ts"),
        }
        if prior is None:
            registry["claims"].append(entry)
        else:
            by_id[claim["claim_id"]] = entry
            idx = next(i for i, c in enumerate(registry["claims"]) if c.get("claim_id") == claim["claim_id"])
            registry["claims"][idx] = entry
        processed.add(ident)
        ingested += 1

    if not dry_run:
        registry["claims"].sort(key=lambda c: c.get("claim_id", ""))
        registry["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        claims_file.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        cursor_file.write_text(
            json.dumps({"processed": sorted(processed), "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2) + "\n",
            encoding="utf-8")

    return {"status": "ok" if ingested else "no_new_events",
            "events": len(events), "ingested": ingested, "skipped": skipped,
            "errors": [], "claims": len(registry["claims"])}


def _run_self_test() -> int:
    """Zero-spend in-memory verification of the full ingest path."""
    with tempfile.TemporaryDirectory(prefix="replay-self-test-") as tmp:
        root = Path(tmp)
        events = root / "feedback_events.jsonl"
        events.write_text("\n".join([
            json.dumps({
                "event": "TASK_FAILED", "version": "1.0", "task_id": "task_aaa1",
                "goal": "ship the release", "failed_step": "release", "failed_index": 0,
                "error": "verify contains 'green'", "attempt": 1, "max_attempts": 3,
                "decision": "REPLAN", "dag": [{"skill": "release", "args": {}}],
                "revised_dag": [{"skill": "release", "args": {"force": True}}],
                "ts": "2026-08-10T12:00:00+00:00",
            }),
            json.dumps({
                "event": "TASK_FAILED", "version": "1.0", "task_id": None,
                "goal": "scrape v2ex", "failed_step": "route:v2ex/scrape", "failed_index": 0,
                "error": "dispatch exit 1", "attempt": 1, "max_attempts": 1,
                "decision": "BLOCK", "dag": [{"skill": "route", "args": {"task": "scrape v2ex"}}],
                "revised_dag": None, "ts": "2026-08-10T12:05:00+00:00",
            }),
            # duplicate of the first event — must be skipped on replay
            json.dumps({
                "event": "TASK_FAILED", "version": "1.0", "task_id": "task_aaa1",
                "goal": "ship the release", "failed_step": "release", "failed_index": 0,
                "error": "verify contains 'green'", "attempt": 1, "max_attempts": 3,
                "decision": "REPLAN", "dag": [{"skill": "release", "args": {}}],
                "revised_dag": [{"skill": "release", "args": {"force": True}}],
                "ts": "2026-08-10T12:00:00+00:00",
            }),
        ]) + "\n")
        ledger = root / "ledger"

        first = ingest(events, ledger, "deadbeef")
        assert first["status"] == "ok", first
        assert first["ingested"] == 2, first
        assert first["claims"] == 2, first

        claims = json.loads((ledger / "claims.json").read_text())
        assert len(claims["claims"]) == 2, claims
        by_id = {c["claim_id"]: c for c in claims["claims"]}
        release = by_id["claim:ok:task:task_aaa1"]
        assert release["verdict"] == "UNVERIFIED", release
        assert len(release["negative_evidence"]) == 1, release
        route = by_id["claim:ok:route:v2ex/scrape"]
        assert route["verdict"] == "UNVERIFIED", route
        assert route["verification_tier"] == "T3", route

        negative = list((ledger / "evidence" / "negative").glob("*.json"))
        assert len(negative) == 2, negative
        art = json.loads(negative[0].read_text())
        assert art["polarity"] == "CONTRADICTING", art
        assert art["evidence_type"] == "task_failed", art
        assert art["provenance"]["input"]["error"], art
        assert len(art["artifact_hash"]) == 16, art
        assert art["provenance"]["execution"]["decision"] in ("REPLAN", "BLOCK"), art

        # Idempotency: replaying the same stream ingests nothing new — all 3
        # lines (2 unique events + the duplicate, which shares event 1's
        # identity) are already in the cursor.
        second = ingest(events, ledger, "deadbeef")
        assert second["ingested"] == 0, second
        assert second["skipped"] == 3, second
        assert len(claims["claims"]) == 2  # unchanged

        # REGRESSED: a previously-supported claim hit by fresh negative evidence.
        registry = load_registry(ledger / "claims.json")
        registry["claims"].append({
            "claim_id": "claim:ok:task:task_bbb2", "subject": "task:task_bbb2",
            "text": '"task:task_bbb2" completes without failure',
            "verification_tier": "T3", "verdict": "VERIFIED",
            "negative_evidence": [], "first_negative_evidence_at": None,
            "last_negative_evidence_at": None,
        })
        (ledger / "claims.json").write_text(json.dumps(registry, indent=2) + "\n")
        (ledger / "replay_cursor.json").unlink()  # fresh cursor so the event is new
        ev2 = {
            "event": "TASK_FAILED", "version": "1.0", "task_id": "task_bbb2",
            "goal": "keep the service up", "failed_step": "uptime", "failed_index": 0,
            "error": "timeout", "attempt": 3, "max_attempts": 3, "decision": "BLOCK",
            "dag": [], "revised_dag": None, "ts": "2026-08-10T13:00:00+00:00",
        }
        (ledger / "replay_cursor.json").write_text(json.dumps({"processed": []}))
        events2 = root / "events2.jsonl"
        events2.write_text(json.dumps(ev2) + "\n")
        third = ingest(events2, ledger, "cafebabe")
        assert third["ingested"] == 1, third
        updated = {c["claim_id"]: c for c in load_registry(ledger / "claims.json")["claims"]}
        assert updated["claim:ok:task:task_bbb2"]["verdict"] == "REGRESSED", updated
        assert updated["claim:ok:task:task_bbb2"]["verification_tier"] == "T3"
        # First-ever negative evidence on a clean claim records the event ts.
        assert updated["claim:ok:task:task_bbb2"]["first_negative_evidence_at"] == ev2["ts"], updated

        # CONTESTED stays CONTESTED under more negative evidence: seed a REAL
        # prior registry (the file written by the REGRESSED ingest) with a
        # CONTESTED claim that already carries evidence, then replay a new
        # failure for the same subject and assert the evidence accumulates
        # while the verdict holds and first-seen is preserved.
        seeded = load_registry(ledger / "claims.json")
        seeded["claims"].append({
            "claim_id": "claim:ok:task:task_ccc3", "subject": "task:task_ccc3",
            "text": '"task:task_ccc3" completes without failure',
            "verification_tier": "T3", "verdict": "CONTESTED",
            "negative_evidence": ["ev_prior"],
            "first_negative_evidence_at": "2026-08-10T10:00:00+00:00",
            "last_negative_evidence_at": "2026-08-10T10:00:00+00:00",
        })
        (ledger / "claims.json").write_text(json.dumps(seeded, indent=2) + "\n")
        (ledger / "replay_cursor.json").write_text(json.dumps({"processed": []}))
        ev3 = dict(ev2, ts="2026-08-10T13:30:00+00:00", task_id="task_ccc3")
        ev3["goal"] = "keep the queue healthy"
        events3 = root / "events3.jsonl"
        events3.write_text(json.dumps(ev3) + "\n")
        fourth = ingest(events3, ledger, "deadbeef")
        assert fourth["ingested"] == 1, fourth
        c3 = {c["claim_id"]: c for c in load_registry(ledger / "claims.json")["claims"]}["claim:ok:task:task_ccc3"]
        assert c3["verdict"] == "CONTESTED", c3
        assert "ev_prior" in c3["negative_evidence"], c3          # accumulation preserved
        assert c3["first_negative_evidence_at"] == "2026-08-10T10:00:00+00:00", c3
        assert c3["last_negative_evidence_at"] == ev3["ts"], c3

        # Same-second distinct failures for the same subject must NOT conflate
        # (the old ts|task|step identity would have skipped the second one).
        events_same_sec = root / "events_same_sec.jsonl"
        ev_s1 = dict(ev2, task_id="task_eee5", error="timeout v1", ts="2026-08-10T13:40:00+00:00")
        ev_s2 = dict(ev2, task_id="task_eee5", error="timeout v2", ts="2026-08-10T13:40:00+00:00")
        events_same_sec.write_text(json.dumps(ev_s1) + "\n" + json.dumps(ev_s2) + "\n")
        same_sec = ingest(events_same_sec, root / "ledger_samesec", "x")
        assert same_sec["ingested"] == 2, same_sec

        # Malformed line -> fail loud (exit path via error summary).
        events_bad = root / "events_bad.jsonl"
        events_bad.write_text("{\"event\": \"TASK_FAILED\", \"ts\": \"x\"}\nnot-json\n")
        bad = ingest(events_bad, root / "ledger_bad", "x")
        assert bad["status"] == "error" and bad["errors"], bad

        # Absent stream is NOT an incident: status no_events, no errors, so
        # the CLI exits 0 (the canonical stream doesn't exist until the first
        # failure — a daily gate must not fail on "nothing to replay").
        absent = ingest(root / "does_not_exist.jsonl", root / "ledger_absent", "x")
        assert absent["status"] == "no_events" and not absent["errors"], absent

        # Dry-run writes nothing.
        events4 = root / "events4.jsonl"
        events4.write_text(json.dumps(dict(ev2, task_id="task_ddd4", ts="2026-08-10T14:00:00+00:00")) + "\n")
        ledger_dry = root / "ledger_dry"
        dry = ingest(events4, ledger_dry, "x", dry_run=True)
        assert dry["ingested"] == 1, dry
        assert not (ledger_dry / "claims.json").exists(), "dry-run must write nothing"

    print("self-test: PASS (ingest, dedupe incl. same-second distinct, UNVERIFIED/REGRESSED/CONTESTED verdicts, T3 tier, accumulation, malformed fail-loud, dry-run, no-events exit 0)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="replay_feedback_events.py",
        description="Replay TASK_FAILED events into the ledger as negative (CONTRADICTING) evidence.",
    )
    parser.add_argument("--events", type=Path,
                        default=Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "logs" / "feedback_events.jsonl",
                        help="TASK_FAILED event stream (jsonl)")
    parser.add_argument("--ledger-dir", type=Path, default=None,
                        help="ledger root (default: <skill>/ledger)")
    parser.add_argument("--git-head", default="unknown", help="git identity to bind artifacts to")
    parser.add_argument("--dry-run", action="store_true", help="print the ingest plan, write nothing")
    parser.add_argument("--self-test", action="store_true", help="run the in-memory verification and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    ledger_dir = args.ledger_dir or (Path(__file__).resolve().parent.parent / "ledger")
    summary = ingest(args.events, ledger_dir, args.git_head, dry_run=args.dry_run)

    if summary["errors"]:
        print("replay_feedback_events: FAILED")
        for err in summary["errors"]:
            print(f"  error: {err}")
        return 1
    if summary["status"] == "no_events":
        # The absent-stream branch must still honor --dry-run: a caller (or
        # gate leg) that dry-runs before the first failure exists must see the
        # marker, not a bare "nothing to replay" — the smoke leg asserts it.
        print(f"replay_feedback_events: no event stream at {args.events} (nothing to replay)"
              + (" [dry-run — nothing written]" if args.dry_run else ""))
        return 0
    print(f"replay_feedback_events: {summary['ingested']} ingested, {summary['skipped']} already-replayed "
          f"of {summary['events']} events; claims in registry: {summary['claims']}"
          + (" [dry-run — nothing written]" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
