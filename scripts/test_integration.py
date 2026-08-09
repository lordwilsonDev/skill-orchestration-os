from pathlib import Path
import sys, os
root = Path(__file__).resolve().parent.parent
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
executor = Executor(registry, audit)

shell_dag = [{"skill": "shell", "args": {"cmd": "echo hello-from-skill-os"}}]
print("shell:", executor.run(shell_dag)["shell_out"])

ar_dag = [{"skill": "agent-reach-doctor", "args": {}}]
print("agent-reach doctor:", executor.run(ar_dag)["agent_reach_doctor_out"])

chain_dag = [
  {"skill": "shell", "args": {"cmd": "echo 'Skill OS is operational' > /tmp/skill-os-test.txt"}},
  {"skill": "read-file", "args": {"path": "/tmp/skill-os-test.txt"}},
]
chain_result = executor.run(chain_dag)
print("chain:", chain_result["shell_out"], chain_result["read_file_out"])

print("INTEGRATION_TEST_OK")
