#!/usr/bin/env python3
"""
Personal CFO: guided intake.

    python start.py
    python start.py --model anthropic/claude-sonnet-5-5

1. Asks where you're tax resident, what you want to achieve, and a short profile.
2. Asks for the documents that matter for your goals: give one folder and it sorts them,
   then it asks only for what's missing, saying where to get each one and why.
   Every document is optional, and you can type key figures instead.
3. Shows how complete the picture is, proposes a team of specialists, and runs it.
4. Saves everything under workspaces/ (never committed) and writes report.md.
"""

import argparse
import importlib.util
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from cfo.documents import Document, classify, collect_files, read_file
from cfo.jurisdictions import available_packs, documents_for, has_tax_tables, load_pack, resolve_code
from cfo.planner import (
    Intake,
    LLMTeamPlanner,
    TeamPlan,
    build_config,
    build_payload,
    coverage,
    critique_choice,
    detect_domains,
    heuristic_team,
    relevant_documents,
    slugify,
    team_from_choice,
)
from cfo.team import ROLES, SPECIALIST_ORDER
from core.llm import create_llm_client
from core.runner import format_output, run_config

SKIP = {"", "skip", "no", "n", "none", "-", "nao", "não"}
DEFAULT_MODELS = (("ANTHROPIC_API_KEY", "anthropic/claude-sonnet-5-5"), ("OPENAI_API_KEY", "openai/gpt-4o"))
MOCK_MODEL: Dict[str, Any] = {"provider": "mock", "model": "mock"}
LEVEL_MARK = {"good": "✓", "partial": "~", "low": "✗"}


class Cancelled(Exception):
    """The user chose to stop."""


class Console:
    """All terminal I/O, so the flow can be scripted in tests."""

    def __init__(self, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print):
        self._input, self._output = input_fn, output_fn

    def say(self, text: str = "") -> None:
        self._output(text)

    def ask(self, prompt: str) -> str:
        try:
            return self._input(prompt).strip()
        except EOFError:
            return ""

    def choose(self, prompt: str, options: Dict[str, str]) -> Optional[str]:
        """Numbered choice; returns the option value, or None when skipped."""
        answer = self.ask(prompt)
        return options.get(answer.lower()) if answer.lower() not in SKIP else None

    def confirm(self, prompt: str, default: bool) -> bool:
        answer = self.ask(prompt).lower()
        return default if not answer else answer in {"y", "yes", "s", "sim"}


def detect_model(cli_model: Optional[str]) -> Tuple[Dict[str, Any], str]:
    """CLI flag, then API keys in the environment, then offline demo mode."""
    if cli_model:
        return {"provider": "litellm", "model": cli_model}, f"Model: {cli_model}"
    has_litellm = importlib.util.find_spec("litellm") is not None
    for env_var, model in DEFAULT_MODELS:
        if os.environ.get(env_var):
            if has_litellm:
                return {"provider": "litellm", "model": model}, f"Model: {model} (found {env_var})"
            return dict(MOCK_MODEL), f"Found {env_var} but LiteLLM isn't installed (pip install litellm): offline demo mode."
    return dict(MOCK_MODEL), ("No API key found: offline demo mode. Document reading, the plan and every calculator "
                              "work, but the agents' written answers are placeholders. For real advice set "
                              "ANTHROPIC_API_KEY or OPENAI_API_KEY and pip install litellm, or keep data on your "
                              "machine with a local model: python start.py --model ollama_chat/llama3.1")


# --------------------------------------------------------------------------- questions


def ask_country(console: Console) -> Tuple[str, Dict[str, Any]]:
    packs = available_packs()
    console.say(f"Countries with full tax tables: {', '.join(packs.values())}. Others work with indicative tax figures.")
    answer = console.ask("1. Where are you tax resident? [Portugal]: ") or "Portugal"
    code = resolve_code(answer)
    pack = load_pack(code)
    if not has_tax_tables(pack):
        console.say(f"  No tax tables for '{answer}' yet: budgeting, debt and investing work fully; "
                    "tax figures will be indicative and clearly labelled.")
    else:
        console.say(f"  Using {pack['country']} rules for {pack['tax_year']} (verified {pack['verified']}).")
    return code, pack


def ask_goals(console: Console) -> List[str]:
    console.say("\n2. What do you want to achieve? One goal per line, empty line when done.")
    console.say("   e.g. 'pay off my debts faster', 'pay less IRS', 'start investing', 'can I afford a house?',")
    console.say("        or simply 'a complete financial plan'.")
    goals: List[str] = []
    empty_answers = 0
    while True:
        line = console.ask("> ")
        if line:
            goals.append(line)
            continue
        if goals:
            return goals
        empty_answers += 1
        if empty_answers >= 3:
            raise Cancelled("no goal")
        console.say("  At least one goal is needed. Describe it in your own words.")


def ask_profile(console: Console, pack: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    console.say("\nA few quick facts (press Enter to skip any):")
    profile: Dict[str, Any] = {}
    age = console.ask("  Your age: ")
    if age.isdigit():
        profile["age"] = int(age)
    household = console.choose("  Household: [1] single  [2] married / civil union  [3] single parent: ",
                               {"1": "single", "2": "couple", "3": "single_parent"})
    if household:
        profile["household"] = household
    deps = console.ask("  Dependants' ages, comma-separated (Enter if none): ")
    ages = [int(a) for a in deps.replace(" ", "").split(",") if a.isdigit()]
    if ages:
        profile["dependants_ages"] = ages
    work = console.choose("  Work: [1] employee  [2] self-employed  [3] both  [4] retired  [5] other: ",
                          {"1": "employee", "2": "self_employed", "3": "employee_and_self_employed", "4": "retired", "5": "other"})
    if work:
        profile["work"] = work
    income_tax = pack.get("income_tax", {})
    young = income_tax.get("young_people_regime")
    regimes = income_tax.get("special_regimes", {})
    if young or regimes:
        options = {}
        labels = []
        if young and profile.get("age", 0) <= young.get("max_age", 35):
            options["1"] = "young"
            labels.append(f"[1] {young['name']}")
        for index, (key, regime) in enumerate(regimes.items(), start=2):
            options[str(index)] = key
            labels.append(f"[{index}] {regime['name'].split(' (')[0]}")
        if labels:
            status = console.choose(f"  Special tax status: {'  '.join(labels)}: ", options)
            if status == "young":
                year = console.ask(f"    Which year of {young['name']} are you in (1-{young['years']})? ")
                profile["young_regime_year"] = int(year) if year.isdigit() else "unknown"
            elif status:
                profile["special_regime"] = status
    risk = console.choose("  Comfort with investment risk: [1] low  [2] medium  [3] high: ",
                          {"1": "low", "2": "medium", "3": "high"})
    if risk:
        profile["risk_tolerance"] = risk
    language = console.ask("  Report language [English]: ") or "English"
    if language.lower() in {"pt", "pt-pt", "portugues", "português", "portuguese"}:
        language = "Portuguese (Portugal)"
    return profile, language


def _load(paths_text: str, console: Console, catalog: List[Dict[str, Any]], forced_type: Optional[str] = None) -> Optional[List[Document]]:
    """Paths typed by the user -> documents. None if the text isn't a list of existing paths."""
    parts = [p.strip().strip("'\"") for p in paths_text.split(",") if p.strip()]
    paths = [Path(p).expanduser() for p in parts]
    if not paths or not all(p.exists() for p in paths):
        return None
    files, notes = collect_files(paths)
    for note in notes:
        console.say(f"  {note}")
    documents = []
    for file in files:
        doc = read_file(file)
        for warning in doc.warnings:
            console.say(f"  {file.name}: {warning}")
        if doc.content in ("", None, []):
            continue
        doc.doc_type = classify(doc, catalog) if not forced_type or doc.kind == "statement" else forced_type
        if forced_type and doc.doc_type != forced_type and doc.kind != "statement":
            doc.doc_type = forced_type
        documents.append(doc)
    return documents


def _looks_like_path(text: str) -> bool:
    return "/" in text or "\\" in text or Path(text.split(",")[0].strip()).suffix != ""


def ask_documents(console: Console, intake: Intake, catalog: List[Dict[str, Any]]) -> None:
    names = {d["id"]: d["name"] for d in catalog}
    console.say("\n3. Your documents. They stay on this computer; identifiers (tax numbers, IBANs, cards) are masked")
    console.say("   before anything is sent to the model.")
    console.say("   Fastest: give me ONE folder with all your financial documents (PDF, CSV, Excel, text), or several")
    console.say("   paths separated by commas. Or press Enter to go through the list one by one.")
    while True:
        answer = console.ask("> ")
        if answer.lower() in SKIP:
            break
        docs = _load(answer, console, catalog)
        if docs is None:
            console.say("  I couldn't find that path. Try again, or press Enter to go one by one.")
            continue
        intake.documents.extend(docs)
        break
    if intake.documents:
        console.say(f"\n  Read {len(intake.documents)} document(s):")
        by_type: Dict[str, List[str]] = {}
        for doc in intake.documents:
            by_type.setdefault(names.get(doc.doc_type or "", "Other / unrecognised"), []).append(doc.name)
        for type_name, files in by_type.items():
            console.say(f"   • {type_name}: {', '.join(files)}")

    domains = detect_domains(intake.goals_text)
    groups = relevant_documents(catalog, domains)
    missing = [d for d in groups["essential"] if d["id"] not in intake.provided_types()]
    if missing:
        console.say(f"\nFor your goals these matter most. For each: a path, OR type the key figures, OR press Enter to skip.")
    for entry in missing:
        local = f" ({entry['local_name']})" if entry.get("local_name") else ""
        console.say(f"\n• {entry['name']}{local}")
        console.say(f"  Where: {entry['where']}")
        console.say(f"  Why:   {entry['why']}")
        while True:
            answer = console.ask(f"  > path, or {entry['facts_question']} ")
            if answer.lower() in SKIP:
                break
            docs = _load(answer, console, catalog, forced_type=entry["id"]) if _looks_like_path(answer) else None
            if docs:
                intake.documents.extend(docs)
                console.say(f"  Added {len(docs)} file(s).")
                break
            if _looks_like_path(answer) and docs is None:
                console.say("  I couldn't find that file. Try again, type the figures instead, or press Enter to skip.")
                continue
            intake.facts[entry["id"]] = answer
            break

    optional = [d for d in groups["helpful"] if d["id"] not in intake.provided_types()]
    if optional:
        console.say("\nOptional, for a more complete picture:")
        for entry in optional:
            console.say(f"   - {entry['name']}: {entry['where']}")
        while True:
            answer = console.ask("  Add any? Path(s), or Enter to continue: ")
            if answer.lower() in SKIP:
                break
            docs = _load(answer, console, catalog)
            if docs is None:
                console.say("  I couldn't find that path.")
                continue
            intake.documents.extend(docs)
            console.say(f"  Added {len(docs)} file(s): {', '.join(names.get(d.doc_type or '', 'other') for d in docs)}.")


def show_coverage(console: Console, intake: Intake, catalog: List[Dict[str, Any]]) -> None:
    cov = coverage(intake, catalog, detect_domains(intake.goals_text))
    console.say("\nHow complete is the picture for your goals:")
    for domain, info in cov.items():
        detail = f"missing: {', '.join(info['missing'])}" if info["missing"] else "all key items provided"
        console.say(f"  {LEVEL_MARK[info['level']]} {domain:<10} {detail}")
    if any(info["missing"] for info in cov.values()):
        console.say("  That's fine: where data is missing the team states its assumptions, and the report lists what")
        console.say("  would sharpen the plan.")


def describe_team(console: Console, team: TeamPlan, title: str = "Proposed team") -> None:
    console.say(f"\n{title}: {len(team.roles)} agent{'s' if team.multi_agent else ''}. {team.reason}")
    for index, role in enumerate(team.roles, 1):
        console.say(f"  {index}. {ROLES[role].title:<22} {ROLES[role].summary}")


def choose_team(console: Console, intake: Intake, recommended: TeamPlan) -> TeamPlan:
    while True:
        choice = console.ask("\nStart?  [Enter] yes   [1] CFO only   [e] pick specialists   [q] quit: ").lower()
        if choice in {"", "y", "yes", "s", "sim"}:
            return recommended
        if choice in {"q", "quit"}:
            raise Cancelled("quit at team review")
        if choice == "1":
            chosen = TeamPlan(["cfo"], "Your choice: the CFO alone.", recommended.domains, source="user")
            break
        if choice == "e":
            console.say("  Specialists:")
            for index, role in enumerate(SPECIALIST_ORDER, 1):
                console.say(f"   {index}. {ROLES[role].title}: {ROLES[role].summary}")
            picked = console.ask("  Numbers, comma-separated (e.g. 1,2,8): ")
            numbers = [int(n) for n in picked.replace(" ", "").split(",") if n.isdigit()]
            if not numbers:
                console.say("  Nothing picked; keeping the proposed team.")
                return recommended
            chosen = team_from_choice(numbers, intake)
            break
        console.say("  Press Enter, or type 1, e or q.")
    advice = critique_choice(recommended, chosen, intake)
    if advice:
        console.say(f"\nSuggestion: {advice}")
        if console.confirm("Use the proposed team instead? [y/N]: ", default=False):
            return recommended
        console.say("Okay, going with your choice.")
    describe_team(console, chosen, title="Your team")
    return chosen


# --------------------------------------------------------------------------- save & run


class _BlockDumper(yaml.SafeDumper):
    """Readable multi-line prompts in config.yaml."""


_BlockDumper.add_representer(
    str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None))


def save_workspace(root: Path, intake: Intake, config: Dict[str, Any], payload: Dict[str, Any]) -> Path:
    base = f"{date.today().isoformat()}-{slugify(intake.goals[0])}"
    folder, n = root / base, 2
    while folder.exists():
        folder, n = root / f"{base}-{n}", n + 1
    folder.mkdir(parents=True)
    (folder / "config.yaml").write_text(yaml.dump(config, Dumper=_BlockDumper, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")
    (folder / "input.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return folder


def run_and_report(console: Console, folder: Path, config: Dict[str, Any], payload: Dict[str, Any]) -> int:
    console.say("")
    try:
        state = run_config(config, payload)
    except Exception as exc:
        console.say(f"\nThe run failed: {type(exc).__name__}: {exc}")
        console.say(f"Your setup is saved. Fix the problem, then run: python run.py {folder}")
        return 1
    (folder / "result.json").write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    report = format_output(state["artifacts"][config["agents"][-1]["name"]]["output"])
    (folder / "report.md").write_text(report + "\n", encoding="utf-8")
    console.say("\n" + "=" * 72 + "\nYOUR PLAN\n" + "=" * 72)
    console.say(report)
    console.say("=" * 72)
    console.say(f"Saved: {folder / 'report.md'} (and every specialist's work in result.json)")
    return 0


def guided_intake(console: Console, model_override: Optional[str], workspace_root: Path, redact_ids: bool = True) -> int:
    console.say("Personal CFO: guided intake")
    console.say("I'll ask what you want to achieve, a few facts, and for the documents that matter. Only your goals")
    console.say("are required; press Enter to skip anything else.\n")
    model, note = detect_model(model_override)
    console.say(note + "\n")

    code, pack = ask_country(console)
    catalog = documents_for(pack)
    goals = ask_goals(console)
    profile, language = ask_profile(console, pack)
    intake = Intake(country_code=code, goals=goals, profile=profile, language=language)
    ask_documents(console, intake, catalog)
    show_coverage(console, intake, catalog)

    planner = heuristic_team if model["provider"] == "mock" else None
    if planner is None:
        try:
            team = LLMTeamPlanner(create_llm_client(model)).plan(intake)
        except ImportError as exc:
            console.say(f"{exc}. Using the built-in planner.")
            team = heuristic_team(intake)
    else:
        team = planner(intake)
    describe_team(console, team)
    team = choose_team(console, intake, team)

    config = build_config(team, intake, pack, model)
    payload = build_payload(intake, pack, catalog, redact_ids=redact_ids)
    folder = save_workspace(workspace_root, intake, config, payload)
    console.say(f"\nSaved to {folder}/. It contains your financial data: it is git-ignored; delete it when done.")
    if payload.get("privacy"):
        console.say(payload["privacy"] + " Names and addresses are not masked.")
    console.say(f"Re-run any time: python run.py {folder}" + (" --model <model-id>" if model["provider"] == "mock" else ""))
    if not console.confirm("\nRun it now? [Y/n]: ", default=True):
        console.say("Okay. Nothing was run.")
        return 0
    return run_and_report(console, folder, config, payload)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Guided intake for your personal CFO team.")
    parser.add_argument("--model", help="LiteLLM model id (e.g. anthropic/claude-sonnet-5-5, ollama_chat/llama3.1)")
    parser.add_argument("--workspaces", type=Path, default=Path("workspaces"), help="Where to save engagements")
    parser.add_argument("--no-redact", action="store_true", help="Don't mask tax numbers, IBANs, cards, emails, phones")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed logs")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)-7s %(name)s: %(message)s")
    for noisy in ("httpx", "LiteLLM", "litellm", "pypdf"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        return guided_intake(Console(), args.model, args.workspaces, redact_ids=not args.no_redact)
    except (Cancelled, KeyboardInterrupt):
        print("\nCancelled. Nothing was run.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
