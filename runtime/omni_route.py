from typing import Callable, Dict, Any

class OmniRoute:
    def __init__(self):
        self._handlers: Dict[str, Callable] = {}

    def register(self, skill: str, handler: Callable):
        self._handlers[skill] = handler

    def send(self, skill: str, payload: Dict[str, Any]) -> Any:
        if skill not in self._handlers:
            raise ValueError(f"No handler for {skill}")
        return self._handlers[skill](payload)
