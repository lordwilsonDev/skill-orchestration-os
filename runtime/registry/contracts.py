from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class SkillContract:
    name: str
    inputs: List[str]
    outputs: List[str]
    side_effects: List[str]
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)
    version: str = "0.1.0"
    timeout_s: int = 120
    description: str = ""

class SkillRegistry:
    def __init__(self):
        self._contracts: Dict[str, SkillContract] = {}

    def register(self, contract: SkillContract):
        self._contracts[contract.name] = contract

    def get(self, name: str) -> SkillContract:
        return self._contracts[name]

    def all(self) -> Dict[str, SkillContract]:
        return dict(self._contracts)
