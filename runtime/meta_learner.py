from typing import List, Dict, Optional
from collections import Counter
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parent.parent / "logs" / "meta_history.jsonl"

class MetaLearner:
    def __init__(self, path=None):
        self.path = Path(path or HISTORY_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.history: List[Dict] = []
        if self.path.exists():
            try:
                for line in self.path.read_text().splitlines():
                    line = line.strip()
                    if line:
                        self.history.append(__import__("json").loads(line))
            except Exception:
                self.history = []

    def record(self, task: str, dag: List[Dict], outcome: Dict):
        entry = {"task": task, "dag": dag, "outcome": outcome}
        self.history.append(entry)
        try:
            with open(self.path, "a") as f:
                f.write(__import__("json").dumps(entry) + "\n")
        except Exception:
            pass

    def suggest(self, task: str) -> List[Dict]:
        counts = Counter()
        for record in self.history:
            if record["task"] == task:
                for step in record["dag"]:
                    counts[step.get("skill")] += 1
        if not counts:
            return []
        return [{"skill": s, "args": {}} for s, _ in counts.most_common(3)]

    def failure_rate(self, skill: str) -> float:
        total = 0
        errors = 0
        for record in self.history:
            for step in record.get("dag", []):
                if step.get("skill") == skill:
                    total += 1
                    out = record.get("outcome", {})
                    if isinstance(out, dict) and out.get("status") == "error":
                        errors += 1
                    elif isinstance(out, str) and out.startswith("Traceback"):
                        errors += 1
        return errors / total if total else 0.0
