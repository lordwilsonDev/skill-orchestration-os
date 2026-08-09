#!/usr/bin/env python3
"""skill-os CLI — the entry point for skill-orchestration-os.

Two modes:
  default  `skill-os <task>`      DeepSeek-planned DAG, executed step by step
  route    `skill-os route <task>`  routing front-end: classify the task to
                                    exactly ONE skill, dispatch a Claude
                                    subagent from that skill's directory,
                                    record the outcome (the domain-router,
                                    folded in as the routing front-end).
"""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os"))

from runtime.registry.contracts import SkillRegistry, SkillContract
from runtime.orchestrator import Orchestrator
from runtime.executor import Executor
from runtime.audit import AuditLogger
from runtime.meta_learner import MetaLearner
from runtime.domain_router import DomainRouter


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


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "route":
        return cmd_route(argv[1:])

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
