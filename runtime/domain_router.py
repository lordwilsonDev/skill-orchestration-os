"""domain_router — the routing front-end of skill-orchestration-os.

One task -> one skill -> dispatch a Claude subagent on that skill -> record.

This is the canonical home of the domain-router (folded in from the standalone
~/.hermes/domain-router project). It reuses the shared DeepSeek transport
(runtime/deepseek.py) — the SAME transport the Orchestrator planner uses — so
there is exactly one copy of the request construction. It plugs into the
Executor as the `route` skill (a DAG step) and into cli.py as `route <task>`.

Flow:
  1. Load domains.json (the routing table; built by build_registry.py).
  2. Classify via DeepSeek — fail-loud (spec §9), never the orchestrator's
     silent fallback. Returns exactly one {skill_id, reason}.
  3. Validate skill_id (unknown ids rejected with closest matches).
  4. Dispatch: `cd <skill.dir> && claude -p ...` — directory-based, so the
     subagent resolves ./SKILL.md and relative script paths.
  5. Record one line to the route audit log.

Spec: ~/.hermes/domain-router/docs/superpowers/specs/2026-08-09-domain-router-design.md
"""

from __future__ import annotations

import difflib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from runtime.deepseek import deepseek_chat, strip_fences

HOME_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = HOME_DIR / "domains.json"
AUDIT_PATH = HOME_DIR / "logs" / "route_audit.jsonl"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
# Permission posture for headless dispatch: acceptEdits (file edits allowed)
# plus explicit tools so skills that run scripts or write files work.
CLAUDE_FLAGS = [
    "--permission-mode", "acceptEdits",
    "--allowedTools", "Read", "Glob", "Grep", "Bash", "Write", "Edit",
    "--output-format", "json",
]


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        raise SystemExit(f"no {REGISTRY_PATH} — run: python build_registry.py --rebuild")
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def registry_by_id(registry: dict) -> dict[str, dict]:
    return {e["skill_id"]: e for e in registry["skills"]}


# --------------------------------------------------------------------------
# Classification — transport REUSED from runtime/deepseek.py (the same
# deepseek_chat the Orchestrator planner uses). Error contract: fail-loud.
# --------------------------------------------------------------------------

def _deepseek_prompt(task: str, registry: dict) -> str:
    lines: list[str] = []
    current_container = None
    for entry in registry["skills"]:
        if entry["container"] != current_container:
            current_container = entry["container"]
            lines.append(f"[{current_container}]")
        lines.append(f"{entry['skill_id']} — {entry['description'][:120]}")
    joined = "\n".join(lines)
    return (
        "You are a task router. Given a task and a registry of skills (grouped by "
        "container), choose the SINGLE best skill to accomplish the task.\n\n"
        "Return ONLY valid JSON, no markdown, of the form "
        '{"skill_id": "<exact skill_id from the registry>", "reason": "<one short sentence>"}.\n\n'
        f"Registry:\n{joined}\n\nTask: {task}"
    )


def _post_deepseek(prompt: str, api_key: str) -> str:
    """Raw transport call — thin wrapper over the shared deepseek_chat."""
    return deepseek_chat(prompt, api_key=api_key)


def classify(task: str, registry: dict, api_key: str) -> dict:
    """Return {"skill_id", "reason"}. Raises on any failure (fail-loud)."""
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set — set it, or bypass classification with --domain <skill_id>"
        )
    try:
        content = strip_fences(_post_deepseek(_deepseek_prompt(task, registry), api_key))
    except Exception as exc:  # network / HTTP / parse — fail loud, never guess
        raise RuntimeError(f"DeepSeek classification failed: {exc}") from exc
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DeepSeek returned non-JSON: {content[:200]!r}") from exc
    skill_id = str(result.get("skill_id", "")).strip()
    reason = str(result.get("reason", "")).strip()
    if not skill_id:
        raise RuntimeError(f"DeepSeek response missing skill_id: {result!r}")
    return {"skill_id": skill_id, "reason": reason}


def validate_skill_id(skill_id: str, by_id: dict[str, dict]) -> tuple[str, str]:
    """Return (skill_id, warning). Raises SystemExit for unknown ids."""
    if skill_id in by_id:
        return skill_id, ""
    known = list(by_id)
    close = difflib.get_close_matches(skill_id, known, n=3, cutoff=0.5)
    hint = f"  closest: {', '.join(close)}" if close else ""
    raise SystemExit(f"rejected unknown skill_id: {skill_id!r}{hint}")


# --------------------------------------------------------------------------
# Dispatch — directory-based claude -p
# --------------------------------------------------------------------------

def build_dispatch_command(entry: dict, task: str) -> list[str]:
    """The exact argv for the subagent, run with cwd=entry['dir']."""
    prompt = (
        "Load and follow the skill at ./SKILL.md (the skill in this directory). "
        f"Use it to accomplish the task below.\n\nTask: {task}"
    )
    return [CLAUDE_BIN, "-p", *CLAUDE_FLAGS, prompt]


def _summarize_output(output: str) -> str:
    """Extract the subagent's answer from claude -p's JSON envelope when
    present (--output-format json); otherwise use raw output, truncated."""
    text = output.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(payload, dict) and "result" in payload:
            text = str(payload["result"]).strip()
        elif isinstance(payload, dict) and "error" in payload:
            text = str(payload["error"]).strip()
    if len(text) > 500:
        text = text[:500] + "…"
    return text


def dispatch(entry: dict, task: str, dry_run: bool) -> tuple[int, str]:
    cmd = build_dispatch_command(entry, task)
    shell_line = f"cd {entry['dir']} && " + " ".join(f'"{c}"' if " " in c else c for c in cmd)
    if dry_run:
        print(f"domain: {entry['skill_id']}")
        print(f"command: {shell_line}")
        return 0, "(dry-run — not executed)"
    try:
        proc = subprocess.run(
            cmd, cwd=entry["dir"], capture_output=True, text=True, timeout=600
        )
        summary = _summarize_output(proc.stdout or proc.stderr or "")
        return proc.returncode, summary
    except FileNotFoundError:
        return 127, f"claude not found on PATH ({CLAUDE_BIN!r})"
    except subprocess.TimeoutExpired:
        return 124, "dispatch timed out after 600s"


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

def append_audit(record: dict, audit_path: Path | None = None) -> None:
    record["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = audit_path or AUDIT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)  # fresh checkouts have no logs/
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# DomainRouter — the routing front-end object (executor skill + cli route)
# --------------------------------------------------------------------------

class DomainRouter:
    """The routing front-end: classify a task to exactly one skill, dispatch
    a Claude subagent from that skill's directory, and record the outcome.

    Wired into the Executor as the `route` skill and into cli.py as
    `route <task>` (and `--domain <skill_id>` to bypass classification).
    """

    def __init__(self, registry_path: Path | None = None,
                 audit_path: Path | None = None,
                 api_key: str | None = None):
        self.registry_path = registry_path or REGISTRY_PATH
        self.audit_path = audit_path or AUDIT_PATH
        self.api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")

    def route(self, task: str, dry_run: bool = False,
              domain: str | None = None) -> dict:
        """Run the full routing loop; returns the audit record dict."""
        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        by_id = registry_by_id(registry)

        # Resolve the target skill (manual override or classification).
        if domain:
            skill_id = domain
            reason = "manual --domain override"
            if skill_id not in by_id:
                leaves = [sid for sid, e in by_id.items() if e["container"] == skill_id]
                if len(leaves) == 1:
                    skill_id = leaves[0]
                    reason = "manual --domain (container resolved unambiguously)"
                else:
                    validate_skill_id(skill_id, by_id)  # raises with closest matches
        else:
            result = classify(task, registry, self.api_key)
            skill_id = result["skill_id"]
            reason = result.get("reason", "")
            skill_id, _warn = validate_skill_id(skill_id, by_id)

        entry = by_id[skill_id]
        exit_code, summary = dispatch(entry, task, dry_run)

        record = {
            "task": task,
            "skill_id": entry["skill_id"],
            "container": entry["container"],
            "reason": reason,
            "exit_code": exit_code,
            "result_summary": summary,
        }
        append_audit(record, audit_path=self.audit_path)
        return record


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_list(args) -> int:
    registry = load_registry()
    if args.container:
        matches = [e for e in registry["skills"] if e["container"] == args.container]
        if not matches:
            print(f"no skills in container {args.container!r}")
            return 1
        for e in matches:
            print(f"{e['skill_id']} — {e['description'][:90]}")
        return 0
    print(f"{registry['count']} skills across {len(registry['containers'])} containers:")
    for c in registry["containers"]:
        n = sum(1 for e in registry["skills"] if e["container"] == c)
        print(f"  {c} ({n})")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="router.py",
        description="Classify a task to exactly one skill and dispatch a Claude subagent.",
    )
    parser.add_argument("task", nargs="?", help="the task to route")
    parser.add_argument("--domain", metavar="SKILL_ID", help="skip classification; dispatch this skill directly")
    parser.add_argument("--dry-run", action="store_true", help="classify + print the claude command, do not execute")
    parser.add_argument("--rebuild", action="store_true", help="regenerate domains.json (needs build_registry.py)")
    parser.add_argument("--list", nargs="?", const="__all__", metavar="CONTAINER", help="browse the registry")
    args = parser.parse_args(argv)

    if args.rebuild:
        import build_registry
        return build_registry.main(["--rebuild"])
    if args.list:
        args.container = None if args.list == "__all__" else args.list
        return cmd_list(args)
    if not args.task:
        parser.print_help()
        return 2

    record = DomainRouter().route(args.task, dry_run=args.dry_run, domain=args.domain)
    if not args.dry_run:
        print(f"dispatched: {record['skill_id']} (exit {record['exit_code']})")
    return 0 if record["exit_code"] == 0 else record["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
