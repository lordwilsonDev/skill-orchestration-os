from typing import Any, Dict
from .contracts import SkillContract
from .contracts import SkillRegistry as BaseRegistry

_HERMES_TOOL_REGISTRY: Dict[str, SkillContract] = {}

def _register_tool(name: str, description: str, inputs, outputs, side_effects):
    _HERMES_TOOL_REGISTRY[name] = SkillContract(
        name=name,
        inputs=inputs,
        outputs=outputs,
        side_effects=side_effects,
        preconditions=[],
        postconditions=[],
        version="0.1.0",
        timeout_s=60,
    )

# Hermes/terminal tools
_register_tool(
    name="hermes_terminal",
    description="Run a shell command via Hermes terminal tool",
    inputs=["command", "timeout?"],
    outputs=["stdout", "stderr", "exit_code"],
    side_effects=["exec", "filesystem", "network"],
)
_register_tool(
    name="hermes_read_file",
    description="Read a text file via Hermes read_file tool",
    inputs=["path", "offset?", "limit?"],
    outputs=["content", "total_lines"],
    side_effects=["read"],
)
_register_tool(
    name="hermes_write_file",
    description="Write a text file via Hermes write_file tool",
    inputs=["path", "content"],
    outputs=["bytes_written", "verified"],
    side_effects=["write"],
)
_register_tool(
    name="hermes_search_files",
    description="Search files by content or name via Hermes search_files tool",
    inputs=["pattern", "target?", "path?", "file_glob?", "limit?"],
    outputs=["matches"],
    side_effects=["read"],
)
_register_tool(
    name="hermes_web_search",
    description="Search the web via Hermes web_search tool",
    inputs=["query", "limit?"],
    outputs=["results"],
    side_effects=["network"],
)
_register_tool(
    name="hermes_web_extract",
    description="Extract web page content via Hermes web_extract tool",
    inputs=["urls", "char_limit?"],
    outputs=["results"],
    side_effects=["network", "read"],
)

# Agent-Reach tools
_register_tool(
    name="agent_reach_doctor",
    description="Run Agent-Reach doctor to check channel/platform status",
    inputs=[],
    outputs=["report"],
    side_effects=["exec", "read"],
)
_register_tool(
    name="agent_reach_configure",
    description="Configure an Agent-Reach channel/platform",
    inputs=["key", "value"],
    outputs=["success", "message"],
    side_effects=["write", "exec"],
)
_register_tool(
    name="agent_reach_scrape",
    description="Scrape a supported platform via Agent-Reach backend",
    inputs=["platform", "query", "limit?"],
    outputs=["results"],
    side_effects=["network", "exec", "write"],
)

# Obsidian tools
_register_tool(
    name="obsidian_search",
    description="Search Obsidian vault via Hermes obsidian skill or vault search",
    inputs=["query", "limit?"],
    outputs=["results"],
    side_effects=["read"],
)
_register_tool(
    name="obsidian_read",
    description="Read an Obsidian vault file",
    inputs=["path"],
    outputs=["content"],
    side_effects=["read"],
)
_register_tool(
    name="obsidian_write",
    description="Write/overwrite an Obsidian vault file",
    inputs=["path", "content"],
    outputs=["bytes_written"],
    side_effects=["write"],
)

# n8n tools
_register_tool(
    name="n8n_list_workflows",
    description="List n8n workflows via REST API",
    inputs=[],
    outputs=["workflows"],
    side_effects=["network", "read"],
)
_register_tool(
    name="n8n_trigger_workflow",
    description="Trigger an n8n workflow execution by ID",
    inputs=["workflow_id"],
    outputs=["execution_id", "status"],
    side_effects=["network", "exec"],
)

def get_contract(name: str):
    return _HERMES_TOOL_REGISTRY.get(name)

def register_all(registry: BaseRegistry):
    for contract in _HERMES_TOOL_REGISTRY.values():
        registry.register(contract)
