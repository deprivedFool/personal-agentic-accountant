"""
A concrete, configuration-driven agent.

``DynamicAgent`` carries no domain knowledge of its own: its behaviour comes
entirely from the system prompt, optional task instructions and tools it is
given. On each run it reads the shared canvas, calls its model (running any
requested tools), and writes its output back to the canvas.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from core.base_agent import BaseCanvasAgent
from core.llm import LLMClient, LLMResponse
from core.state import AgentCanvasState
from plugins.base_tool import CanvasTool

logger = logging.getLogger(__name__)


class DynamicAgent(BaseCanvasAgent):
    """LLM-backed agent whose role is defined entirely by configuration."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        llm: LLMClient,
        tools: Optional[List[CanvasTool]] = None,
        instructions: str = "",
        max_tool_rounds: int = 5,
    ):
        """
        Args:
            name: Unique agent name; also the key of its entry in ``artifacts``.
            system_prompt: The agent's role and rules for the current domain.
            llm: Client used for model calls (see ``core.llm``).
            tools: Tools the model may call while working.
            instructions: Optional task text added after the canvas context.
            max_tool_rounds: Upper bound on model->tool->model round trips.
        """
        super().__init__(name=name, system_prompt=system_prompt, tools=tools)
        self.llm = llm
        self.instructions = instructions
        self.max_tool_rounds = max_tool_rounds
        self._tools_by_name: Dict[str, CanvasTool] = {tool.name: tool for tool in self.tools}

    def execute(self, state: AgentCanvasState) -> AgentCanvasState:
        """Run the agent against the canvas and return the updated state."""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self._build_context(state)},
        ]
        tool_schemas = [tool.to_schema() for tool in self.tools] or None
        tool_log: List[Dict[str, Any]] = []

        response = self.llm.complete(messages, tools=tool_schemas)
        rounds = 0
        while response.tool_calls:
            if rounds >= self.max_tool_rounds:
                logger.warning("[%s] Stopping after %d tool rounds", self.name, self.max_tool_rounds)
                break
            rounds += 1
            messages.append(response.to_message())
            messages.extend(self._run_tools(response, tool_log))
            response = self.llm.complete(messages, tools=tool_schemas)

        output = _parse_output(response.content)
        logger.info("[%s] Finished (%d tool call(s))", self.name, len(tool_log))

        # Return a new state instead of mutating the caller's lists and dicts.
        return {
            **state,
            "messages": [
                *state["messages"],
                {"role": "assistant", "name": self.name, "content": response.content},
            ],
            "artifacts": {
                **state["artifacts"],
                self.name: {"output": output, "tool_calls": tool_log},
            },
        }

    def _build_context(self, state: AgentCanvasState) -> str:
        """Render the canvas into the user message the model sees."""
        sections = [
            f"## Domain context\n{state['domain_context']}",
            f"## Input data\n{json.dumps(state['raw_input_data'], indent=2, default=str)}",
        ]
        if state["artifacts"]:
            upstream = {name: artifact["output"] if isinstance(artifact, dict) and "output" in artifact else artifact
                        for name, artifact in state["artifacts"].items()}
            sections.append(
                f"## Outputs from previous agents\n{json.dumps(upstream, indent=2, default=str)}"
            )
        if self.instructions:
            sections.append(f"## Your task\n{self.instructions}")
        return "\n\n".join(sections)

    def _run_tools(self, response: LLMResponse, tool_log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute the tool calls in ``response`` and return the tool result messages."""
        results: List[Dict[str, Any]] = []
        for call in response.tool_calls:
            tool = self._tools_by_name.get(call.name)
            if tool is None:
                result = f"ERROR: unknown tool '{call.name}'"
            else:
                logger.info("[%s] Calling tool %s(%s)", self.name, call.name, call.arguments)
                try:
                    result = tool.invoke(call.arguments)
                except Exception as exc:  # report failures to the model so it can recover
                    result = f"ERROR: {type(exc).__name__}: {exc}"
            tool_log.append({"tool": call.name, "arguments": call.arguments, "result": result})
            results.append({"role": "tool", "tool_call_id": call.id, "content": result})
        return results


def _parse_output(content: str) -> Any:
    """Return JSON output as Python data when possible, otherwise the raw text."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return content
