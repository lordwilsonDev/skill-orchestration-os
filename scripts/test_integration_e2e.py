from pathlib import Path
import sys, os
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
import importlib.util
spec = importlib.util.spec_from_file_location('bootstrap', str(root/'bootstrap.py'))
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
from runtime.audit import AuditLogger
from runtime.executor import Executor

registry = bootstrap.create_registry()
audit = AuditLogger()
executor = Executor(registry, audit)

# 1. Hermes wrapper: read_file then write_file via executor primitives
read_dag = [{"skill": "read-file", "args": {"path": str(root/"SKILL.md"), "limit": 5}}]
read_result = executor.run(read_dag)
print("read_file_ok:", bool(read_result["read_file_out"].get("content")))

# 2. Obsidian wrapper: read obsidian read path
obs_read_dag = [{"skill": "obsidian-read", "args": {"path": "/Users/lordwilson/Documents/Vault/99_Meta/index/Vault-Index.md", "limit": 20}}]
obs_read_result = executor.run(obs_read_dag)
print("obs_read_ok:", isinstance(obs_read_result.get("obsidian_read_out"), dict))

# 3. Agent-Reach doctor
ar_dag = [{"skill": "agent-reach-doctor", "args": {}}]
ar_result = executor.run(ar_dag)
print("agent_reach_doctor_ok:", isinstance(ar_result.get("agent_reach_doctor_out"), dict))

# 4. n8n list workflows runner
n8n_dag = [{"skill": "n8n-list-workflows", "args": {}}]
n8n_result = executor.run(n8n_dag)
print("n8n_list_workflows_ok:", isinstance(n8n_result.get("n8n_list_workflows_out"), dict))

# 5. chain: write a temp file then read it back
chain_dag = [
  {"skill": "write-file", "args": {"path": "/tmp/skill-os-e2e-test.txt", "content": "skill-os e2e"}},
  {"skill": "read-file", "args": {"path": "/tmp/skill-os-e2e-test.txt"}},
]
chain = executor.run(chain_dag)
print("chain_ok:", chain["read_file_out"].get("content") == "skill-os e2e")

print("INTEGRATION_E2E_OK")
