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


def test_route_stubbed_dispatch():
    """Close the last unprovable box: a REAL subprocess dispatch through the
    canonical dispatch() — with a stub `claude` binary emitting the JSON
    envelope claude -p --output-format json produces — runs green, writes a
    real audit.jsonl line, and records nonzero exits faithfully. Zero spend:
    the stub replaces the binary; nothing is invoked remotely, and no
    ~/.hermes skill dirs are touched (temp registry + temp cwd)."""
    import tempfile
    from runtime import domain_router as dr

    with tempfile.TemporaryDirectory(prefix="dispatch-stub-") as tmp:
        tmp = Path(tmp)
        stub_ok = tmp / "claude-ok"
        stub_ok.write_text(
            "#!/bin/sh\n"
            "echo '{\"result\": \"stubbed claude completed the task\"}'\n"
            "exit 0\n"
        )
        stub_ok.chmod(0o755)
        stub_fail = tmp / "claude-fail"
        stub_fail.write_text("#!/bin/sh\necho 'boom' >&2\nexit 1\n")
        stub_fail.chmod(0o755)

        reg = tmp / "registry.json"
        reg.write_text(json.dumps({
            "count": 1, "containers": ["stub"],
            "skills": [{"skill_id": "stub/skill", "container": "stub",
                        "description": "stub", "dir": str(tmp)}],
        }))

        saved = dr.CLAUDE_BIN
        try:
            # Happy path: fake claude succeeds -> exit 0, summary parsed, audit line written.
            dr.CLAUDE_BIN = str(stub_ok)
            router = dr.DomainRouter(registry_path=reg, audit_path=tmp / "audit.jsonl",
                                     events_path=tmp / "feedback_events.jsonl")
            record = router.route("do the thing", dry_run=False, domain="stub/skill")
            assert record["exit_code"] == 0
            assert "stubbed claude completed the task" in record["result_summary"]
            lines = (tmp / "audit.jsonl").read_text().strip().splitlines()
            assert len(lines) == 1
            assert json.loads(lines[0])["exit_code"] == 0
            assert json.loads(lines[0])["skill_id"] == "stub/skill"
            # Success emits no feedback event.
            assert not (tmp / "feedback_events.jsonl").exists()

            # Failure path: fake claude exits 1 -> the router records it, not masks it.
            dr.CLAUDE_BIN = str(stub_fail)
            rec2 = dr.DomainRouter(registry_path=reg, audit_path=tmp / "audit.jsonl",
                                   events_path=tmp / "feedback_events.jsonl").route(
                "fail task", dry_run=False, domain="stub/skill")
            assert rec2["exit_code"] == 1
            lines2 = (tmp / "audit.jsonl").read_text().strip().splitlines()
            assert len(lines2) == 2
            assert json.loads(lines2[1])["exit_code"] == 1
        finally:
            dr.CLAUDE_BIN = saved


def test_vault_check_first_routable():
    """vault-check-first must be registered in the canonical registry with a
    usable description, and the CLI must dry-run-dispatch it zero-spend.
    Guards the 2026-08-10 routing: a skill that lives in ~/.agents/skills but
    is missing from the hermes tree (or dropped from domains.json) is not
    dispatchable by the orchestrator -- this test fails loudly instead."""
    import subprocess

    from runtime.domain_router import REGISTRY_PATH
    table = json.loads(Path(REGISTRY_PATH).read_text(encoding="utf-8"))
    entries = [e for e in table["skills"] if e["skill_id"] == "vault-check-first"]
    assert entries, "vault-check-first missing from domains.json — rebuild with --rebuild"
    entry = entries[0]
    assert entry["container"] == "vault-check-first", entry
    assert len(entry["description"]) > 40, f"description too thin: {entry['description']!r}"
    # On-disk + parity legs require the LOCAL hermes skill tree. CI checks out
    # only this repo (no ~/.hermes siblings), so these legs skip there with a
    # visible reason; the routing assertions above and the CLI dry-run below
    # still run everywhere. Locally they execute at full strength.
    if not Path(entry["skill_md"]).exists():
        raise _Skip(
            "hermes skill tree not present in this checkout — on-disk/parity "
            "legs run locally where ~/.hermes/skills/vault-check-first exists")
    # Parity: the hermes copy must not drift from the canonical ~/.agents/skills
    # original (the mutation-tested one). If the description diverges, the
    # routing table would dispatch on a stale description -- same drift class
    # the registry sync guard catches, but for skill content.
    agents_orig = Path.home() / ".agents" / "skills" / "vault-check-first" / "SKILL.md"
    if agents_orig.exists():
        hermes = Path(entry["skill_md"]).read_text(encoding="utf-8")
        orig = agents_orig.read_text(encoding="utf-8")
        import re as _re

        def _fm_field(text: str, key: str) -> str:
            m = _re.search(rf"^{key}:\s*(.+)$", text, _re.MULTILINE)
            return m.group(1).strip().strip('"').strip() if m else ""

        for key in ("name", "description"):
            h, o = _fm_field(hermes, key), _fm_field(orig, key)
            assert h == o, (
                f"vault-check-first drift: hermes {key!r} differs from ~/.agents/skills "
                f"original — re-copy the skill or rebuild"
            )
    # Zero-spend dispatch proof: dry-run through the real CLI.
    proc = subprocess.run(
        [sys.executable, "cli.py", "route", "--domain", "vault-check-first",
         "--dry-run", "check the vault before client onboarding work"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "vault-check-first" in proc.stdout
    assert "dispatched:" not in proc.stdout  # dry-run must not execute


def test_registry_copies_in_sync():
    """Canonical domains.json (skill-orchestration-os) and the domain-router
    shim copy must not drift: same count, same skill_id list, same container
    list. Mirrors the d03 sync-guard pattern — a rebuild of one without the
    other fails this test. `generated_at` timestamps always differ, so the
    comparison normalizes them away."""
    canonical = Path(__file__).resolve().parent.parent / "domains.json"
    shim = Path.home() / ".hermes" / "domain-router" / "domains.json"
    assert canonical.exists(), f"canonical domains.json missing: {canonical}"
    # The shim copy lives in the domain-router repo (~/.hermes/domain-router),
    # which CI does not check out. The sync guard runs at full strength where
    # both copies exist (locally, daily via the d03 factory gate); in CI the
    # leg skips with a visible reason rather than failing on a missing sibling.
    if not shim.exists():
        raise _Skip(
            "domain-router shim not in this checkout — the sync guard runs "
            "locally (d03 factory gate) where both registry copies exist")
    c = json.loads(canonical.read_text(encoding="utf-8"))
    s = json.loads(shim.read_text(encoding="utf-8"))
    c = json.loads(canonical.read_text(encoding="utf-8"))
    s = json.loads(shim.read_text(encoding="utf-8"))
    assert c["count"] == s["count"], (
        f"registry drift: canonical {c['count']} vs shim {s['count']} "
        f"— rebuild both with --rebuild")
    assert c["containers"] == s["containers"], (
        "registry drift: container lists differ — rebuild both with --rebuild")
    assert [e["skill_id"] for e in c["skills"]] == [e["skill_id"] for e in s["skills"]], (
        "registry drift: skill_id lists differ — rebuild both with --rebuild")


def test_sovereign_verification_routable():
    """sovereign-verification must be registered in the canonical registry with
    a usable description, and the CLI must dry-run-dispatch it zero-spend.
    Guards the 2026-08-10 ledger skill install: the skill lives in
    ~/.hermes/skills and ~/.agents/skills and is dispatchable by the
    orchestrator — this test fails loudly if it is dropped from domains.json,
    its description is too thin for routing, or the parity copy drifts."""
    import subprocess

    from runtime.domain_router import REGISTRY_PATH
    table = json.loads(Path(REGISTRY_PATH).read_text(encoding="utf-8"))
    entries = [e for e in table["skills"] if e["skill_id"] == "sovereign-verification"]
    assert entries, "sovereign-verification missing from domains.json — rebuild with --rebuild"
    entry = entries[0]
    assert entry["container"] == "sovereign-verification", entry
    assert len(entry["description"]) > 40, f"description too thin: {entry['description']!r}"
    # On-disk + parity legs require the LOCAL hermes skill tree.
    if not Path(entry["skill_md"]).exists():
        raise _Skip(
            "hermes skill tree not present in this checkout — on-disk/parity "
            "legs run locally where ~/.hermes/skills/sovereign-verification exists")
    # Parity: hermes copy must not drift from the canonical ~/.agents/skills.
    agents_orig = Path.home() / ".agents" / "skills" / "sovereign-verification" / "SKILL.md"
    if agents_orig.exists():
        hermes = Path(entry["skill_md"]).read_text(encoding="utf-8")
        orig = agents_orig.read_text(encoding="utf-8")
        import re as _re

        def _fm_field(text: str, key: str) -> str:
            m = _re.search(rf"^{key}:\s*(.+)$", text, _re.MULTILINE)
            return m.group(1).strip().strip('"').strip() if m else ""

        for key in ("name", "description"):
            h, o = _fm_field(hermes, key), _fm_field(orig, key)
            assert h == o, (
                f"sovereign-verification drift: hermes {key!r} differs from "
                f"~/.agents/skills original — re-copy the skill or rebuild")
    # Zero-spend dispatch proof: dry-run through the real CLI.
    proc = subprocess.run(
        [sys.executable, "cli.py", "route", "--domain", "sovereign-verification",
         "--dry-run", "check the verification ledger before running the factory gate"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "sovereign-verification" in proc.stdout
    assert "dispatched:" not in proc.stdout  # dry-run must not execute


# ---------------------------------------------------------------------------
# Reality feedback loop (09 -> 02 -> 04) — zero-spend: echo steps, temp
# events paths, mocked planner transport. No API keys, no subagents.
# ---------------------------------------------------------------------------

def test_reality_loop_pass_no_events():
    """A DAG that verifies green emits ZERO feedback events (the loop only
    speaks on failure) and returns success with the final context."""
    import tempfile
    from runtime.reality_loop import RealityLoop

    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    orch = Orchestrator(registry)
    executor = Executor(registry)
    with tempfile.TemporaryDirectory(prefix="reality-pass-") as tmp:
        loop = RealityLoop(orch, executor, events_path=Path(tmp) / "feedback_events.jsonl")
        out = loop.run("say hi", [{"skill": "echo", "args": {"message": "hi"}}])
        assert out["status"] == "success"
        assert out["attempts"] == 1
        assert out["context"]["echo_out"] == "hi"
        assert out["events"] == []
        assert not (Path(tmp) / "feedback_events.jsonl").exists()


def test_reality_loop_failure_blocks_honestly():
    """A failed step with no fallback and an empty replan -> honest BLOCK:
    one TASK_FAILED event (decision BLOCK as executed, revised_dag None),
    status blocked, nothing guessed."""
    import tempfile
    from runtime import orchestrator as orch_mod
    from runtime.reality_loop import RealityLoop

    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    orch = Orchestrator(registry)
    orch.deepseek_key = "TEST_KEY"  # would call DeepSeek — transport mocked below
    executor = Executor(registry)

    def _boom(*a, **k):
        raise RuntimeError("transport down")

    original = orch_mod.deepseek_chat
    orch_mod.deepseek_chat = _boom
    try:
        with tempfile.TemporaryDirectory(prefix="reality-block-") as tmp:
            loop = RealityLoop(orch, executor, events_path=Path(tmp) / "feedback_events.jsonl")
            dag = [{"skill": "echo", "args": {"message": "wrong"}, "verify": {"contains": "right"}}]
            out = loop.run("say right", dag)
            assert out["status"] == "blocked"
            assert out["attempts"] == 1
            assert out["failure"]["step"] == "echo"
            lines = (Path(tmp) / "feedback_events.jsonl").read_text().strip().splitlines()
            assert len(lines) == 1
            ev = json.loads(lines[0])
            assert ev["event"] == "TASK_FAILED"
            assert ev["decision"] == "BLOCK"
            assert ev["revised_dag"] is None
    finally:
        orch_mod.deepseek_chat = original


def test_reality_loop_fallback_recovers():
    """A failed step WITH fallback_args -> deterministic FALLBACK decision
    (planner never consulted — asserting that with a bombed replan), the step
    retries with merged args and passes."""
    import tempfile
    from runtime.reality_loop import RealityLoop

    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    orch = Orchestrator(registry)

    def _bomb(*a, **k):
        raise AssertionError("FALLBACK must not consult the planner")

    orch.replan = _bomb
    executor = Executor(registry)
    with tempfile.TemporaryDirectory(prefix="reality-fallback-") as tmp:
        loop = RealityLoop(orch, executor, events_path=Path(tmp) / "feedback_events.jsonl")
        dag = [{"skill": "echo", "args": {"message": "wrong"},
                "fallback_args": {"message": "right"}, "verify": {"contains": "right"}}]
        out = loop.run("say right", dag)
        assert out["status"] == "success"
        assert out["attempts"] == 2
        assert out["context"]["echo_out"] == "right"
        ev = json.loads((Path(tmp) / "feedback_events.jsonl").read_text().strip().splitlines()[0])
        assert ev["decision"] == "FALLBACK"
        assert ev["revised_dag"][0]["args"]["message"] == "right"


def test_reality_loop_replan_recovers():
    """A planner-revised DAG that fixes the failure -> success on attempt 2;
    the TASK_FAILED event carries the revised_dag (graph-mutation evidence)."""
    import tempfile
    from runtime.reality_loop import RealityLoop

    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    orch = Orchestrator(registry)
    orch.replan = lambda task, dag, failure: [
        {"skill": "echo", "args": {"message": "right"}, "verify": {"contains": "right"}}
    ]
    executor = Executor(registry)
    with tempfile.TemporaryDirectory(prefix="reality-replan-") as tmp:
        loop = RealityLoop(orch, executor, events_path=Path(tmp) / "feedback_events.jsonl")
        dag = [{"skill": "echo", "args": {"message": "wrong"}, "verify": {"contains": "right"}}]
        out = loop.run("say right", dag)
        assert out["status"] == "success"
        assert out["attempts"] == 2
        ev = json.loads((Path(tmp) / "feedback_events.jsonl").read_text().strip().splitlines()[0])
        assert ev["decision"] == "REPLAN"
        assert ev["revised_dag"][0]["args"]["message"] == "right"


def test_reality_loop_bounded_attempts():
    """Replans that keep returning still-failing DAGs are bounded by
    max_attempts: attempts == max_attempts, final event decision BLOCK."""
    import tempfile
    from runtime.reality_loop import RealityLoop

    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    orch = Orchestrator(registry)
    orch.replan = lambda task, dag, failure: [
        {"skill": "echo", "args": {"message": "still wrong"}, "verify": {"contains": "right"}}
    ]
    executor = Executor(registry)
    with tempfile.TemporaryDirectory(prefix="reality-bounded-") as tmp:
        loop = RealityLoop(orch, executor, events_path=Path(tmp) / "feedback_events.jsonl", max_attempts=3)
        dag = [{"skill": "echo", "args": {"message": "wrong"}, "verify": {"contains": "right"}}]
        out = loop.run("say right", dag)
        assert out["status"] == "blocked"
        assert out["attempts"] == 3
        events = [json.loads(l) for l in (Path(tmp) / "feedback_events.jsonl").read_text().strip().splitlines()]
        assert [e["decision"] for e in events] == ["REPLAN", "REPLAN", "BLOCK"]
        assert events[0]["attempt"] == 1
        assert events[2]["attempt"] == 3


def test_feedback_event_contract_shape():
    """TASK_FAILED events carry the full contract: event, version, task_id,
    goal, failed_step/index, error, attempt, max_attempts, decision, dag,
    revised_dag (+ ts on write). The ledger can replay these without the
    runtime."""
    import tempfile
    from runtime.reality_loop import build_failed_event, append_feedback_event

    ev = build_failed_event(
        task_id="t1", goal="g", failed_step="echo", failed_index=0, error="boom",
        attempt=2, max_attempts=3, decision="REPLAN",
        dag=[{"skill": "echo", "args": {}}], revised_dag=None,
    )
    required = {"event", "version", "task_id", "goal", "failed_step", "failed_index",
                "error", "attempt", "max_attempts", "decision", "dag", "revised_dag"}
    assert required <= set(ev)
    assert ev["event"] == "TASK_FAILED"
    assert ev["version"] == "1.0"
    assert ev["dag"][0]["skill"] == "echo"
    assert ev["revised_dag"] is None
    with tempfile.TemporaryDirectory(prefix="evt-shape-") as tmp:
        p = Path(tmp) / "events.jsonl"
        append_feedback_event(ev, path=p)
        line = json.loads(p.read_text().strip())
        assert line["ts"]  # write-time stamp
        assert line["decision"] == "REPLAN"


def test_orchestrator_replan_local_fallback():
    """replan() with the transport down falls back to the deterministic local
    policy: fallback_args -> merged retry; no fallback_args -> honest []."""
    from runtime import orchestrator as orch_mod

    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))
    orch = Orchestrator(registry)
    orch.deepseek_key = "TEST_KEY"

    def _boom(*a, **k):
        raise RuntimeError("transport down")

    original = orch_mod.deepseek_chat
    orch_mod.deepseek_chat = _boom
    try:
        dag = [{"skill": "echo", "args": {"message": "a"}, "fallback_args": {"message": "b"}}]
        revised = orch.replan("t", dag, {"step": dag[0], "index": 0, "error": "x"})
        assert revised[0]["args"] == {"message": "b"}
        assert "fallback_args" not in revised[0]  # one-shot fix
        dag2 = [{"skill": "echo", "args": {"message": "a"}}]
        assert orch.replan("t", dag2, {"step": dag2[0], "index": 0, "error": "x"}) == []
    finally:
        orch_mod.deepseek_chat = original


def test_route_dispatch_failure_emits_feedback_event():
    """A failed claude dispatch (exit != 0) appends a TASK_FAILED feedback
    event alongside the audit line — the 09 -> router link. Success emits
    none. Zero spend: stubbed claude binaries."""
    import tempfile
    from runtime import domain_router as dr

    with tempfile.TemporaryDirectory(prefix="route-fb-") as tmp:
        tmp = Path(tmp)
        stub_fail = tmp / "claude-fail"
        stub_fail.write_text("#!/bin/sh\necho 'nope' >&2\nexit 1\n")
        stub_fail.chmod(0o755)
        stub_ok = tmp / "claude-ok"
        stub_ok.write_text("#!/bin/sh\necho '{\"result\": \"done\"}'\nexit 0\n")
        stub_ok.chmod(0o755)
        reg = tmp / "registry.json"
        reg.write_text(json.dumps({
            "count": 1, "containers": ["stub"],
            "skills": [{"skill_id": "stub/skill", "container": "stub",
                        "description": "stub", "dir": str(tmp)}],
        }))
        events = tmp / "feedback_events.jsonl"
        saved = dr.CLAUDE_BIN
        try:
            # Failure path: exit 1 -> TASK_FAILED feedback event, honest BLOCK.
            dr.CLAUDE_BIN = str(stub_fail)
            router = dr.DomainRouter(registry_path=reg, audit_path=tmp / "audit.jsonl",
                                     events_path=events)
            rec = router.route("fail task", dry_run=False, domain="stub/skill")
            assert rec["exit_code"] == 1
            ev = json.loads(events.read_text().strip().splitlines()[0])
            assert ev["event"] == "TASK_FAILED"
            assert ev["decision"] == "BLOCK"
            assert ev["failed_step"] == "route:stub/skill"
            assert ev["revised_dag"] is None
            # Success path: exit 0 -> no additional event.
            dr.CLAUDE_BIN = str(stub_ok)
            rec2 = dr.DomainRouter(registry_path=reg, audit_path=tmp / "audit.jsonl",
                                   events_path=events).route("ok task", dry_run=False, domain="stub/skill")
            assert rec2["exit_code"] == 0
            assert len(events.read_text().strip().splitlines()) == 1
        finally:
            dr.CLAUDE_BIN = saved


def test_replay_consumer_wired():
    """The TASK_FAILED replay consumer is wired as a gate leg: a malformed
    stream makes the real CLI exit NON-zero (never silently replay the clean
    prefix), an absent stream exits 0 (nothing to replay is not an incident),
    and a well-formed event flows through the real consumer into a temp
    ledger with the REGRESSED flip (previously-VERIFIED claim contradicted by
    reality). Hermetic and zero-spend — temp fixtures, no network, no keys.

    The consumer lives in the sovereign-verification skill tree
    (~/.hermes/skills/sovereign-verification), which CI does not check out;
    there the leg skips with a visible reason and runs at full strength
    locally — same doctrine as the vault-check-first/sovereign-verification
    routability tests."""
    import importlib.util
    import subprocess
    import tempfile

    consumer_path = Path.home() / ".hermes" / "skills" / "sovereign-verification" / "scripts" / "replay_feedback_events.py"
    if not consumer_path.exists():
        raise _Skip(
            "sovereign-verification skill tree not in this checkout — the "
            "replay gate leg runs locally where the consumer lives")
    spec = importlib.util.spec_from_file_location("replay_feedback_events", consumer_path)
    consumer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(consumer)

    with tempfile.TemporaryDirectory(prefix="replay-wired-") as tmp:
        tmp = Path(tmp)
        events = tmp / "events.jsonl"
        events.write_text(json.dumps({
            "event": "TASK_FAILED", "version": "1.0", "task_id": "task_wired1",
            "goal": "keep the service up", "failed_step": "uptime", "failed_index": 0,
            "error": "timeout", "attempt": 3, "max_attempts": 3, "decision": "BLOCK",
            "dag": [], "revised_dag": None, "ts": "2026-08-10T16:00:00+00:00",
        }) + "\n")
        bad = tmp / "bad.jsonl"
        bad.write_text('{"event": "TASK_FAILED", "ts": "x"}\nnot-json\n')

        # Absent stream: exit 0 (a daily gate must not fail before the first
        # failure is ever recorded).
        proc0 = subprocess.run(
            [sys.executable, str(consumer_path), "--events", str(tmp / "missing.jsonl"),
             "--ledger-dir", str(tmp / "ledger_cli0")],
            capture_output=True, text=True, timeout=60,
        )
        assert proc0.returncode == 0, proc0.stdout + proc0.stderr
        assert "nothing to replay" in proc0.stdout, proc0.stdout

        # Malformed stream: the real CLI exits NON-zero — the gate leg fails
        # loudly instead of silently replaying the clean prefix.
        proc = subprocess.run(
            [sys.executable, str(consumer_path), "--events", str(bad),
             "--ledger-dir", str(tmp / "ledger_cli")],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "FAILED" in proc.stdout, proc.stdout
        assert "malformed event" in proc.stdout, proc.stdout
        assert not (tmp / "ledger_cli" / "claims.json").exists(), \
            "a malformed stream must ingest nothing"

        # Well-formed event through the real consumer -> REGRESSED flip.
        ledger = tmp / "ledger"
        ledger.mkdir(parents=True, exist_ok=True)
        (ledger / "claims.json").write_text(json.dumps({"claims": [{
            "claim_id": "claim:ok:task:task_wired1", "subject": "task:task_wired1",
            "text": '"task:task_wired1" completes without failure',
            "verification_tier": "T3", "verdict": "VERIFIED",
            "negative_evidence": [], "first_negative_evidence_at": None,
            "last_negative_evidence_at": None,
        }], "generated_at": None}, indent=2) + "\n")
        ok = consumer.ingest(events, ledger, "wired-test")
        assert ok["status"] == "ok" and ok["ingested"] == 1, ok
        reg = json.loads((ledger / "claims.json").read_text())
        claim = {c["claim_id"]: c for c in reg["claims"]}["claim:ok:task:task_wired1"]
        assert claim["verdict"] == "REGRESSED", claim
        assert claim["negative_evidence"], claim
        assert claim["first_negative_evidence_at"] == "2026-08-10T16:00:00+00:00", claim

        # Idempotent: replaying the same event ingests nothing new.
        again = consumer.ingest(events, ledger, "wired-test")
        assert again["ingested"] == 0, again


def test_replay_events_cli():
    """`skill-os replay-events` flows TASK_FAILED events into the ledger from
    the CLI — no manual consumer invocation. Absent stream -> exit 0
    (nothing to replay); malformed stream -> non-zero with nothing ingested
    (fail loud); well-formed event -> exit 0 with the REGRESSED flip and the
    auto-detected git head bound to the evidence; a missing consumer tree ->
    fail loud with a clear error (the CI path). Hermetic temp fixtures;
    zero spend."""
    import subprocess
    import tempfile

    cli_py = Path(__file__).resolve().parent.parent / "cli.py"
    consumer_path = Path.home() / ".hermes" / "skills" / "sovereign-verification" / "scripts" / "replay_feedback_events.py"
    if not consumer_path.exists():
        raise _Skip(
            "sovereign-verification skill tree not in this checkout — the "
            "replay CLI leg runs locally where the consumer lives")

    with tempfile.TemporaryDirectory(prefix="replay-cli-") as tmp:
        tmp = Path(tmp)
        events = tmp / "events.jsonl"
        events.write_text(json.dumps({
            "event": "TASK_FAILED", "version": "1.0", "task_id": "task_wired_cli",
            "goal": "keep the pipeline green", "failed_step": "pipeline", "failed_index": 0,
            "error": "verify contains 'green'", "attempt": 2, "max_attempts": 3,
            "decision": "REPLAN", "dag": [{"skill": "pipeline", "args": {}}],
            "revised_dag": [{"skill": "pipeline", "args": {"force": True}}],
            "ts": "2026-08-10T17:00:00+00:00",
        }) + "\n")
        bad = tmp / "bad.jsonl"
        bad.write_text('{"event": "TASK_FAILED", "ts": "x"}\nnot-json\n')

        def run(*args):
            return subprocess.run(
                [sys.executable, str(cli_py), "replay-events", *args],
                capture_output=True, text=True, timeout=60,
            )

        # Absent stream: exit 0 (a gate/CLI run before the first failure is
        # not an incident).
        p0 = run("--events", str(tmp / "missing.jsonl"), "--ledger-dir", str(tmp / "ledger0"))
        assert p0.returncode == 0, p0.stdout + p0.stderr
        assert "nothing to replay" in p0.stdout, p0.stdout

        # Malformed stream: non-zero exit, nothing ingested.
        p1 = run("--events", str(bad), "--ledger-dir", str(tmp / "ledger1"))
        assert p1.returncode != 0, p1.stdout + p1.stderr
        assert "FAILED" in p1.stdout, p1.stdout
        assert not (tmp / "ledger1" / "claims.json").exists(), "malformed stream must ingest nothing"

        # Well-formed event: exit 0, REGRESSED flip, auto-bound git head.
        ledger = tmp / "ledger"
        ledger.mkdir(parents=True, exist_ok=True)
        (ledger / "claims.json").write_text(json.dumps({"claims": [{
            "claim_id": "claim:ok:task:task_wired_cli", "subject": "task:task_wired_cli",
            "text": '"task:task_wired_cli" completes without failure',
            "verification_tier": "T3", "verdict": "VERIFIED",
            "negative_evidence": [], "first_negative_evidence_at": None,
            "last_negative_evidence_at": None,
        }], "generated_at": None}, indent=2) + "\n")
        p2 = run("--events", str(events), "--ledger-dir", str(ledger))
        assert p2.returncode == 0, p2.stdout + p2.stderr
        assert "1 ingested" in p2.stdout, p2.stdout
        reg = json.loads((ledger / "claims.json").read_text())
        claim = {c["claim_id"]: c for c in reg["claims"]}["claim:ok:task:task_wired_cli"]
        assert claim["verdict"] == "REGRESSED", claim
        expected_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(cli_py.parent),
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        art = json.loads(list((ledger / "evidence" / "negative").glob("*.json"))[0].read_text())
        assert art["git_head"] == expected_head, art  # evidence bound to repo identity

        # Missing consumer tree (CI has no sovereign-verification sibling):
        # fail loud with a clear error rather than pretending to replay.
        import cli
        saved = cli.CONSUMER_PATH
        cli.CONSUMER_PATH = tmp / "no-such-consumer.py"
        try:
            rc = cli.cmd_replay_events(["--events", str(events), "--ledger-dir", str(tmp / "ledger2")])
            assert rc != 0, rc
        finally:
            cli.CONSUMER_PATH = saved


try:
    import pytest  # present under mutmut's pytest runner; absent in zero-dep use
    _PYTEST_SKIP_EXC = pytest.skip.Exception
except ImportError:  # pragma: no cover
    _PYTEST_SKIP_EXC = Exception


class _Skip(_PYTEST_SKIP_EXC):
    """Raised by a test whose environment-dependent legs are inapplicable in
    the current checkout (e.g. sibling trees CI doesn't clone). The runner
    prints a visible SKIP line and counts the test as passed — the test was
    not violated, it was not applicable. On the local machine every test runs
    at full strength, so a skip here always indicates an environment gap,
    never a hidden failure.

    Under mutmut the suite is driven by pytest: a raised pytest.skip.Exception
    is a real skip, while a plain Exception would fail the baseline run (-x
    stops at the first one) and poison every mutant verdict. The standalone
    runner catches _Skip directly either way."""


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
        test_route_stubbed_dispatch,
        test_vault_check_first_routable,
        test_registry_copies_in_sync,
        test_sovereign_verification_routable,
        test_reality_loop_pass_no_events,
        test_reality_loop_failure_blocks_honestly,
        test_reality_loop_fallback_recovers,
        test_reality_loop_replan_recovers,
        test_reality_loop_bounded_attempts,
        test_feedback_event_contract_shape,
        test_orchestrator_replan_local_fallback,
        test_route_dispatch_failure_emits_feedback_event,
        test_replay_consumer_wired,
        test_replay_events_cli,
    ]
    passed = 0
    skipped = 0
    for test in tests:
        try:
            test()
            passed += 1
            print(f"PASS {test.__name__}")
        except _Skip as e:
            passed += 1
            skipped += 1
            print(f"SKIP {test.__name__}: {e}")
        except Exception as e:
            print(f"FAIL {test.__name__}: {e}")
    note = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{passed}/{len(tests)} passed{note}")
    sys.exit(0 if passed == len(tests) else 1)
