"""
Tool definitions and the plugin registry.

A tool is a plain Python function wrapped in ``CanvasTool``. The ``@canvas_tool``
decorator derives the JSON schema from the function's type hints and registers
it in the global ``registry``, from which agents pull tools by name.

A plugin is any importable module that defines tools. It may also expose
``configure(settings: dict) -> None``, which receives that plugin's
``settings`` block from the use-case YAML.
"""

import importlib
import inspect
import json
import logging
import typing
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_JSON_TYPES: Dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass
class CanvasTool:
    """A callable tool plus the metadata a model needs to use it."""

    name: str
    description: str
    parameters: Dict[str, Any]
    func: Callable[..., Any]

    def to_schema(self) -> Dict[str, Any]:
        """Return the OpenAI-style function schema (also accepted by LiteLLM)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def invoke(self, arguments: Dict[str, Any]) -> str:
        """Call the tool and return its result as a string for the model."""
        result = self.func(**arguments)
        return result if isinstance(result, str) else json.dumps(result, default=str)


class ToolRegistry:
    """Name -> ``CanvasTool`` lookup shared by every agent."""

    def __init__(self) -> None:
        self._tools: Dict[str, CanvasTool] = {}

    def register(self, tool: CanvasTool) -> CanvasTool:
        if tool.name in self._tools:
            logger.warning("Tool '%s' is being redefined", tool.name)
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> CanvasTool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"Unknown tool '{name}'. Registered tools: {self.names()}") from None

    def resolve(self, names: Iterable[str]) -> List[CanvasTool]:
        """Look up several tools at once, failing on the first unknown name."""
        return [self.get(name) for name in names]

    def names(self) -> List[str]:
        return sorted(self._tools)

    def load_plugin(self, module_path: str, settings: Optional[Dict[str, Any]] = None) -> None:
        """Import a plugin module (registering its tools) and pass it its settings."""
        module = importlib.import_module(module_path)
        configure = getattr(module, "configure", None)
        if callable(configure):
            configure(settings or {})
        elif settings:
            logger.warning("Plugin '%s' has no configure() but settings were given", module_path)
        logger.info("Loaded plugin %s", module_path)


registry = ToolRegistry()


def canvas_tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    param_descriptions: Optional[Dict[str, str]] = None,
    target: ToolRegistry = registry,
) -> Callable[[Callable[..., Any]], CanvasTool]:
    """
    Turn a typed function into a registered ``CanvasTool``.

    The description defaults to the function's docstring. Parameters without a
    default value are marked as required.

    Example::

        @canvas_tool(param_descriptions={"text": "Text to count words in"})
        def word_count(text: str) -> int:
            \"\"\"Count the words in a piece of text.\"\"\"
            return len(text.split())
    """
    param_descriptions = param_descriptions or {}

    def decorator(func: Callable[..., Any]) -> CanvasTool:
        hints = typing.get_type_hints(func)
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for param in inspect.signature(func).parameters.values():
            hint = hints.get(param.name, str)
            schema: Dict[str, Any] = {"type": _JSON_TYPES.get(typing.get_origin(hint) or hint, "string")}
            if param.name in param_descriptions:
                schema["description"] = param_descriptions[param.name]
            properties[param.name] = schema
            if param.default is inspect.Parameter.empty:
                required.append(param.name)

        tool = CanvasTool(
            name=name or func.__name__,
            description=description or inspect.getdoc(func) or "",
            parameters={"type": "object", "properties": properties, "required": required},
            func=func,
        )
        return target.register(tool)

    return decorator
