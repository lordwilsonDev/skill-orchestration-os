#!/usr/bin/env python3
"""skill-os CLI — the entry point for skill-orchestration-os.

Three modes:
  default      `skill-os <task>`        DeepSeek-planned DAG, executed step by step
  route        `skill-os route <task>`  routing front-end: classify the task to
                                        exactly ONE skill, dispatch a Claude
                                        subagent from that skill's directory,
                                        record the outcome (the domain-router,
                                        folded in as the routing front-end).
  replay-events `skill-os replay-events`
                                        replay the TASK_FAILED event stream
                                        (logs/feedback_events.jsonl) into the
                                        sovereign-verification ledger as
                                        negative evidence — no manual
                                        invocation of the consumer needed.
"""
import sys
import json
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime.registry.contracts import SkillRegistry, SkillContract
from runtime.orchestrator import Orchestrator
from runtime.executor import Executor
from runtime.audit import AuditLogger
from runtime.meta_learner import MetaLearner
from runtime.domain_router import DomainRouter

# The replay consumer lives in the sovereign-verification skill tree (a
# sibling that CI does not check out). Loaded lazily so the rest of the CLI
# works without it; when a replay is actually requested and the tree is
# absent, the CLI fails loud rather than pretending to replay.
CONSUMER_PATH = Path.home() / ".hermes" / "skills" / "sovereign-verification" / "scripts" / "replay_feedback_events.py"


def _default_git_head() -> str:
    """Bind replayed evidence to the current repo identity when available;
    fail soft to the consumer's "unknown" default."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True, text=True, timeout=10,
        )
        head = proc.stdout.strip()
        return head if proc.returncode == 0 and head else "unknown"
    except Exception:
        return "unknown"


def _register_skills(registry: SkillRegistry) -> None:
    registry.register(SkillContract(
        name="echo", inputs=["message"], outputs=["text"], side_effects=[],
        description="prints the given message",
    ))
    registry.register(SkillContract(
        name="route",
        inputs=["task", "dry_run?", "domain?"],
        outputs=["record"],
        side_effects=["subagent-execution"],
        timeout_s=660,
        description="classify a task to one domain skill and dispatch a Claude subagent",
    ))


def cmd_route(argv: list[str]) -> int:
    """`skill-os route <task> [--dry-run] [--domain <skill_id>]`"""
    domain = None
    dry_run = False
    task_parts: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dry-run":
            dry_run = True
        elif a == "--domain":
            i += 1
            if i >= len(argv):
                print("error: --domain requires a skill_id", file=sys.stderr)
                return 2
            domain = argv[i]
        else:
            task_parts.append(a)
        i += 1
    if not task_parts:
        print("error: route needs a task", file=sys.stderr)
        return 2
    task = " ".join(task_parts)
    try:
        record = DomainRouter().route(task, dry_run=dry_run, domain=domain)
    except SystemExit as e:
        print(f"route rejected: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"route failed: {e}", file=sys.stderr)
        return 1
    # dispatch() already prints `domain:` + `command:` in dry-run mode;
    # only the non-dry-run summary belongs here.
    if not dry_run:
        print(f"dispatched: {record['skill_id']} (exit {record['exit_code']})")
        if record.get("result_summary"):
            print(f"result: {record['result_summary'][:300]}")
    return 0 if record["exit_code"] == 0 else record["exit_code"]


def cmd_replay_events(argv: list[str]) -> int:
    """`skill-os replay-events [--events PATH] [--ledger-dir DIR]
    [--git-head SHA] [--dry-run]`

    Replays the TASK_FAILED event stream into the sovereign-verification
    ledger as negative (CONTRADICTING) evidence. Defaults match the
    consumer: the live stream (logs/feedback_events.jsonl) and the ledger
    under the consumer's skill tree. Exit codes are the consumer's own:
    0 = success (incl. nothing-to-replay), 1 = malformed stream / missing
    consumer (fail loud), 2 = bad arguments."""
    events = None
    ledger_dir = None
    git_head = _default_git_head()
    dry_run = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--events":
            i += 1
            if i >= len(argv):
                print("error: --events requires a path", file=sys.stderr)
                return 2
            events = argv[i]
        elif a == "--ledger-dir":
            i += 1
            if i >= len(argv):
                print("error: --ledger-dir requires a path", file=sys.stderr)
                return 2
            ledger_dir = argv[i]
        elif a == "--git-head":
            i += 1
            if i >= len(argv):
                print("error: --git-head requires a value", file=sys.stderr)
                return 2
            git_head = argv[i]
        elif a == "--dry-run":
            dry_run = True
        else:
            print(f"error: unknown replay-events argument {a!r}", file=sys.stderr)
            return 2
        i += 1

    if not CONSUMER_PATH.exists():
        print(
            f"error: replay consumer not found at {CONSUMER_PATH} "
            "(sovereign-verification skill tree not present in this checkout)",
            file=sys.stderr,
        )
        return 1

    import importlib.util

    spec = importlib.util.spec_from_file_location("replay_feedback_events", CONSUMER_PATH)
    consumer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(consumer)

    consumer_args: list[str] = []
    if events:
        consumer_args += ["--events", events]
    if ledger_dir:
        consumer_args += ["--ledger-dir", ledger_dir]
    if git_head != "unknown":
        consumer_args += ["--git-head", git_head]
    if dry_run:
        consumer_args += ["--dry-run"]
    return consumer.main(consumer_args)


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "route":
        return cmd_route(argv[1:])
    if argv and argv[0] == "replay-events":
        return cmd_replay_events(argv[1:])

    task = " ".join(argv) or "echo hello from skill os"
    registry = SkillRegistry()
    _register_skills(registry)

    audit = AuditLogger()
    executor = Executor(registry, audit_logger=audit)
    orchestrator = Orchestrator(registry)
    learner = MetaLearner()

    dag = orchestrator.plan(task)
    executor._last_task = task
    result = executor.run(dag)
    learner.record(task, dag, {"status": "ok", "result": result})
    suggestions = learner.suggest(task)
    print(f"DAG: {json.dumps(dag, ensure_ascii=False)}")
    print(f"Result: {json.dumps(result, ensure_ascii=False)}")
    if suggestions:
        print(f"Suggestions: {json.dumps(suggestions, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
