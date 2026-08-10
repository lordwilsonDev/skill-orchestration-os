from typing import List, Dict, Any
import os
import json

from runtime.deepseek import deepseek_chat

PROMPT_TMPL = (
    "Return a JSON array of skill steps for this task. "
    'Each step: {{"skill": "skill-name", "args": {{}}}}. '
    "Available skills (name — description):\n{skills}\n"
    "Use the `route` skill when the task maps to ONE domain skill (e.g. "
    "drafting, research, n8n automation, architecture, git releases): it "
    "classifies the task and dispatches a Claude subagent. "
    'Route args: {{"task": "<original task>"}}. '
    "Task: {task}. "
    "Return ONLY valid JSON, no markdown."
)

REPLAN_PROMPT_TMPL = (
    "A skill-DAG execution failed at step {index} ({skill}). "
    "Task: {task}\n"
    "Original DAG:\n{dag}\n"
    "Failure: {error}\n\n"
    "Return a revised JSON array of skill steps. Preserve steps that work; "
    "fix or replace the failed step. "
    'Each step: {{"skill": "skill-name", "args": {{}}}}. '
    "Available skills (name — description):\n{skills}\n"
    "Return ONLY valid JSON, no markdown."
)


class Orchestrator:
    def __init__(self, registry):
        self.registry = registry
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY")

    def plan(self, task: str) -> List[Dict[str, Any]]:
        if self.deepseek_key:
            dag = self._plan_with_deepseek(task)
            if dag:
                return dag
        return self._plan_local(task)

    def _build_prompt(self, task: str) -> str:
        lines = []
        for name, contract in self.registry.all().items():
            desc = getattr(contract, "description", "") or ""
            lines.append(f"{name} — {desc}" if desc else name)
        return PROMPT_TMPL.format(skills="\n".join(lines), task=task)

    def _plan_with_deepseek(self, task: str) -> List[Dict[str, Any]]:
        try:
            content = deepseek_chat(self._build_prompt(task), api_key=self.deepseek_key)
            parsed = json.loads(content)
        except Exception:
            # Silent fallback contract preserved: planner failure -> local policy.
            return []
        # The model occasionally wraps the array in an object (e.g.
        # {"steps": [...]}). Normalize that; reject any other non-list shape so
        # the executor never iterates dict keys as steps.
        if isinstance(parsed, dict):
            for key in ("steps", "dag", "plan"):
                if isinstance(parsed.get(key), list):
                    parsed = parsed[key]
                    break
            else:
                return []
        if not isinstance(parsed, list):
            return []
        return [s for s in parsed if isinstance(s, dict) and s.get("skill")]

    def _plan_local(self, task: str) -> List[Dict[str, Any]]:
        return [{"skill": "echo", "args": {"message": task}}]

    # --- replanning (the 02 -> 04 link for the reality loop) ---

    def replan(self, task: str, dag: List[Dict[str, Any]], failure: dict) -> List[Dict[str, Any]]:
        """Revise a DAG after a step failed verification. DeepSeek first
        (failure context in the prompt); deterministic local policy as
        fallback: fallback_args -> merged retry; otherwise honest [] (the
        loop blocks upstream — we never guess which steps to drop)."""
        if self.deepseek_key:
            revised = self._replan_with_deepseek(task, dag, failure)
            if revised:
                return revised
        return self._replan_local(task, dag, failure)

    def _build_replan_prompt(self, task: str, dag: List[Dict[str, Any]], failure: dict) -> str:
        lines = []
        for name, contract in self.registry.all().items():
            desc = getattr(contract, "description", "") or ""
            lines.append(f"{name} — {desc}" if desc else name)
        return REPLAN_PROMPT_TMPL.format(
            skills="\n".join(lines),
            task=task,
            index=failure.get("index", "?"),
            skill=failure.get("step", {}).get("skill", "?") if isinstance(failure.get("step"), dict) else failure.get("step", "?"),
            dag=json.dumps(dag, indent=2),
            error=failure.get("error", "?"),
        )

    def _replan_with_deepseek(self, task: str, dag: List[Dict[str, Any]], failure: dict) -> List[Dict[str, Any]]:
        try:
            content = deepseek_chat(self._build_replan_prompt(task, dag, failure), api_key=self.deepseek_key)
            parsed = json.loads(content)
        except Exception:
            return []
        if isinstance(parsed, dict):
            for key in ("steps", "dag", "plan"):
                if isinstance(parsed.get(key), list):
                    parsed = parsed[key]
                    break
            else:
                return []
        if not isinstance(parsed, list):
            return []
        return [s for s in parsed if isinstance(s, dict) and s.get("skill")]

    def _replan_local(self, task: str, dag: List[Dict[str, Any]], failure: dict) -> List[Dict[str, Any]]:
        """Deterministic fallback: apply fallback_args if the failed step has
        them; otherwise honest [] — the loop blocks rather than guess."""
        step = failure.get("step") or {}
        if not isinstance(step, dict) or not step.get("fallback_args"):
            return []
        steps = [dict(s) for s in dag]
        merged = {**step.get("args", {}), **step["fallback_args"]}
        replaced = {**step, "args": merged}
        replaced.pop("fallback_args", None)
        idx = min(failure.get("index", 0), max(0, len(steps) - 1))  # defensive clamp
        steps[idx] = replaced
        return steps
