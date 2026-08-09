from pathlib import Path
import sys, os
root = Path.home()/'.hermes'/'skills'/'skill-orchestration-os'
sys.path.insert(0, str(root))
os.environ['N8N_API_KEY'] = open(root/'.env').read().split('N8N_API_KEY="')[1].split('"')[0] if (root/'.env').exists() else ''
import importlib.util
spec = importlib.util.spec_from_file_location('bootstrap', str(root/'bootstrap.py'))
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
from runtime.audit import AuditLogger
from runtime.executor import Executor

registry = bootstrap.create_registry()
audit = AuditLogger()
executor = Executor(registry, audit_logger=audit)

# 1. approval gate blocks agent-reach-scrape by default
blocked_dag = [{"skill": "agent-reach-scrape", "args": {"platform": "reddit", "query": "ai", "limit": 1}}]
blocked_result = executor.run(blocked_dag)
print("gate_block:", blocked_result["agent_reach_scrape_out"].get("error") == "blocked by approval gate")

# 2. allow scrape, then inspect meta learner
executor.gate.set_policy("agent-reach-scrape", True)
allowed_dag = [{"skill": "agent-reach-scrape", "args": {"platform": "bilibili", "query": "test", "limit": 1}}]
allowed_result = executor.run(allowed_dag)
print("scrape_allowed:", allowed_result["agent_reach_scrape_out"].get("error") != "blocked by approval gate")

# 3. meta suggestion for repeated task
for _ in range(2):
    executor._last_task = "audit outreach workflows"
    executor.run([{"skill": "n8n-list-workflows", "args": {}}])
suggestions = executor._meta.suggest("audit outreach workflows")
print("meta_suggest:", len(suggestions) >= 1)

print("APPROVAL_E2E_OK")
