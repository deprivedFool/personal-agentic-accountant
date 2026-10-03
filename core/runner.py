"""
Turn a use-case config (a dict, usually loaded from YAML) into a finished pipeline run.

Shared by ``demo.py`` (configs from files) and ``start.py`` (configs built by the
guided setup), so both behave identically.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from core.dynamic_agent import DynamicAgent
from core.llm import create_llm_client
from core.orchestrator import CanvasOrchestrator
from core.state import AgentCanvasState
from plugins.base_tool import registry

logger = logging.getLogger(__name__)


def validate_config(config: Dict[str, Any], source: str = "config") -> Dict[str, Any]:
    """Check the keys the runner relies on and return the config unchanged."""
    missing = [key for key in ("domain_context", "agents") if not config.get(key)]
    if missing:
        raise ValueError(f"{source}: missing required key(s): {', '.join(missing)}")
    for index, agent in enumerate(config["agents"]):
        for key in ("name", "system_prompt"):
            if not agent.get(key):
                raise ValueError(f"{source}: agents[{index}] is missing '{key}'")
    names = [agent["name"] for agent in config["agents"]]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"{source}: agent names must be unique, repeated: {', '.join(duplicates)}")
    return config


def load_plugins(config: Dict[str, Any]) -> None:
    """Import every configured plugin module so its tools are registered."""
    for entry in config.get("plugins", []):
        if isinstance(entry, str):
            registry.load_plugin(entry)
        else:
            registry.load_plugin(entry["module"], entry.get("settings"))
    logger.info("Registered tools: %s", registry.names() or "none")


def resolve_model_config(
    config: Dict[str, Any], agent_spec: Dict[str, Any], model_override: Optional[str] = None
) -> Dict[str, Any]:
    """Merge the default model, the agent's overrides and an optional CLI model id."""
    model_config = {**config.get("model", {"provider": "mock"}), **agent_spec.get("model", {})}
    if model_override:
        # A CLI model swaps in a real LiteLLM model but keeps tuning params like temperature.
        model_config.update(provider="litellm", model=model_override)
    return model_config


def build_agents(config: Dict[str, Any], model_override: Optional[str] = None) -> List[DynamicAgent]:
    """Create one ``DynamicAgent`` per entry under ``agents``."""
    agents: List[DynamicAgent] = []
    for spec in config["agents"]:
        model_config = resolve_model_config(config, spec, model_override)
        agents.append(
            DynamicAgent(
                name=spec["name"],
                system_prompt=spec["system_prompt"],
                llm=create_llm_client(model_config),
                tools=registry.resolve(spec.get("tools", [])),
                instructions=spec.get("instructions", ""),
                max_tool_rounds=spec.get("max_tool_rounds", 5),
            )
        )
        logger.info(
            "Agent %-22s model=%s:%s tools=%s",
            spec["name"],
            model_config.get("provider"),
            model_config.get("model"),
            spec.get("tools", []),
        )
    return agents


def run_config(
    config: Dict[str, Any], payload: Any, model_override: Optional[str] = None
) -> AgentCanvasState:
    """Load plugins, build the agents and run the pipeline on ``payload``."""
    validate_config(config)
    load_plugins(config)
    agents = build_agents(config, model_override)
    initial_state: AgentCanvasState = {
        "domain_context": config["domain_context"],
        "raw_input_data": payload,
        "messages": [],
        "artifacts": {},
        "current_step": "start",
    }
    return CanvasOrchestrator(agents).run_pipeline(initial_state)


def format_output(output: Any) -> str:
    """Render an agent output for the terminal."""
    return output if isinstance(output, str) else json.dumps(output, indent=2, default=str, ensure_ascii=False)


def print_artifacts(artifacts: Dict[str, Any]) -> None:
    """Pretty-print each agent's output and tool usage."""
    print("\n" + "=" * 72 + "\nARTIFACTS\n" + "=" * 72)
    for name, artifact in artifacts.items():
        print(f"\n[{name}]")
        output = artifact.get("output") if isinstance(artifact, dict) else artifact
        print(format_output(output))
        for call in artifact.get("tool_calls", []) if isinstance(artifact, dict) else []:
            print(f"  - tool {call['tool']}({json.dumps(call['arguments'])}) -> {call['result'][:200]}")
