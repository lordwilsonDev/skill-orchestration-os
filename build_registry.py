#!/usr/bin/env python3
"""build_registry.py — build the domain-router routing table.

Scans ~/.hermes/skills/**/SKILL.md and emits domains.json: one entry per leaf
skill (any directory containing a SKILL.md), identified by its path relative
to the skills root. Deterministic (sorted) so the committed file is diffable.

Description fallback chain (spec §4):
  1. `description:` frontmatter (YAML), else
  2. first non-heading, non-blank body paragraph (trimmed ~200 chars), else
  3. the skill name.

Hidden directories (any path segment starting with `.`) are excluded, which
drops `.hub`, `.curator_backups` and any future hidden support dirs.

Spec: ~/.hermes/domain-router/docs/superpowers/specs/2026-08-09-domain-router-design.md
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILLS_ROOT = Path(os.environ.get("HERMES_SKILLS", str(Path.home() / ".hermes" / "skills")))
OUT_PATH = Path(__file__).resolve().parent / "domains.json"

_DESCRIPTION_RE = re.compile(r"^description:\s*(.+)$", re.MULTILINE)
_NAME_RE = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
_ID_RE = re.compile(r"^id:\s*(.+)$", re.MULTILINE)
_BODY_PARA_RE = re.compile(
    r"^\s*(?!#|[-*>\d.\s]+$)(.{40,})$", re.MULTILINE
)  # first substantive line that is not a heading/list/toc


def _frontmatter(text: str) -> str:
    """Return the YAML frontmatter block, or '' if absent."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    return m.group(1) if m else ""


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().strip('"').strip("'").strip()


def _frontmatter_field(frontmatter: str, pattern: re.Pattern[str]) -> str:
    m = pattern.search(frontmatter)
    return _clean(m.group(1)) if m else ""


def _body_description(text: str) -> str:
    """First non-heading, non-blank paragraph of the SKILL.md body."""
    for m in _BODY_PARA_RE.finditer(text):
        para = m.group(1).strip()
        if len(para) >= 40 and not para.startswith("```"):
            return para[:200].rstrip()
    return ""


def parse_skill(md: Path, root: Path) -> dict | None:
    """Parse one SKILL.md into a registry entry (or None if excluded)."""
    parts = md.relative_to(root).parts  # e.g. ('engineering', 'foo', 'SKILL.md')
    if any(part.startswith(".") for part in parts[:-1]):
        return None  # hidden support dirs
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    skill_dir = md.parent
    skill_id = "/".join(parts[:-1])

    front = _frontmatter(text)
    name = _frontmatter_field(front, _NAME_RE) or _frontmatter_field(front, _ID_RE)
    desc = _frontmatter_field(front, _DESCRIPTION_RE)
    if not desc:
        bare = _DESCRIPTION_RE.search(text[:800])
        desc = _clean(bare.group(1)) if bare else ""
    if not desc:
        desc = _body_description(text)
    if not desc:
        desc = name or skill_id.rsplit("/", 1)[-1]
    return {
        "skill_id": skill_id,
        "name": name or skill_id.rsplit("/", 1)[-1],
        "description": desc[:300],
        "container": parts[0],
        "dir": str(skill_dir),
        "skill_md": str(md),
    }


def build() -> dict:
    entries: list[dict] = []
    for md in sorted(SKILLS_ROOT.rglob("SKILL.md")):
        entry = parse_skill(md, SKILLS_ROOT)
        if entry:
            entries.append(entry)
    containers = sorted({e["container"] for e in entries})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(entries),
        "containers": containers,
        "skills": entries,
    }


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    rebuild = "--rebuild" in args
    data = build()
    if rebuild:
        OUT_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUT_PATH} ({data['count']} skills, {len(data['containers'])} containers)")
    else:
        blank = [e["skill_id"] for e in data["skills"] if not e["description"].strip()]
        print(f"{data['count']} skills across {len(data['containers'])} containers")
        if blank:
            print(f"WARNING: {len(blank)} entries with blank descriptions: {blank[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
