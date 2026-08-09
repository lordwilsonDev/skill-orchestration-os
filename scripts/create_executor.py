from pathlib import Path
from registry.contracts import SkillRegistry, SkillContract
from executor import Executor
from audit import AuditLogger

AGENT_REACH_DIR = Path.home() / "agent-reach"

_CONTRACTS = [
    SkillContract(
        name="agent-reach-doctor",
        inputs=[],
        outputs=["report"],
        side_effects=["exec", "read"],
        version="0.1.0",
        timeout_s=120,
    ),
    SkillContract(
        name="agent-reach-configure",
        inputs=["key", "value"],
        outputs=["success", "message"],
        side_effects=["write", "exec"],
        version="0.1.0",
        timeout_s=120,
    ),
    SkillContract(
        name="agent-reach-scrape",
        inputs=["platform", "query", "limit?"],
        outputs=["results"],
        side_effects=["network", "exec", "write"],
        version="0.1.0",
        timeout_s=180,
    ),
    SkillContract(
        name="obsidian-search",
        inputs=["query", "limit?"],
        outputs=["results"],
        side_effects=["read"],
        version="0.1.0",
        timeout_s=120,
    ),
    SkillContract(
        name="obsidian-read",
        inputs=["path"],
        outputs=["content"],
        side_effects=["read"],
        version="0.1.0",
        timeout_s=60,
    ),
    SkillContract(
        name="obsidian-write",
        inputs=["path", "content"],
        outputs=["bytes_written", "verified"],
        side_effects=["write"],
        version="0.1.0",
        timeout_s=60,
    ),
    SkillContract(
        name="n8n-list-workflows",
        inputs=[],
        outputs=["workflows"],
        side_effects=["network", "read"],
        version="0.1.0",
        timeout_s=60,
    ),
    SkillContract(
        name="n8n-trigger-workflow",
        inputs=["workflow_id"],
        outputs=["execution_id", "status"],
        side_effects=["network", "exec"],
        version="0.1.0",
        timeout_s=120,
    ),
    SkillContract(
        name="shell",
        inputs=["cmd", "timeout?"],
        outputs=["stdout", "stderr", "returncode"],
        side_effects=["exec"],
        version="0.1.0",
        timeout_s=120,
    ),
    SkillContract(
        name="read-file",
        inputs=["path", "offset?", "limit?"],
        outputs=["content", "total_lines"],
        side_effects=["read"],
        version="0.1.0",
        timeout_s=60,
    ),
    SkillContract(
        name="write-file",
        inputs=["path", "content"],
        outputs=["bytes_written", "verified"],
        side_effects=["write"],
        version="0.1.0",
        timeout_s=60,
    ),
    SkillContract(
        name="web-search",
        inputs=["query", "limit?"],
        outputs=["results"],
        side_effects=["network"],
        version="0.1.0",
        timeout_s=60,
    ),
    SkillContract(
        name="web-extract",
        inputs=["urls", "char_limit?"],
        outputs=["results"],
        side_effects=["network", "read"],
        version="0.1.0",
        timeout_s=120,
    ),
]


def create_registry() -> SkillRegistry:
    registry = SkillRegistry()
    for contract in _CONTRACTS:
        registry.register(contract)
    return registry


def create_executor(registry: SkillRegistry, audit: AuditLogger) -> Executor:
    return Executor(registry, audit_logger=audit)
