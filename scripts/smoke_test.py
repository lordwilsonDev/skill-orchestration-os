#!/usr/bin/env python3
"""Smoke test for Skill Orchestration OS scaffold."""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.registry.contracts import SkillRegistry, SkillContract
from runtime.orchestrator import Orchestrator
from runtime.executor import Executor
from runtime.audit import AuditLogger
from runtime.omni_route import OmniRoute
from runtime.meta_learner import MetaLearner


def test_registry():
    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    registry.register(SkillContract(name="vault_read", inputs=["path"], outputs=["content"], side_effects=["read"]))
    assert "echo" in registry.all()
    assert registry.get("echo").version == "0.1.0"


def test_orchestrator_local_fallback():
    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    orch = Orchestrator(registry)
    dag = orch.plan("say hello")
    assert isinstance(dag, list)
    assert len(dag) >= 1
    assert dag[0]["skill"] == "echo"


def test_planner_prompt_suggests_route():
    """The planner prompt lists `route` with its description and the
    single-domain guidance, so DeepSeek can emit route steps (zero spend —
    only prompt construction is checked)."""
    registry = SkillRegistry()
    registry.register(SkillContract(
        name="route", inputs=["task", "dry_run?", "domain?"],
        outputs=["record"], side_effects=["subagent-execution"],
        description="classify a task to one domain skill and dispatch a Claude subagent",
    ))
    registry.register(SkillContract(
        name="echo", inputs=["message"], outputs=["text"], side_effects=[],
        description="prints the given message",
    ))
    orch = Orchestrator(registry)
    prompt = orch._build_prompt("draft a client proposal")
    assert "route — classify a task to one domain skill" in prompt
    assert "echo — prints the given message" in prompt
    assert '"task": "<original task>"' in prompt
    assert "draft a client proposal" in prompt


def test_executor_runs_dag():
    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    audit = AuditLogger()
    executor = Executor(registry, audit_logger=audit)
    dag = [{"skill": "echo", "args": {"message": "hello-os"}}]
    result = executor.run(dag)
    assert result["echo_out"] == "hello-os"


def test_audit_log():
    import tempfile
    log_dir = Path(tempfile.mkdtemp(prefix="skill-os-test-"))
    audit = AuditLogger(log_dir=log_dir)
    audit.log("echo", "success", {"message": "hi"}, "hi")
    entries = audit.replay()
    assert len(entries) == 1
    assert entries[0]["skill"] == "echo"
    assert entries[0]["status"] == "success"


def test_omni_route():
    route = OmniRoute()
    route.register("echo", lambda payload: payload.get("message", ""))
    result = route.send("echo", {"message": " routed"})
    assert result == " routed"


def test_meta_learner():
    learner = MetaLearner()
    learner.record("say hello", [{"skill": "echo", "args": {}}], {"status": "ok"})
    suggestions = learner.suggest("say hello")
    assert len(suggestions) >= 1
    assert suggestions[0]["skill"] == "echo"


def test_deepseek_plan():
    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    registry.register(SkillContract(name="vault_read", inputs=["path"], outputs=["content"], side_effects=["read"]))
    orch = Orchestrator(registry)
    dag = orch.plan("read vault file and echo it")
    if not dag:
        print("DeepSeek plan returned empty; local fallback used")
        return
    assert isinstance(dag, list)
    assert len(dag) >= 1
    for step in dag:
        assert "skill" in step


def test_route_executor_dag_step():
    """The folded routing front-end runs as an executor DAG step — zero spend.

    Uses --domain (no DeepSeek) + dry_run (no subagent): resolves a REAL
    skill_id from the canonical domains.json, produces a record, and appends
    an audit line to a temp audit path.
    """
    import tempfile

    registry = SkillRegistry()
    registry.register(SkillContract(
        name="route", inputs=["task", "dry_run?", "domain?"],
        outputs=["record"], side_effects=["subagent-execution"],
    ))
    from runtime.domain_router import DomainRouter, REGISTRY_PATH
    table = json.loads(Path(REGISTRY_PATH).read_text(encoding="utf-8"))
    assert table["count"] > 0, "canonical domains.json is empty — build it first"
    # First table entry, unconditionally: dirs are absolute ~/.hermes paths
    # that don't exist on a clean runner — dry-run routing never touches them.
    skill_id = table["skills"][0]["skill_id"]

    with tempfile.TemporaryDirectory(prefix="route-smoke-") as tmp:
        router = DomainRouter(audit_path=Path(tmp) / "route_audit.jsonl")
        record = router.route("run the smoke test", dry_run=True, domain=skill_id)
        assert record["skill_id"] == skill_id
        assert record["exit_code"] == 0
        assert "dry-run" in record["result_summary"]
        lines = (Path(tmp) / "route_audit.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["skill_id"] == skill_id

    executor = Executor(registry)
    out = executor.run([{"skill": "route", "args": {"task": "t", "dry_run": True, "domain": skill_id}}])
    assert out["route_out"]["record"]["skill_id"] == skill_id


def test_route_rejects_unknown_domain():
    """A bad --domain records a step error and the DAG run survives (SystemExit
    must not escape the executor — regression for the fold review fix)."""
    registry = SkillRegistry()
    registry.register(SkillContract(
        name="route", inputs=["task", "dry_run?", "domain?"],
        outputs=["record"], side_effects=["subagent-execution"],
    ))
    executor = Executor(registry)
    out = executor.run([{"skill": "route", "args": {
        "task": "t", "dry_run": True, "domain": "definitely-bogus-skill"}}])
    assert "error" in out["route_out"]
    assert "unknown skill_id" in out["route_out"]["error"]


def test_planner_normalizes_wrapped_steps():
    """DeepSeek sometimes wraps the step array in {"steps": [...]}; the
    planner must unwrap it (and reject non-list shapes) so the executor never
    iterates dict keys as steps. Transport mocked — zero spend."""
    from runtime import orchestrator as orch_mod

    original = orch_mod.deepseek_chat
    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    registry.register(SkillContract(
        name="route", inputs=["task", "dry_run?", "domain?"],
        outputs=["record"], side_effects=["subagent-execution"],
        description="classify a task to one domain skill and dispatch a Claude subagent",
    ))
    orch = Orchestrator(registry)
    orch.deepseek_key = "TEST_KEY"  # bypass the env-key guard

    orch_mod.deepseek_chat = lambda *a, **k: json.dumps({"steps": [{"skill": "echo", "args": {"message": "hi"}}]})
    try:
        dag = orch.plan("say hi")
        assert dag == [{"skill": "echo", "args": {"message": "hi"}}]
    finally:
        orch_mod.deepseek_chat = original

    # bare list passes through unchanged
    orch_mod.deepseek_chat = lambda *a, **k: json.dumps([{"skill": "echo", "args": {"message": "bare"}}])
    try:
        assert orch.plan("say hi") == [{"skill": "echo", "args": {"message": "bare"}}]
    finally:
        orch_mod.deepseek_chat = original

    # {"steps": <non-list>} -> [] -> local fallback
    orch_mod.deepseek_chat = lambda *a, **k: json.dumps({"steps": "oops"})
    try:
        assert orch.plan("say hi") == [{"skill": "echo", "args": {"message": "say hi"}}]
    finally:
        orch_mod.deepseek_chat = original

    # non-list dict -> [] -> local fallback
    orch_mod.deepseek_chat = lambda *a, **k: json.dumps({"nonsense": True})
    try:
        assert orch.plan("say hi") == [{"skill": "echo", "args": {"message": "say hi"}}]
    finally:
        orch_mod.deepseek_chat = original


def test_route_cli_dry_run():
    """`skill-os route --domain <id> --dry-run` works end-to-end, zero spend."""
    import subprocess

    from runtime.domain_router import REGISTRY_PATH
    table = json.loads(Path(REGISTRY_PATH).read_text(encoding="utf-8"))
    assert table["count"] > 0, "canonical domains.json is empty — build it first"
    # First table entry, unconditionally: dirs are absolute ~/.hermes paths
    # that don't exist on a clean runner — dry-run routing never touches them.
    skill_id = table["skills"][0]["skill_id"]
    proc = subprocess.run(
        [sys.executable, "cli.py", "route", "--domain", skill_id, "--dry-run", "smoke task"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "command:" in proc.stdout
    assert "cd " in proc.stdout               # directory-based dispatch
    assert "dispatched:" not in proc.stdout   # dry-run prints no dispatch summary


if __name__ == "__main__":
    tests = [
        test_registry,
        test_orchestrator_local_fallback,
        test_planner_prompt_suggests_route,
        test_planner_normalizes_wrapped_steps,
        test_executor_runs_dag,
        test_audit_log,
        test_omni_route,
        test_meta_learner,
        test_deepseek_plan,
        test_route_executor_dag_step,
        test_route_rejects_unknown_domain,
        test_route_cli_dry_run,
    ]
    passed = 0
    for test in tests:
        try:
            test()
            passed += 1
            print(f"PASS {test.__name__}")
        except Exception as e:
            print(f"FAIL {test.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
