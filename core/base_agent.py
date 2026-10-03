import os
from abc import ABC, abstractmethod
from typing import Dict, Any, List

class BaseCanvasAgent(ABC):
    def __init__(self, name: str, system_prompt: str, tools: List[Any] = None):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools or []

    @abstractmethod
    def execute(self, state: Dict[Any, Any]) -> Dict[Any, Any]:
        """
        Executes the agent's core logic using the incoming state canvas.
        Must be overridden by specific implementations.
        """
        pass

    def update_system_prompt(self, new_prompt: str):
        """Allows users to completely wipe and re-contextualize the agent for a new domain."""
        self.system_prompt = new_prompt
