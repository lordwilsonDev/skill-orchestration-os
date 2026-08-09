import sys, os, json
from pathlib import Path
root = Path.home()/'.hermes'/'skills'/'skill-orchestration-os'
sys.path.insert(0, str(root))

# Load key from Hermes env without exposing it
env_path = Path.home()/'.hermes'/'.env'
deepseek_key = None
for line in env_path.read_text().splitlines():
    if line.startswith('DEEPSEEK_API_KEY='):
        deepseek_key = line.split('=', 1)[1].strip()
        break

from registry.contracts import SkillRegistry, SkillContract
from orchestrator import Orchestrator

registry = SkillRegistry()
registry.register(SkillContract(name='echo', inputs=['message'], outputs=['text'], side_effects=[]))
registry.register(SkillContract(name='vault_read', inputs=['path'], outputs=['content'], side_effects=['read']))
orch = Orchestrator(registry)
orch.deepseek_key = deepseek_key

print('deepseek_key_set:', bool(orch.deepseek_key))
dag = orch.plan('Read the Vault index and echo its title line')
print('dag:', dag)
