#!/usr/bin/env python3
"""
Re-run a saved engagement (a folder created by start.py, or any folder with config.yaml + input.json).

    python run.py workspaces/2026-10-03-pay-off-my-debts
    python run.py workspaces/2026-10-03-pay-off-my-debts --model anthropic/claude-sonnet-5-5

Edit input.json (e.g. update a balance) or config.yaml (e.g. a prompt) and re-run.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

import yaml

from core.runner import format_output, run_config, validate_config


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Re-run a saved personal-CFO engagement.")
    parser.add_argument("folder", type=Path, help="Workspace folder with config.yaml and input.json")
    parser.add_argument("--model", help="LiteLLM model id for every agent (overrides the saved model)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")

    config = validate_config(yaml.safe_load((args.folder / "config.yaml").read_text(encoding="utf-8")), str(args.folder))
    payload = json.loads((args.folder / config.get("input", "input.json")).read_text(encoding="utf-8"))
    state = run_config(config, payload, args.model)
    report = format_output(state["artifacts"][config["agents"][-1]["name"]]["output"])
    (args.folder / "report.md").write_text(report + "\n", encoding="utf-8")
    (args.folder / "result.json").write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("\n" + report + f"\n\nSaved {args.folder / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
