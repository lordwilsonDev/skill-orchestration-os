import sys, os, json
from pathlib import Path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

# Load key from the environment, falling back to the Hermes .env (if
# present) without exposing it. Env-first so CI runs skip cleanly.
env_path = Path(os.environ.get('HERMES_ENV_FILE', str(Path.home()/'.hermes'/'.env')))
deepseek_key = os.environ.get('DEEPSEEK_API_KEY')
if not deepseek_key and env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.startswith('DEEPSEEK_API_KEY='):
            deepseek_key = line.split('=', 1)[1].strip()
            break

from runtime.registry.contracts import SkillRegistry, SkillContract
from runtime.orchestrator import Orchestrator

registry = SkillRegistry()
registry.register(SkillContract(name='echo', inputs=['message'], outputs=['text'], side_effects=[]))
registry.register(SkillContract(name='vault_read', inputs=['path'], outputs=['content'], side_effects=['read']))
orch = Orchestrator(registry)
orch.deepseek_key = deepseek_key

print('deepseek_key_set:', bool(orch.deepseek_key))
dag = orch.plan('Read the Vault index and echo its title line')
print('dag:', dag)
