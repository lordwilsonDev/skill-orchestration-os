import json, datetime
from pathlib import Path
from typing import Any, Dict

class AuditLogger:
    def __init__(self, log_dir=None):
        self.log_dir = Path(log_dir or Path(__file__).resolve().parent.parent / "logs")
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
            f.write(json.dumps(entry) + "\n")

    def replay(self, limit: int = 100):
        path = self.log_dir / "audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text().splitlines()[-limit:] if x.strip()]
