"""reality_loop — the 09 -> 02 -> 04 wiring of the personal-AI architecture.

When a task fails verification, emit a contract-shaped TASK_FAILED event that
triggers replanning (Orchestrator.replan) and graph mutation (a revised DAG),
then retry — bounded. This is the "Reality Feedback Loop" from the saved
architecture (Personal-AI-MoIE-Task-Scoped-Cognitive-Runtime.md):

    EXECUTE -> VERIFY -> EVENT -> REPLAN/MUTATE -> RETRY -> (bounded) BLOCK

Design rules:
  - Only FAILURES emit events. A DAG that verifies green writes nothing.
  - Decisions are deterministic (ReplanPolicy), never guessed: fallback_args
    present -> FALLBACK; planner available -> REPLAN; attempts exhausted or
    replan produced nothing -> honest BLOCK (we refuse to guess which steps
    to drop).
  - Events are ledger-shaped (see build_failed_event) and append to the same
    logs/ tree as the route audit, so the sovereign-verification ledger can
    replay failures without the runtime.
  - Zero-spend by construction: the loop never calls the planner for a
    FALLBACK decision and BLOCKs rather than inventing work.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

HOME_DIR = Path(__file__).resolve().parent.parent
FEEDBACK_EVENTS_PATH = HOME_DIR / "logs" / "feedback_events.jsonl"

EVENT_VERSION = "1.0"


# --------------------------------------------------------------------------
# Event contract
# --------------------------------------------------------------------------

def build_failed_event(*, task_id: str | None, goal: str,
                       failed_step: str, failed_index: int, error: str,
                       attempt: int, max_attempts: int, decision: str,
                       dag: List[Dict[str, Any]], revised_dag: List[Dict[str, Any]] | None) -> dict:
    """The contract-shaped TASK_FAILED event. Every field is load-bearing for
    the ledger: what failed (step/index/error), how many tries were allowed,
    what was decided, and the before/after graph (graph-mutation evidence)."""
    return {
        "event": "TASK_FAILED",
        "version": EVENT_VERSION,
        "task_id": task_id,
        "goal": goal,
        "failed_step": failed_step,
        "failed_index": failed_index,
        "error": error,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "decision": decision,  # REPLAN | FALLBACK | BLOCK (as executed)
        "dag": [dict(s) for s in dag],
        "revised_dag": [dict(s) for s in revised_dag] if revised_dag is not None else None,
    }


def append_feedback_event(event: dict, path: Path | None = None) -> dict:
    """Persist one feedback event to the jsonl ledger (one line per event,
    ts stamped at write time — same pattern as the route audit)."""
    event = dict(event)
    event["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = path or FEEDBACK_EVENTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)  # fresh checkouts have no logs/
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


# --------------------------------------------------------------------------
# Deterministic decision policy
# --------------------------------------------------------------------------

class ReplanPolicy:
    """Deterministic failure-handling decisions (no guessing, no LLM).

    decision(failed_step, attempt, max_attempts) -> "FALLBACK" | "REPLAN" | "BLOCK"
      - attempt >= max_attempts            -> "BLOCK"   (bounded, honest)
      - failed_step has fallback_args      -> "FALLBACK" (deterministic fix, no planner spend)
      - otherwise                          -> "REPLAN"  (ask the planner)

    Uppercase by contract — these values land directly in the event's
    decision field; there is exactly one spelling.
    """

    def decision(self, failed_step: dict, attempt: int, max_attempts: int) -> str:
        if attempt >= max_attempts:
            return "BLOCK"
        if failed_step.get("fallback_args"):
            return "FALLBACK"
        return "REPLAN"


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

class RealityLoop:
    """plan -> execute -> verify -> (on failure) event + mutate -> retry, bounded.

    The loop is thin on purpose: it never plans on its own, never drops steps,
    and never retries past max_attempts. Probabilistic intelligence proposes
    (the planner); this deterministic infrastructure decides, executes,
    verifies, and records.
    """

    def __init__(self, orchestrator, executor, policy: ReplanPolicy | None = None,
                 events_path: Path | None = None, max_attempts: int = 3):
        self.orchestrator = orchestrator
        self.executor = executor
        self.policy = policy or ReplanPolicy()
        self.events_path = events_path or FEEDBACK_EVENTS_PATH
        self.max_attempts = max_attempts

    def run(self, task: str, dag: List[Dict[str, Any]] | None = None,
            task_id: str | None = None, max_attempts: int | None = None) -> dict:
        """Execute (and if dag is None, first plan) the task with verification
        and bounded replanning. Returns {\"status\": \"success\"|\"blocked\", ...}
        with attempts, emitted events, final context, and (on block) the last
        failure. Only failures emit events — a green run writes nothing."""
        max_attempts = max_attempts or self.max_attempts
        task_id = task_id or f"task_{uuid4().hex[:8]}"
        dag = [dict(s) for s in dag] if dag is not None else list(self.orchestrator.plan(task))
        events: List[dict] = []
        attempt = 1
        while True:
            results = self.executor.run_steps(dag)
            failure = self._first_failure(dag, results)
            if failure is None:
                context = {f"{r['skill'].replace('-', '_')}_out": r["output"] for r in results}
                return {
                    "status": "success", "task_id": task_id, "goal": task,
                    "attempts": attempt, "events": events, "context": context,
                    "failure": None,
                }
            failed_step = failure["step"]
            error = failure["error"]
            decision = self.policy.decision(failed_step, attempt, max_attempts)
            revised: List[Dict[str, Any]] | None = None
            if decision == "FALLBACK":
                revised = self._apply_fallback(dag, failure)
            elif decision == "REPLAN":
                revised = self.orchestrator.replan(task, dag, failure)
                if not revised:
                    # Planner had nothing (transport down / empty / local
                    # policy has no fallback) — honest block, never guess.
                    # revised stays None so the event records "no viable
                    # revision", not an empty graph.
                    decision = "BLOCK"
                    revised = None
            event = build_failed_event(
                task_id=task_id, goal=task,
                failed_step=failed_step.get("skill") or "?",
                failed_index=failure["index"], error=error,
                attempt=attempt, max_attempts=max_attempts,
                decision=decision,
                dag=dag, revised_dag=revised,
            )
            self._emit(event)
            events.append(event)
            if decision == "BLOCK":
                return {
                    "status": "blocked", "task_id": task_id, "goal": task,
                    "attempts": attempt, "events": events, "context": None,
                    "failure": {"step": failed_step.get("skill") or "?",
                                "index": failure["index"], "error": error},
                }
            dag = [dict(s) for s in revised]
            attempt += 1

    # --- internals ---

    def _first_failure(self, dag, results) -> dict | None:
        """First step that fails verification, or None. Returns
        {\"step\": <original step dict>, \"index\": int, \"error\": str}."""
        for i, (step, result) in enumerate(zip(dag, results)):
            ok, reason = self.executor.verify_step(step, result)
            if not ok:
                return {"step": step, "index": i, "error": reason}
        return None

    def _apply_fallback(self, dag, failure) -> List[Dict[str, Any]]:
        """Deterministic in-loop fix: merge fallback_args into the failed
        step's args and clear fallback_args so the fix is one-shot (a second
        failure routes to replan/block instead of looping on the same args)."""
        steps = [dict(s) for s in dag]
        step = failure["step"]
        merged = {**step.get("args", {}), **step["fallback_args"]}
        replaced = {**step, "args": merged}
        replaced.pop("fallback_args", None)
        steps[failure["index"]] = replaced
        return steps

    def _emit(self, event: dict) -> None:
        """Persist the event. Fail loud: a TASK_FAILED event that cannot be
        recorded is itself an incident (ledger invariant #8 — notification
        failure is an observable event). Never fake a record by swallowing
        the write failure."""
        append_feedback_event(event, path=self.events_path)
