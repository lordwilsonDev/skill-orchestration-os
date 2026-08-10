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
            router = dr.DomainRouter(registry_path=reg, audit_path=tmp / "audit.jsonl")
            record = router.route("do the thing", dry_run=False, domain="stub/skill")
            assert record["exit_code"] == 0
            assert "stubbed claude completed the task" in record["result_summary"]
            lines = (tmp / "audit.jsonl").read_text().strip().splitlines()
            assert len(lines) == 1
            assert json.loads(lines[0])["exit_code"] == 0
            assert json.loads(lines[0])["skill_id"] == "stub/skill"

            # Failure path: fake claude exits 1 -> the router records it, not masks it.
            dr.CLAUDE_BIN = str(stub_fail)
            rec2 = dr.DomainRouter(registry_path=reg, audit_path=tmp / "audit.jsonl").route(
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


class _Skip(Exception):
    """Raised by a test whose environment-dependent legs are inapplicable in
    the current checkout (e.g. sibling trees CI doesn't clone). The runner
    prints a visible SKIP line and counts the test as passed — the test was
    not violated, it was not applicable. On the local machine every test runs
    at full strength, so a skip here always indicates an environment gap,
    never a hidden failure."""


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
