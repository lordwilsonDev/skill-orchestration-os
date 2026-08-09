---
name: skill-orchestration-os
description: Use when the user asks to chain, orchestrate, route, or build an OS from Hermes skills. Turns Hermes skills into a sovereign cognitive runtime with capability contracts, fault isolation, audit trails, and DeepSeek-based planning.
---

# Skill Orchestration OS

Build and operate a sovereign skill runtime: registry + planner + executor + audit + meta-learning. Use DeepSeek for planning, Omni Route for inter-skill messaging, and local policy for runtime control.

## When to Use

- User says "turn skills into an OS", "chain skills", "skill orchestration", "orchestrate these skills"
- User mentions Omni Route + DeepSeek together
- User wants to eliminate manual tool switching across multiple skills
- User asks for workflow automation across 3+ skills

## Core Architecture

```
Skill Orchestration OS
├── registry/          Capability declarations (pre/post/side-effects)
├── deepseek.py        SHARED DeepSeek transport (planner + router use one copy)
├── orchestrator.py    DeepSeek-based planner + local fallback policy
├── domain_router.py   Routing FRONT-END: classify a task to ONE skill, dispatch
│                      a Claude subagent from that skill's directory (the
│                      domain-router, folded in; reuses the shared transport)
├── executor.py        Isolated execution with rollback + circuit breakers
├── audit.py           Execution log + replay + provenance
├── omni_route.py      Inter-skill messaging bus
├── meta_learner.py    Routing policy improvement from outcomes
├── build_registry.py  Builds domains.json (362 skills / 46 containers)
└── cli.py             Entry point: `skill-os <task>` | `skill-os route <task>`
```

## Routing Front-End (`skill-os route`)

The domain-router is folded into this OS as its routing front-end. `route`
classifies a natural-language task to **exactly one** skill from the routing
table (`domains.json`, built from `~/.hermes/skills/**/SKILL.md`), then
dispatches a Claude subagent from that skill's directory:

```bash
python cli.py route "Draft a client proposal for a Fox Valley manufacturer"
#   -> classify (DeepSeek, same transport as the planner)
#   -> dispatch: cd <skill.dir> && claude -p "Load ./SKILL.md ..."
#   -> record:   logs/route_audit.jsonl

python cli.py route "<task>" --dry-run        # classify + print command, no spend
python cli.py route "<task>" --domain <id>    # skip classification (escape hatch)
```

The `route` skill is also registered in the executor, so a planned DAG can
include a routing step — and the planner's prompt now lists `route` with its
description plus the "maps to ONE domain skill" guidance, so DeepSeek
actually emits `{"skill": "route", "args": {"task": ...}}` steps for
single-domain tasks (drafting, research, n8n automation, etc.). The routing
front-end is deliberately thin: no meta-learning, no multi-skill fan-out
(that stays in the planner).

## Build Pattern

## Build Pattern

1. **Registry first**: every skill declares `{name, inputs, outputs, side_effects, preconditions, postconditions, version}`
2. **Planner second**: DeepSeek generates a DAG of skill invocations from a natural-language task
3. **Executor third**: run each node isolated; on failure, retry/substitute/escalate per policy
4. **Audit always**: log every execution with inputs, outputs, routing decisions, and outcomes
5. **Local fallback**: if DeepSeek is unavailable, use a deterministic rule-based planner

## Scaffold

Run: `python3 scripts/scaffold_skill_os.py`

This creates the runtime skeleton under `~/.hermes/skills/skill-orchestration-os/`.

## Verification

```bash
python3 scripts/smoke_test.py
# Expect: registry loads, planner generates a 2-step DAG, executor runs both steps, audit log written

# Routing front-end
python3 cli.py route --dry-run "classify this task"   # needs DEEPSEEK_API_KEY
python3 ../domain-router/test_router.py 2>/dev/null || \
  pytest ~/.hermes/domain-router/test_router.py -q     # 23 unit tests (shim-verified)
```

## Pitfalls

- **Skill contracts are mandatory**: a skill without a contract cannot be safely composed. Add contracts before chaining.
- **DeepSeek is the planner, not the runtime**: if the API is down, the local fallback policy must still execute safe workflows.
- **Side effects are permanent unless rolled back**: executor must checkpoint state before destructive skills.
- **Audit is not optional**: without replay, debugging multi-skill chains is impossible.
- **Omni Route is the bus, not the brain**: it transports messages between skills; it does not decide which skills to run.

## Integration Points

- **Hermes tools**: executor invokes Hermes tool calls via `execute_code` or subprocess
- **msb-v3**: status/metrics endpoints for runtime observability
- **n8n**: long-horizon workflows can be triggered as skills in the chain
- **Agent-Reach**: research/data collection skills plug into the registry
- **Obsidian vault**: read/write via Hermes file tools as skill side effects
