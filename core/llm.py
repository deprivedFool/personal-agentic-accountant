"""
Model-agnostic LLM client layer.

Agents never talk to a vendor SDK directly. They talk to an ``LLMClient``, which
takes OpenAI-style chat messages plus optional tool schemas and returns an
``LLMResponse``. Two providers ship out of the box:

* ``litellm`` - routes to OpenAI, Anthropic, Ollama, Azure, Bedrock, ... using
  LiteLLM model strings such as ``anthropic/claude-sonnet-5-5``,
  ``openai/gpt-4o`` or ``ollama/llama3``.
* ``mock``    - an offline, deterministic client so pipelines can be run and
  inspected without API keys or network access.

Additional providers (e.g. a native SDK) can be plugged in with
``register_provider``.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    """Provider-neutral model response."""

    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)

    def to_message(self) -> Dict[str, Any]:
        """Render this response as an OpenAI-style assistant message."""
        message: Dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in self.tool_calls
            ]
        return message


class LLMClient(Protocol):
    """Anything that can turn chat messages (+ tools) into an ``LLMResponse``."""

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse: ...


class LiteLLMClient:
    """Routes completions through LiteLLM, covering most hosted and local models."""

    def __init__(self, model: str, **params: Any):
        try:
            import litellm  # imported lazily so the mock provider works without it
        except ImportError as exc:
            raise ImportError(
                "The 'litellm' provider requires LiteLLM: pip install litellm"
            ) from exc
        self._litellm = litellm
        self.model = model
        self.params = params

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        kwargs: Dict[str, Any] = {"model": self.model, "messages": messages, **self.params}
        if tools:
            kwargs["tools"] = tools
        response = self._litellm.completion(**kwargs)
        message = response.choices[0].message

        tool_calls: List[ToolCall] = []
        for call in getattr(message, "tool_calls", None) or []:
            raw_args = call.function.arguments or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                logger.warning("Model sent non-JSON arguments for tool %s: %r", call.function.name, raw_args)
                arguments = {}
            tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))

        return LLMResponse(content=message.content or "", tool_calls=tool_calls)


class MockLLMClient:
    """
    Offline stand-in for a real model.

    It never calls tools; it returns a JSON summary of what the agent was given,
    which makes it easy to verify how state flows through a pipeline.
    """

    def __init__(self, model: str = "mock", **params: Any):
        self.model = model

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        role_line = next((line.strip() for line in system.splitlines() if line.strip()), "")
        summary = {
            "mock_response": True,
            "role": role_line,
            "context_characters": len(user),
            "tools_available": [t["function"]["name"] for t in tools or []],
            "note": "Run with --model <litellm-model-id> for real model output.",
        }
        return LLMResponse(content=json.dumps(summary))


ProviderFactory = Callable[..., LLMClient]

_PROVIDERS: Dict[str, ProviderFactory] = {
    "litellm": LiteLLMClient,
    "mock": MockLLMClient,
}


def register_provider(name: str, factory: ProviderFactory) -> None:
    """Make a custom provider available to ``create_llm_client`` and YAML configs."""
    _PROVIDERS[name] = factory


def create_llm_client(config: Dict[str, Any]) -> LLMClient:
    """
    Build a client from a config mapping such as::

        {"provider": "litellm", "model": "anthropic/claude-sonnet-5-5", "temperature": 0.2}

    Every key other than ``provider`` and ``model`` is passed to the provider as a
    completion parameter.
    """
    params = dict(config)
    provider = params.pop("provider", "litellm")
    model = params.pop("model", "mock")
    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown LLM provider '{provider}'. Available: {sorted(_PROVIDERS)}")
    return _PROVIDERS[provider](model=model, **params)
