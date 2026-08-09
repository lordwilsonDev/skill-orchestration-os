"""Runner: obsidian_search via direct Vault filesystem search."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from collections import Counter

SKILL_ROOT = Path.home() / ".hermes" / "skills" / "skill-orchestration-os"
sys.path.insert(0, str(SKILL_ROOT))

DEFAULT_VAULT = Path.home() / "Documents" / "Vault"


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except Exception:
        return ""


def _score(text: str, query_terms: list[str]) -> int:
    lower = text.lower()
    return sum(lower.count(t.lower()) for t in query_terms)


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "missing query", "results": []}, ensure_ascii=False))
        return 2
    query = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    vault = DEFAULT_VAULT if DEFAULT_VAULT.exists() else Path(".")
    terms = [t.strip() for t in query.split() if t.strip()]
    scored = []
    for path in vault.rglob("*.md"):
        text = _read_text_safe(path)
        if not text:
            continue
        score = _score(text, terms)
        if score > 0:
            scored.append((score, str(path), text.splitlines()[0].strip()))
    scored.sort(reverse=True)
    results = [
        {"path": p, "title": t, "score": s}
        for s, p, t in scored[:limit]
    ]
    print(json.dumps({"query": query, "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
