"""Runner: web_extract via Hermes-compatible backend."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_ROOT = Path.home() / ".hermes" / "skills" / "skill-orchestration-os"
sys.path.insert(0, str(SKILL_ROOT))


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python -m skill_orchestration_os.runners.web_extract <urls_json> [char_limit]", file=sys.stderr)
        return 2
    urls = json.loads(sys.argv[1])
    char_limit = int(sys.argv[2]) if len(sys.argv) > 2 else 15000
    print(json.dumps({"urls": urls, "char_limit": char_limit, "results": [], "note": "web_extract backend not configured"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
