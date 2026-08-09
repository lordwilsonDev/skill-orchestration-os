#!/usr/bin/env python3
"""Scaffold Skill Orchestration OS runtime skeleton."""
from pathlib import Path

ROOT = Path.home() / ".hermes" / "skills" / "skill-orchestration-os"

registry_init = "from .contracts import SkillRegistry, SkillContract\n"

registry_contracts = '''from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class SkillContract:
    name: str
    inputs: List[str]
    outputs: List[str]
    side_effects: List[str]
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)
    version: str = "0.1.0"
    timeout_s: int = 120

class SkillRegistry:
    def __init__(self):
        self._contracts: Dict[str, SkillContract] = {}

    def register(self, contract: SkillContract):
        self._contracts[contract.name] = contract

    def get(self, name: str) -> SkillContract:
        return self._contracts[name]

    def all(self) -> Dict[str, SkillContract]:
        return dict(self._contracts)
'''

orchestrator = '''from typing import List, Dict, Any
import os

PROMPT_TMPL = (
    "Return a JSON array of skill steps for this task. "
    'Each step: {"skill": "skill-name", "args": {}}. '
    "Available skills: {skills}. Task: {task}. "
    "Return ONLY valid JSON, no markdown."
)

class Orchestrator:
    def __init__(self, registry):
        self.registry = registry
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        self.deepseek_url = "https://api.deepseek.com/v1/chat/completions"

    def plan(self, task: str) -> List[Dict[str, Any]]:
        if self.deepseek_key:
            dag = self._plan_with_deepseek(task)
            if dag:
                return dag
        return self._plan_local(task)

    def _plan_with_deepseek(self, task: str) -> List[Dict[str, Any]]:
        import urllib.request, json
        prompt = PROMPT_TMPL.format(
            skills=", ".join(self.registry.all().keys()), task=task
        )
        try:
            req = urllib.request.Request(
                self.deepseek_url,
                data=json.dumps({
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 500
                }).encode(),
                headers={
                    "Authorization": "Bearer " + self.deepseek_key,
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("```", 1)[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            return json.loads(content)
        except Exception:
            return []

    def _plan_local(self, task: str) -> List[Dict[str, Any]]:
        return [{"skill": "echo", "args": {"message": task}}]
'''

executor = '''from typing import List, Dict, Any

class Executor:
    def __init__(self, registry, audit_logger=None, workdir=None):
        self.registry = registry
        self.audit = audit_logger

    def run(self, dag: List[Dict[str, Any]]) -> Dict[str, Any]:
        context = {}
        for step in dag:
            skill = step.get("skill")
            args = step.get("args", {})
            try:
                output = self._run_skill(skill, args, context)
                context[f"{skill}_out"] = output
                if self.audit:
                    self.audit.log(skill, "success", args, output)
            except Exception as e:
                if self.audit:
                    self.audit.log(skill, "error", args, str(e))
                raise
        return context

    def _run_skill(self, skill: str, args: Dict, context: Dict) -> Any:
        if skill == "echo":
            return args.get("message", "")
        raise ValueError(f"Unknown skill: {skill}")
'''

audit = '''import json, datetime
from pathlib import Path

class AuditLogger:
    def __init__(self, log_dir=None):
        self.log_dir = Path(log_dir or Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, skill: str, status: str, args: Dict, output):
        entry = {
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
            "skill": skill,
            "status": status,
            "args": args,
            "output": str(output)[:2000]
        }
        with open(self.log_dir / "audit.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\\n")

    def replay(self, limit: int = 100):
        path = self.log_dir / "audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text().splitlines()[-limit:] if x.strip()]
'''

omni_route = '''from typing import Callable, Dict, Any

class OmniRoute:
    def __init__(self):
        self._handlers: Dict[str, Callable] = {}

    def register(self, skill: str, handler: Callable):
        self._handlers[skill] = handler

    def send(self, skill: str, payload: Dict[str, Any]) -> Any:
        if skill not in self._handlers:
            raise ValueError(f"No handler for {skill}")
        return self._handlers[skill](payload)
'''

meta_learner = '''from typing import List, Dict
from collections import Counter

class MetaLearner:
    def __init__(self):
        self.history: List[Dict] = []

    def record(self, task: str, dag: List[Dict], outcome: Dict):
        self.history.append({"task": task, "dag": dag, "outcome": outcome})

    def suggest(self, task: str) -> List[Dict]:
        counts = Counter()
        for record in self.history:
            if record["task"] == task:
                for step in record["dag"]:
                    counts[step.get("skill")] += 1
        if not counts:
            return []
        return [{"skill": s, "args": {}} for s, _ in counts.most_common(3)]
'''

cli = '''#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".hermes" / "skills" / "skill-orchestration-os"))

from registry.contracts import SkillRegistry, SkillContract
from orchestrator import Orchestrator
from executor import Executor
from audit import AuditLogger
from meta_learner import MetaLearner

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: skill-os <task>")
        sys.exit(0)

    task = " ".join(sys.argv[1:])
    registry = SkillRegistry()
    registry.register(SkillContract(name="echo", inputs=["message"], outputs=["text"], side_effects=[]))

    audit = AuditLogger()
    orchestrator = Orchestrator(registry)
    executor = Executor(registry, audit_logger=audit)
    learner = MetaLearner()

    dag = orchestrator.plan(task)
    print(f"DAG: {dag}")
    result = executor.run(dag)
    learner.record(task, dag, {"status": "ok", "result": result})
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
'''

files = {
    "registry/__init__.py": registry_init,
    "registry/contracts.py": registry_contracts,
    "orchestrator.py": orchestrator,
    "executor.py": executor,
    "audit.py": audit,
    "omni_route.py": omni_route,
    "meta_learner.py": meta_learner,
    "cli.py": cli,
}

for rel, content in files.items():
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f"Wrote {path}")

print("Scaffold complete.")
