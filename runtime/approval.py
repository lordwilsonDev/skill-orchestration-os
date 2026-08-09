from pathlib import Path
from typing import Dict, List, Any

APPROVAL_PATH = Path.home() / ".hermes" / "skills" / "skill-orchestration-os" / "logs" / "approvals.jsonl"


class ApprovalGate:
    def __init__(self, policy: Dict[str, bool], path=None):
        self.policy = policy
        self.path = Path(path or APPROVAL_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def allow(self, skill: str, args: Dict[str, Any]) -> bool:
        key = skill.replace("-", "_")
        allowed = self.policy.get(key, True)
        entry = {"skill": skill, "args": args, "allowed": allowed}
        try:
            with open(self.path, "a") as f:
                import json
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass
        return allowed

    def set_policy(self, skill: str, allowed: bool):
        key = skill.replace("-", "_")
        self.policy[key] = allowed
