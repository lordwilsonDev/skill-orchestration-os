#!/usr/bin/env python3
"""replay_feedback_events.py — the ledger's producer adapters (replay consumers).

Feeds producer output into the claim registry as first-class evidence. Two
producers, one ledger, one cursor:

  PRODUCER 1 — the reality loop's TASK_FAILED event stream
  (skill-orchestration-os/logs/feedback_events.jsonl). Every failure event
  becomes a derived claim ("<subject> completes without failure") plus a
  canonical evidence artifact, polarity CONTRADICTING, under
  ledger/evidence/negative/ — negative evidence is first-class.

  PRODUCER 2 — the capability-composer probes (live + write).
  (~/capability-composer/evidence/live/ and .../evidence/write/). Every
  probe artifact is ALREADY a §8-shaped evidence object (the probe writes the
  canonical contract): its claim_id (e.g. ghl.live.reads_work for the live
  probe, ghl.live.writes_work for the write probe) is registered verbatim,
  its polarity drives the verdict (SUPPORTING → VERIFIED, CONTRADICTING →
  REGRESSED, both → CONTESTED), its content hash is VERIFIED against the
  artifact's own artifact_hash (tamper detection — the ledger must be harder
  to fool than the claims it evaluates), and it is stored under
  ledger/evidence/<evidence_type>/ — live probes under evidence/live/,
  write probes under evidence/write/, never conflated. The write probe is
  the sandbox-account round trip (create → verify → delete → verify-gone);
  both producers share the exact same §8 contract.

Deterministic verdicts (shared transition policy):
  - fresh CONTRADICTING on a previously-supported claim -> REGRESSED
    (historical green + fresh contradicting evidence)
  - fresh SUPPORTING on a previously-contradicted claim -> CONTESTED
    (supporting and contradicting evidence now coexist)
  - SUPPORTING with no contradiction -> VERIFIED (valid evidence exists)
  - CONTRADICTING with no support history -> UNVERIFIED
    (negative evidence preserved; nothing was ever supported)

Idempotent by design: a replay cursor (ledger/replay_cursor.json) records
processed identities (event content hashes, namespaced probe evidence ids);
re-runs skip them, so the consumer is safe to run daily or after every write.

Zero-spend and deterministic: stdlib-only, no LLM, no network.

CLI:
  --events PATH       the TASK_FAILED jsonl stream (default: the canonical
                      skill-orchestration-os logs/feedback_events.jsonl)
  --probe-evidence DIR  a probe §8 artifact dir — live (default
                      ~/capability-composer/evidence/live) or write
                      (~/capability-composer/evidence/write)
  --ledger-dir DIR    ledger root (default: <skill>/ledger)
  --git-head SHA      bind the artifacts to a git identity (default: unknown)
  --dry-run           print the ingest plan, write nothing
  --self-test         in-memory synthetic events + probe artifacts -> temp
                      ledger; asserts the full path (ingest, dedupe, verdicts,
                      tamper detection, cursor). Exit 0/1.

Exit codes: 0 = success (including "nothing to replay" — an absent stream
or probe dir is not an incident); 1 = any malformed line/artifact or ledger
write failure (a corrupt input is itself a ledger incident — we fail loud,
never silently ingest the clean prefix); 2 = bad arguments.
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
TOOL_VERSION = "1.1"
EVENT_TYPE = "TASK_FAILED"
POLARITY_CONTRADICTING = "CONTRADICTING"
POLARITY_SUPPORTING = "SUPPORTING"
PROBE_EVIDENCE_TYPES = ("live_probe", "write_probe")
# Two probe producers, one §8 contract: the live probe (read-only) and the
# write probe (sandbox round trip). Both write the same canonical artifact
# shape with explicit polarity; the consumer accepts BOTH — a write-probe
# artifact is never silently discarded for not being a live-probe one.
# Evidence mirrors the producer dirs: live probes under ledger/evidence/live/,
# write probes under ledger/evidence/write/.
PROBE_EVIDENCE_DIRS = {"live_probe": "live", "write_probe": "write"}
TIER = "T3"  # real execution produced the failure — EXECUTED-level evidence
PROBE_TIER = "T4"  # live probe = executed against the real external system — INTEGRATED-level evidence
DEFAULT_PROBE_DIR = Path.home() / "capability-composer" / "evidence" / "live"

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


def _verdict_for_polarity(polarity: str, prior: Optional[dict]) -> str:
    """Transition policy for a fresh evidence object with an explicit polarity.

    SUPPORTING on an uncontested claim -> VERIFIED (valid evidence exists); on
    a claim that already carries contradiction (REGRESSED or CONTESTED, i.e.
    negative_evidence is non-empty) -> CONTESTED — support and contradiction
    COEXIST, the ledger never prefers one side. CONTRADICTING follows the
    negative path exactly (REGRESSED from support history, CONTESTED stays
    CONTESTED, else UNVERIFIED). Keying on the evidence lists, not the prior
    verdict string, keeps REGRESSED honest: REGRESSED is in
    _PREVIOUSLY_VERIFIED, so a verdict-string check would wrongly re-elevate.
    """
    if polarity == POLARITY_SUPPORTING:
        prior_negative = (prior or {}).get("negative_evidence") or []
        if prior_negative:
            return "CONTESTED"  # support + contradiction coexist
        return "VERIFIED"  # first or further valid supporting evidence
    return _verdict_for(prior)


def probe_identity(artifact: dict) -> str:
    """Dedupe identity for a probe artifact: its own evidence_id, namespaced
    so it can never collide with event content-hash identities. The evidence_id
    itself is globally unique across producers (ev_live_* vs ev_write_*), so
    the namespace prefix only needs to be distinct from event hashes — not per
    probe type."""
    return f"probe:{artifact.get('evidence_id', '')}"


def verify_probe_hash(artifact: dict) -> bool:
    """Content-hash verification — the §8 artifact's artifact_hash must equal
    the sha256 of the artifact with the hash field blanked (the capability-
    composer probe's own convention). A mismatch means the evidence was
    tampered with after production: fail loud, never ingest."""
    payload = dict(artifact)
    payload["artifact_hash"] = ""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest() == str(artifact.get("artifact_hash", ""))


def derive_probe_claim(artifact: dict) -> Dict[str, str]:
    """The probe artifact declares its own claim — register it verbatim.

    claim_id = the artifact's claim_id (e.g. ghl.live.reads_work); subject =
    its subject_id (e.g. ghl.live.contacts.search). Unlike TASK_FAILED
    events, which derive claims, the probe is already the canonical §8
    evidence object with the claim it stands for.
    """
    subject = str(artifact.get("subject_id") or artifact.get("claim_id") or "unknown")
    claim_id = str(artifact.get("claim_id") or f"claim:ok:{subject}")
    text = f'"{subject}" works against the live API'
    return {"subject": subject, "claim_id": claim_id, "text": text}


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
            # The contradiction must COEXIST with what was historically
            # supported — a REGRESSED claim shows WHAT it regressed from.
            # Carry the prior support linkage forward; never silently drop it
            # (blueprint: contradictory evidence is never silently discarded).
            # (cross-producer test finding: the entry used to drop these keys)
            "supporting_evidence": sorted(set((prior or {}).get("supporting_evidence", []))),
            "inconclusive_evidence": sorted(set((prior or {}).get("inconclusive_evidence", []))),
            "negative_evidence": sorted({*(prior or {}).get("negative_evidence", []), artifact["evidence_id"]}),
            # First-ever negative evidence on a previously-clean claim must
            # record the event ts, not stay null (or falls back correctly).
            "first_negative_evidence_at": (prior or {}).get("first_negative_evidence_at") or ev.get("ts"),
            "last_negative_evidence_at": ev.get("ts"),
        }
        if prior and prior.get("first_supporting_evidence_at"):
            entry["first_supporting_evidence_at"] = prior.get("first_supporting_evidence_at")
            entry["last_supporting_evidence_at"] = prior.get("last_supporting_evidence_at")
        # Keep the in-run view fresh: two distinct failures of the SAME task
        # in one batch must see each other's entries, or the second verdict
        # would ignore the first (same-shape bug fixed in the probe adapter).
        if prior is None:
            registry["claims"].append(entry)
        else:
            idx = next(i for i, c in enumerate(registry["claims"]) if c.get("claim_id") == claim["claim_id"])
            registry["claims"][idx] = entry
        by_id[claim["claim_id"]] = entry
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


def ingest_probe_evidence(probe_dir: Path, ledger_dir: Path, git_head: str,
                          dry_run: bool = False) -> Dict[str, Any]:
    """Ingest capability-composer probe §8 artifacts into the ledger.

    Both probe producers feed here: the live probe (evidence/live/) and the
    write probe (evidence/write/). Each artifact is already the canonical §8
    evidence object; the claim it declares is registered verbatim, its
    polarity drives the verdict, and its content hash is verified before
    ingestion. Evidence is stored under ledger/evidence/<evidence_type>/ so
    live and write probes stay side-by-side, never conflated.

    An ABSENT dir is not an incident (no probe has run yet) — status
    "no_probe_evidence", no errors, exit 0 upstream. A MALFORMED or TAMPERED
    artifact is an incident — status "error", and nothing is ingested (fail
    loud, never silently ingest the clean prefix).

    Each artifact is already the canonical §8 evidence object: the claim it
    declares is registered verbatim (claim_id from the artifact), its
    polarity drives the verdict, and its own content hash is verified before
    ingestion (tamper detection).
    """
    if not probe_dir.exists():
        return {"status": "no_probe_evidence", "artifacts": 0, "ingested": 0,
                "skipped": 0, "errors": [], "claims": 0}
    paths = sorted(probe_dir.glob("*.json"))
    artifacts: List[dict] = []
    errors: List[str] = []
    for path in paths:
        try:
            art = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{path.name}: malformed artifact: {exc}")
            continue
        if art.get("evidence_type") not in PROBE_EVIDENCE_TYPES:
            continue  # not a probe artifact — not this producer's contract
        if not verify_probe_hash(art):
            errors.append(f"{path.name}: artifact_hash mismatch — tampering detected")
            continue
        # §8 contract validation: an unknown polarity or a missing evidence_id
        # would silently misclassify the claim (any non-SUPPORTING value feeds
        # the negative path) or collapse dedupe (identity "live:"). Both are
        # contract violations — fail loud, never ingest.
        polarity = str(art.get("polarity", ""))
        if polarity not in (POLARITY_SUPPORTING, POLARITY_CONTRADICTING):
            errors.append(f"{path.name}: unknown polarity {polarity!r} — must be "
                          "SUPPORTING or CONTRADICTING")
            continue
        if not art.get("evidence_id"):
            errors.append(f"{path.name}: missing evidence_id — §8 contract violation")
            continue
        artifacts.append(art)
    if errors:
        return {"status": "error", "artifacts": len(artifacts), "ingested": 0,
                "skipped": 0, "errors": errors, "claims": 0}
    if not artifacts:
        return {"status": "no_new_artifacts", "artifacts": 0, "ingested": 0,
                "skipped": 0, "errors": [], "claims": 0}

    cursor_file = ledger_dir / "replay_cursor.json"
    claims_file = ledger_dir / "claims.json"
    processed = load_cursor(cursor_file)
    registry = load_registry(claims_file)
    by_id = {c.get("claim_id"): c for c in registry["claims"]}

    ingested = 0
    skipped = 0
    for art in artifacts:
        ident = probe_identity(art)
        if ident in processed:
            skipped += 1
            continue
        claim = derive_probe_claim(art)
        polarity = str(art.get("polarity", ""))
        ts = str(art.get("timestamp", ""))
        evidence_id = str(art["evidence_id"])

        if not dry_run:
            kind_dir = ledger_dir / "evidence" / PROBE_EVIDENCE_DIRS.get(
                str(art.get("evidence_type", "probe")), "probe")
            kind_dir.mkdir(parents=True, exist_ok=True)
            out = kind_dir / f"{evidence_id}.json"
            if not out.exists():
                out.write_text(json.dumps(art, indent=2) + "\n", encoding="utf-8")

        prior = by_id.get(claim["claim_id"])
        verdict = _verdict_for_polarity(polarity, prior)
        entry = {
            "claim_id": claim["claim_id"],
            "subject": claim["subject"],
            "text": claim["text"],
            "verification_tier": PROBE_TIER,
            "verdict": verdict,
            "supporting_evidence": sorted({
                *(prior or {}).get("supporting_evidence", []),
                *([evidence_id] if polarity == POLARITY_SUPPORTING else []),
            }),
            "inconclusive_evidence": sorted(set((prior or {}).get("inconclusive_evidence", []))),
            "negative_evidence": sorted({
                *(prior or {}).get("negative_evidence", []),
                *([evidence_id] if polarity == POLARITY_CONTRADICTING else []),
            }),
            "first_supporting_evidence_at": (
                (prior or {}).get("first_supporting_evidence_at")
                or (ts if polarity == POLARITY_SUPPORTING else None)
            ),
            "last_supporting_evidence_at": (
                ts if polarity == POLARITY_SUPPORTING
                else (prior or {}).get("last_supporting_evidence_at")
            ),
            "first_negative_evidence_at": (
                (prior or {}).get("first_negative_evidence_at")
                or (ts if polarity == POLARITY_CONTRADICTING else None)
            ),
            "last_negative_evidence_at": (
                ts if polarity == POLARITY_CONTRADICTING
                else (prior or {}).get("last_negative_evidence_at")
            ),
        }
        # Keep the in-run view fresh: a second artifact on the SAME claim in
        # one batch (e.g. SUPPORTING + CONTRADICTING for ghl.live.reads_work)
        # must see the first artifact's entry, or its verdict would ignore it.
        if prior is None:
            registry["claims"].append(entry)
        else:
            idx = next(i for i, c in enumerate(registry["claims"])
                       if c.get("claim_id") == claim["claim_id"])
            registry["claims"][idx] = entry
        by_id[claim["claim_id"]] = entry
        processed.add(ident)
        ingested += 1

    if not dry_run:
        registry["claims"].sort(key=lambda c: c.get("claim_id", ""))
        registry["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        claims_file.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        cursor_file.write_text(
            json.dumps({"processed": sorted(processed),
                        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                        indent=2) + "\n",
            encoding="utf-8")

    return {"status": "ok" if ingested else "no_new_artifacts",
            "artifacts": len(artifacts), "ingested": ingested, "skipped": skipped,
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
            "supporting_evidence": ["ev_supported_once"],
            "first_supporting_evidence_at": "2026-08-10T09:00:00+00:00",
            "last_supporting_evidence_at": "2026-08-10T09:00:00+00:00",
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
        # REGRESSED must still show WHAT was supported (never dropped)
        assert updated["claim:ok:task:task_bbb2"]["supporting_evidence"] == ["ev_supported_once"], updated
        assert updated["claim:ok:task:task_bbb2"]["first_supporting_evidence_at"] == "2026-08-10T09:00:00+00:00", updated
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
            "supporting_evidence": ["ev_supported"],
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
        assert c3.get("supporting_evidence") == ["ev_supported"], c3  # support linkage preserved
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

        # ---- PRODUCER 2: live-probe §8 artifacts ----

        def _probe(evidence_id: str, claim_id: str, subject: str, polarity: str,
                   ts: str, evidence_type: str = "live_probe") -> dict:
            art = {
                "evidence_id": evidence_id, "subject_id": subject,
                "claim_id": claim_id, "evidence_type": evidence_type,
                "polarity": polarity, "git_head": "deadbeef",
                "toolchain": "capability-composer live-probe v1.0",
                "timestamp": ts, "result": "PASS" if polarity == "SUPPORTING" else "FAIL",
                "probe": {"provider": subject.split(".")[0], "latency_ms": 1},
                "provenance": {"execution": {}, "environment": {}, "input": {},
                                "verifier": {}, "dependency": {}},
                "freshness": "FRESH", "artifact_hash": "",
            }
            payload = dict(art)
            payload["artifact_hash"] = ""
            art["artifact_hash"] = _sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")))
            return art

        probe_dir = root / "probe_evidence"
        probe_dir.mkdir()
        # SUPPORTING probe -> claim VERIFIED, T4 tier.
        (probe_dir / "a.json").write_text(json.dumps(
            _probe("ev_live_ghl_1", "ghl.live.reads_work",
                   "ghl.live.contacts.search", "SUPPORTING",
                   "2026-08-10T15:00:00+00:00")) + "\n")
        ledger_probe = root / "ledger_probe"
        p1 = ingest_probe_evidence(probe_dir, ledger_probe, "deadbeef")
        assert p1["status"] == "ok" and p1["ingested"] == 1, p1
        claims1 = {c["claim_id"]: c for c in
                   load_registry(ledger_probe / "claims.json")["claims"]}
        ghl = claims1["ghl.live.reads_work"]
        assert ghl["verdict"] == "VERIFIED", ghl
        assert ghl["verification_tier"] == "T4", ghl
        assert ghl["supporting_evidence"] == ["ev_live_ghl_1"], ghl
        assert not ghl["negative_evidence"], ghl
        assert (ledger_probe / "evidence" / "live" / "ev_live_ghl_1.json").exists()

        # Idempotent: re-ingesting the same dir ingests nothing new.
        p1b = ingest_probe_evidence(probe_dir, ledger_probe, "deadbeef")
        assert p1b["ingested"] == 0 and p1b["skipped"] == 1, p1b

        # CONTRADICTING probe on the same claim -> REGRESSED, evidence coexists.
        (probe_dir / "b.json").write_text(json.dumps(
            _probe("ev_live_ghl_2", "ghl.live.reads_work",
                   "ghl.live.contacts.search", "CONTRADICTING",
                   "2026-08-10T16:00:00+00:00")) + "\n")
        p2 = ingest_probe_evidence(probe_dir, ledger_probe, "deadbeef")
        assert p2["ingested"] == 1, p2
        ghl2 = {c["claim_id"]: c for c in
                load_registry(ledger_probe / "claims.json")["claims"]}["ghl.live.reads_work"]
        assert ghl2["verdict"] == "REGRESSED", ghl2
        assert ghl2["supporting_evidence"] == ["ev_live_ghl_1"], ghl2  # never dropped
        assert ghl2["negative_evidence"] == ["ev_live_ghl_2"], ghl2
        assert ghl2["first_supporting_evidence_at"] == "2026-08-10T15:00:00+00:00", ghl2
        assert ghl2["last_negative_evidence_at"] == "2026-08-10T16:00:00+00:00", ghl2

        # Fresh SUPPORTING after the contradiction -> CONTESTED (both coexist).
        (probe_dir / "c.json").write_text(json.dumps(
            _probe("ev_live_ghl_3", "ghl.live.reads_work",
                   "ghl.live.contacts.search", "SUPPORTING",
                   "2026-08-10T17:00:00+00:00")) + "\n")
        p3 = ingest_probe_evidence(probe_dir, ledger_probe, "deadbeef")
        assert p3["ingested"] == 1, p3
        ghl3 = {c["claim_id"]: c for c in
                load_registry(ledger_probe / "claims.json")["claims"]}["ghl.live.reads_work"]
        assert ghl3["verdict"] == "CONTESTED", ghl3
        assert ghl3["supporting_evidence"] == ["ev_live_ghl_1", "ev_live_ghl_3"], ghl3
        assert ghl3["negative_evidence"] == ["ev_live_ghl_2"], ghl3

        # Tampered artifact -> fail loud, nothing ingested.
        tampered = root / "probe_tampered"
        tampered.mkdir()
        good = _probe("ev_live_hub_1", "hubspot.live.reads_work",
                      "hubspot.live.contacts.search", "SUPPORTING",
                      "2026-08-10T15:00:00+00:00")
        good["result"] = "FAIL"  # mutate AFTER hashing — hash no longer matches
        (tampered / "a.json").write_text(json.dumps(good) + "\n")
        pt = ingest_probe_evidence(tampered, root / "ledger_tampered", "x")
        assert pt["status"] == "error" and "tampering" in pt["errors"][0], pt
        assert not (root / "ledger_tampered" / "claims.json").exists(), \
            "tampered artifacts must ingest nothing"

        # Unknown polarity -> fail loud, nothing ingested.
        bad_pol = root / "probe_bad_polarity"
        bad_pol.mkdir()
        (bad_pol / "a.json").write_text(json.dumps(
            _probe("ev_live_x1", "x.live.reads_work", "x.live.contacts.search",
                   "NEUTRAL", "2026-08-10T15:00:00+00:00")) + "\n")
        pb = ingest_probe_evidence(bad_pol, root / "ledger_badpol", "x")
        assert pb["status"] == "error" and "polarity" in pb["errors"][0], pb
        assert not (root / "ledger_badpol" / "claims.json").exists(), \
            "unknown polarity must ingest nothing"

        # Missing evidence_id -> fail loud, nothing ingested. (Recompute the
        # hash AFTER deleting the key so the artifact is hash-valid and the
        # evidence_id contract check is what fires, not the tamper check.)
        no_eid = root / "probe_no_eid"
        no_eid.mkdir()
        missing = _probe("ev_live_y1", "y.live.reads_work", "y.live.contacts.search",
                         "SUPPORTING", "2026-08-10T15:00:00+00:00")
        del missing["evidence_id"]
        missing["artifact_hash"] = _sha256(json.dumps(
            {k: ("" if k == "artifact_hash" else v) for k, v in missing.items()},
            sort_keys=True, separators=(",", ":")))
        (no_eid / "a.json").write_text(json.dumps(missing) + "\n")
        pe = ingest_probe_evidence(no_eid, root / "ledger_noeid", "x")
        assert pe["status"] == "error" and "evidence_id" in pe["errors"][0], pe
        assert not (root / "ledger_noeid" / "claims.json").exists(), \
            "missing evidence_id must ingest nothing"

        # WRITE-probe artifacts (sandbox round trip) — same §8 contract, own
        # evidence_type + claim namespace. SUPPORTING -> VERIFIED, stored
        # under ledger/evidence/write/ — never conflated with live probes.
        write_dir = root / "probe_write"
        write_dir.mkdir()
        (write_dir / "a.json").write_text(json.dumps(
            _probe("ev_write_ghl_1", "ghl.live.writes_work",
                   "ghl.live.write_roundtrip", "SUPPORTING",
                   "2026-08-10T18:00:00+00:00", evidence_type="write_probe")) + "\n")
        (write_dir / "b.json").write_text(json.dumps(
            _probe("ev_write_ghl_2", "ghl.live.writes_work",
                   "ghl.live.write_roundtrip", "CONTRADICTING",
                   "2026-08-10T18:05:00+00:00", evidence_type="write_probe")) + "\n")
        ledger_write = root / "ledger_write"
        pw = ingest_probe_evidence(write_dir, ledger_write, "deadbeef")
        assert pw["status"] == "ok" and pw["ingested"] == 2, pw
        wclaims = {c["claim_id"]: c for c in
                   load_registry(ledger_write / "claims.json")["claims"]}
        ghlw = wclaims["ghl.live.writes_work"]
        assert ghlw["verdict"] == "REGRESSED", ghlw  # support + fresh contradiction
        assert ghlw["verification_tier"] == "T4", ghlw
        assert (ledger_write / "evidence" / "write" / "ev_write_ghl_1.json").exists()
        assert not (ledger_write / "evidence" / "live").exists(), \
            "write evidence must never land under evidence/live/"
        assert (ledger_write / "claims.json").exists()

        # Absent probe dir is NOT an incident.
        absent_p = ingest_probe_evidence(root / "no_probe_dir", root / "ledger_nop", "x")
        assert absent_p["status"] == "no_probe_evidence" and not absent_p["errors"], absent_p

        # Probe dry-run writes nothing.
        pd = ingest_probe_evidence(probe_dir, root / "ledger_probe_dry", "x", dry_run=True)
        assert pd["ingested"] == 3, pd
        assert not (root / "ledger_probe_dry" / "claims.json").exists(), \
            "probe dry-run must write nothing"

    print("self-test: PASS (TASK_FAILED + live/write probe producers: ingest, dedupe incl. same-second distinct, UNVERIFIED/VERIFIED/REGRESSED/CONTESTED verdicts, T3/T4 tiers, evidence/live vs evidence/write isolation, accumulation, tamper fail-loud, malformed fail-loud, dry-run, no-events exit 0)")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="replay_feedback_events.py",
        description="Replay TASK_FAILED events into the ledger as negative (CONTRADICTING) evidence.",
    )
    parser.add_argument("--events", type=Path,
                        default=Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "logs" / "feedback_events.jsonl",
                        help="TASK_FAILED event stream (jsonl)")
    parser.add_argument("--probe-evidence", type=Path, default=DEFAULT_PROBE_DIR,
                        help="probe §8 artifact dir — live (default: "
                             "~/capability-composer/evidence/live) or write "
                             "~/capability-composer/evidence/write")
    parser.add_argument("--ledger-dir", type=Path, default=None,
                        help="ledger root (default: <skill>/ledger)")
    parser.add_argument("--git-head", default="unknown", help="git identity to bind artifacts to")
    parser.add_argument("--dry-run", action="store_true", help="print the ingest plan, write nothing")
    parser.add_argument("--self-test", action="store_true", help="run the in-memory verification and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    ledger_dir = args.ledger_dir or (Path(__file__).resolve().parent.parent / "ledger")
    failed = False

    summary = ingest(args.events, ledger_dir, args.git_head, dry_run=args.dry_run)
    if summary["errors"]:
        print("replay_feedback_events: FAILED (TASK_FAILED stream)")
        for err in summary["errors"]:
            print(f"  error: {err}")
        failed = True
    elif summary["status"] == "no_events":
        print(f"replay_feedback_events: no event stream at {args.events} (nothing to replay)"
              + (" [dry-run — nothing written]" if args.dry_run else ""))
    else:
        print(f"replay_feedback_events: {summary['ingested']} ingested, {summary['skipped']} already-replayed "
              f"of {summary['events']} events; claims in registry: {summary['claims']}"
              + (" [dry-run — nothing written]" if args.dry_run else ""))

    probe = ingest_probe_evidence(args.probe_evidence, ledger_dir, args.git_head,
                                  dry_run=args.dry_run)
    if probe["errors"]:
        print("replay_feedback_events: FAILED (probe evidence)")
        for err in probe["errors"]:
            print(f"  error: {err}")
        failed = True
    elif probe["status"] == "no_probe_evidence":
        print(f"replay_feedback_events: no probe evidence at {args.probe_evidence} (nothing to ingest)"
              + (" [dry-run — nothing written]" if args.dry_run else ""))
    else:
        print(f"replay_feedback_events: {probe['ingested']} probe artifact(s) ingested, "
              f"{probe['skipped']} already-replayed; claims in registry: {probe['claims']}"
              + (" [dry-run — nothing written]" if args.dry_run else ""))

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
