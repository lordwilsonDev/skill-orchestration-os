from typing import List, Dict, Any
import subprocess
import sys
import json
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent


class Executor:
    def __init__(self, registry, audit_logger=None, workdir=None):
        self.registry = registry
        self.audit = audit_logger
        self.workdir = workdir or SKILL_ROOT
        self.gate_policy = {
            "n8n_trigger_workflow": True,
            "agent_reach_configure": True,
            "agent_reach_scrape": False,
            "write_file": True,
            "obsidian_write": True,
        }
        try:
            from runtime.approval import ApprovalGate
            self.gate = ApprovalGate(self.gate_policy)
        except Exception:
            self.gate = None
        try:
            from runtime.meta_learner import MetaLearner
            self._meta = MetaLearner()
        except Exception:
            self._meta = None

    def run(self, dag: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Backward-compatible entry point: execute the DAG, return the final
        context (the {skill}_out output map). The reality loop uses
        run_steps() instead, which exposes per-step results for verification."""
        context: Dict[str, Any] = {}
        for result in self.run_steps(dag):
            context[f"{result['skill'].replace('-', '_')}_out"] = result["output"]
        return context

    def run_steps(self, dag: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute the DAG and return per-step results. Never raises: step
        errors are captured per-step (same contract as run()).

        Each result: {"skill", "args", "status": "success"|"error", "output",
        "error"?}. The reality loop feeds these into verify_step() to decide
        whether the DAG actually accomplished the task (exit 0 != task done)."""
        context: Dict[str, Any] = {}
        results = []
        for step in dag:
            skill = step.get("skill")
            args = step.get("args", {})
            result = {"skill": skill, "args": args}
            try:
                if self.gate and not self.gate.allow(skill, args):
                    output = {"error": "blocked by approval gate", "skill": skill}
                    result["status"] = "success"
                else:
                    output = self._run_skill(skill, args, context)
                    result["status"] = "success"
                result["output"] = output
                context_key = f"{skill.replace('-', '_')}_out"
                context[context_key] = output
                if self.audit:
                    self.audit.log(skill, "success", args, output)
            except Exception as e:
                output = {"error": str(e)}
                result["status"] = "error"
                result["output"] = output
                result["error"] = str(e)
                context_key = f"{skill.replace('-', '_')}_out"
                context[context_key] = output
                if self.audit:
                    self.audit.log(skill, "error", args, str(e))
            results.append(result)
        if self._meta is not None:
            try:
                self._meta.record(getattr(self, "_last_task", ""), dag, {"status": "ok", "result": context})
            except Exception:
                pass
        return results

    def verify_step(self, step: Dict[str, Any], result: Dict[str, Any]) -> tuple:
        """Evaluate a step's verify criteria against its run result. Returns
        (ok: bool, reason: str).

        - No criteria: ok iff the step did not error (status error OR an
          output dict carrying an "error" key).
        - {"exit_zero": true}: ok iff output dict returncode == 0 (shell/python).
        - {"contains": "text"}: ok iff str(output) contains the text.

        This is the "exit 0 != task done" check: a step can complete without
        error yet fail verification, which is what triggers replanning.
        Note the intentional split with run_steps(): a gate-blocked step is
        recorded as status "success" (run() back-compat) but its output
        carries an "error" key, so verify_step flags it as a failure —
        correct for the loop, harmless for run()."""
        criteria = step.get("verify")
        if not criteria:
            if result["status"] == "error":
                return False, result.get("error") or "step errored"
            out = result["output"]
            if isinstance(out, dict) and out.get("error"):
                return False, str(out["error"])
            return True, ""
        if criteria.get("exit_zero") is True:
            out = result["output"]
            rc = out.get("returncode") if isinstance(out, dict) else None
            return rc == 0, f"returncode={rc}"
        if "contains" in criteria:
            needle = str(criteria["contains"])
            return needle in str(result["output"]), f"contains {needle!r}: {str(result['output'])[:60]!r}"
        return True, ""

    def _run_skill(self, skill: str, args: Dict, context: Dict) -> Any:
        name = skill.replace("-", "_")

        if name == "echo":
            return args.get("message", "")

        if name == "shell":
            return self._run_shell(args)

        if name == "python":
            return self._run_python(args)

        if name == "read_file":
            return self._run_read_file(args)

        if name == "write_file":
            return self._run_write_file(args)

        if name == "search_files":
            return self._run_search_files(args)

        if name == "web_search":
            return self._run_web_search(args)

        if name == "web_extract":
            return self._run_web_extract(args)

        if name == "agent_reach_doctor":
            return self._run_agent_reach_doctor(args)

        if name == "agent_reach_configure":
            return self._run_agent_reach_configure(args)

        if name == "agent_reach_scrape":
            return self._run_agent_reach_scrape(args)

        if name == "obsidian_search":
            return self._run_obsidian_search(args)

        if name == "obsidian_read":
            return self._run_obsidian_read(args)

        if name == "obsidian_write":
            return self._run_obsidian_write(args)

        if name == "n8n_list_workflows":
            return self._run_n8n_list_workflows(args)

        if name == "route":
            return self._run_domain_route(args)

        if name == "n8n_trigger_workflow":
            return self._run_n8n_trigger_workflow(args)

        raise ValueError(f"Unknown skill: {skill}")

    # --- primitives ---

    def _run_shell(self, args: Dict) -> Dict:
        cmd = args.get("cmd", "")
        timeout = int(args.get("timeout", 120) or 120)
        if not cmd:
            return {"stdout": "", "stderr": "missing cmd", "returncode": 2}
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.workdir),
            )
            return {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}
        except subprocess.TimeoutExpired as e:
            return {"stdout": e.stdout or "", "stderr": e.stderr or "", "returncode": 124}

    def _run_python(self, args: Dict) -> Dict:
        code = args.get("code", "")
        timeout = int(args.get("timeout", 120) or 120)
        if not code:
            return {"stdout": "", "stderr": "missing code", "returncode": 2}
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(self.workdir),
        )
        return {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode}

    # --- wrappers ---

    def _run_read_file(self, args: Dict) -> Dict:
        path = args.get("path", "")
        offset = int(args.get("offset", 1) or 1)
        limit = int(args.get("limit", 2000) or 2000)
        path_obj = Path(path)
        if not path_obj.is_absolute():
            path_obj = self.workdir / path_obj
        try:
            lines = path_obj.read_text(errors="replace").splitlines()
        except Exception as e:
            return {"content": "", "total_lines": 0, "error": str(e)}
        start = max(0, offset - 1)
        end = start + limit
        selected = lines[start:end]
        return {"content": "\n".join(selected), "total_lines": len(lines)}

    def _run_write_file(self, args: Dict) -> Dict:
        path = args.get("path", "")
        content = args.get("content", "")
        path_obj = Path(path)
        if not path_obj.is_absolute():
            path_obj = self.workdir / path_obj
        try:
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            path_obj.write_text(content)
            return {"bytes_written": len(content.encode("utf-8")), "verified": True}
        except Exception as e:
            return {"bytes_written": 0, "verified": False, "error": str(e)}

    def _run_search_files(self, args: Dict) -> Dict:
        path = args.get("path", ".")
        pattern = args.get("pattern", "")
        target = args.get("target", "content")
        limit = int(args.get("limit", 50) or 50)
        if target == "files":
            matches = [str(p) for p in Path(path).rglob(pattern)][:limit]
        else:
            matches = []
            try:
                for p in Path(path).rglob("*"):
                    if len(matches) >= limit:
                        break
                    if not p.is_file():
                        continue
                    try:
                        text = p.read_text(errors="ignore")
                        if pattern in text:
                            matches.append(str(p))
                    except Exception:
                        continue
            except Exception as e:
                return {"matches": [], "error": str(e)}
        return {"matches": matches[:limit]}

    def _run_web_search(self, args: Dict) -> Dict:
        query = args.get("query", "")
        limit = int(args.get("limit", 5) or 5)
        return self._run_shell({"cmd": f'python3 -m skill_orchestration_os.runners.web_search "{query}" {limit}', "timeout": 60})

    def _run_web_extract(self, args: Dict) -> Dict:
        urls = args.get("urls", [])
        char_limit = int(args.get("char_limit", 15000) or 15000)
        return self._run_shell({"cmd": f'python3 -m skill_orchestration_os.runners.web_extract {json.dumps(urls)} {char_limit}', "timeout": 120})

    def _run_agent_reach_doctor(self, args: Dict) -> Dict:
        return self._run_shell({"cmd": "agent-reach doctor --json", "timeout": 120})

    def _run_agent_reach_configure(self, args: Dict) -> Dict:
        key = args.get("key", "")
        value = args.get("value", "")
        if not key or value is None or (isinstance(value, str) and not value):
            return {"success": False, "message": "missing key or value"}
        return self._run_shell({"cmd": f"agent-reach configure {key} {value}", "timeout": 120})

    def _run_agent_reach_scrape(self, args: Dict) -> Dict:
        platform = args.get("platform", "")
        query = args.get("query", "")
        limit = int(args.get("limit", 10) or 10)
        if not platform or not query:
            return {"results": [], "error": "missing platform/query"}
        return self._run_shell({"cmd": f"agent-reach scrape {platform} {query} --limit {limit} --json", "timeout": 180})

    def _run_obsidian_search(self, args: Dict) -> Dict:
        query = args.get("query", "")
        limit = int(args.get("limit", 10) or 10)
        return self._run_shell({"cmd": f'python3 -m skill_orchestration_os.runners.obsidian_search "{query}" {limit}', "timeout": 120})

    def _run_obsidian_read(self, args: Dict) -> Dict:
        return self._run_read_file({"path": args.get("path", ""), "offset": args.get("offset", 1), "limit": args.get("limit", 2000)})

    def _run_obsidian_write(self, args: Dict) -> Dict:
        return self._run_write_file({"path": args.get("path", ""), "content": args.get("content", "")})

    def _run_n8n_list_workflows(self, args: Dict) -> Dict:
        return self._run_shell({"cmd": "python3 -m skill_orchestration_os.runners.n8n_list_workflows", "timeout": 60})

    def _run_n8n_trigger_workflow(self, args: Dict) -> Dict:
        workflow_id = args.get("workflow_id", "")
        if not workflow_id:
            return {"execution_id": None, "status": "error", "error": "missing workflow_id"}
        return self._run_shell({"cmd": f"python3 -m skill_orchestration_os.runners.n8n_trigger_workflow {workflow_id}", "timeout": 120})

    # --- routing front-end (the domain-router, folded in) ---

    def _run_domain_route(self, args: Dict) -> Dict:
        """The `route` skill: classify a task to one skill and dispatch a
        Claude subagent from that skill's directory. Reuses the shared
        DeepSeek transport via runtime/domain_router.DomainRouter."""
        task = args.get("task", "")
        if not task:
            return {"error": "missing task", "skill_id": None}
        try:
            from runtime.domain_router import DomainRouter
            dry_run = bool(args.get("dry_run", False))
            record = DomainRouter().route(
                task, dry_run=dry_run, domain=args.get("domain") or None
            )
            return {"record": record}
        except (Exception, SystemExit) as e:
            # SystemExit (from validate_skill_id on an unknown id) must not
            # crash the whole DAG run — record it as a step error instead.
            return {"error": str(e), "skill_id": None}
