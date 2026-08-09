#!/usr/bin/env python3
"""Skill Orchestration OS bootstrap."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime.registry.contracts import SkillRegistry, SkillContract

def create_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(SkillContract(
        name="shell",
        inputs=["cmd"],
        outputs=["stdout", "stderr", "exit_code"],
        side_effects=["exec"],
        version="0.1.0",
    ))
    registry.register(SkillContract(
        name="read-file",
        inputs=["path"],
        outputs=["content"],
        side_effects=["read"],
        version="0.1.0",
    ))
    registry.register(SkillContract(
        name="write-file",
        inputs=["path", "content"],
        outputs=["bytes_written"],
        side_effects=["write"],
        version="0.1.0",
    ))
    registry.register(SkillContract(
        name="agent-reach-doctor",
        inputs=[],
        outputs=["report"],
        side_effects=["network"],
        version="0.1.0",
    ))
    registry.register(SkillContract(
        name="n8n-list-workflows",
        inputs=[],
        outputs=["workflows"],
        side_effects=["api"],
        version="0.1.0",
    ))
    registry.register(SkillContract(
        name="agent-reach-scrape",
        inputs=["platform", "query", "limit"],
        outputs=["results"],
        side_effects=["network", "write"],
        version="0.1.0",
    ))
    return registry

__all__ = ["create_registry"]
