"""
From goals + profile + documents to a team plan, a coverage assessment and a runnable config.
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from cfo.documents import Document, redact
from cfo.statements import summarize
from cfo.team import ROLES, SPECIALIST_ORDER, build_agent
from core.llm import LLMClient

logger = logging.getLogger(__name__)

MAX_PAYLOAD_DOCUMENT_CHARS = 150_000
RECENT_TRANSACTIONS = 150


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _words(*alternatives: str) -> "re.Pattern[str]":
    return re.compile(r"\b(?:" + "|".join(alternatives) + r")", re.IGNORECASE)


# Goal wording -> domains (English and Portuguese, matched without accents).
DOMAIN_PATTERNS: Dict[str, "re.Pattern[str]"] = {
    "tax": _words(r"tax", r"irs\b", r"impost", r"deduc", r"dedu[cz]", r"refund", r"reembols", r"ppr\b", r"e-?fatura",
                  r"jovem", r"ifici", r"nhr\b", r"englobamento", r"fiscal"),
    "debt": _words(r"debt", r"divida", r"loan", r"emprestimo", r"credit", r"cartao", r"card", r"mortgage", r"amortiz",
                   r"prestac", r"payoff", r"pay off", r"pagar (?:o|a|os|as) (?:credito|divida|carro)", r"interest rate", r"juros"),
    "cashflow": _words(r"budget", r"orcament", r"spend", r"gast", r"cash ?flow", r"fluxo", r"poupar", r"save\b", r"saving",
                       r"poupanca", r"despesa", r"expens", r"subscri", r"emergenc", r"fundo de emergencia", r"month end", r"fim do mes"),
    "wealth": _words(r"invest", r"etf", r"stock", r"acoes", r"accoes", r"portfolio", r"carteira", r"wealth", r"patrimon",
                     r"rich", r"fire\b", r"financial independence", r"independencia financeira", r"crypto", r"cripto",
                     r"certificados", r"grow (?:my )?money"),
    "retirement": _words(r"retir", r"reforma", r"pension", r"pensao"),
    "property": _words(r"house", r"home", r"casa", r"imovel", r"apartment", r"apartamento", r"flat\b", r"rent\b", r"arrend",
                       r"renda", r"buy (?:a )?(?:house|home|flat)", r"comprar casa", r"imt\b", r"imi\b"),
    "protection": _words(r"insur", r"seguro", r"protect", r"protec", r"heranca", r"inherit", r"will\b", r"testament", r"life cover"),
}
OVERVIEW = _words(r"overview", r"full plan", r"complete", r"everything", r"whole picture", r"organi[sz]e my finances",
                  r"financial plan", r"plano financeiro", r"visao geral", r"plano completo", r"organizar as (?:minhas )?financas",
                  r"\bcfo\b", r"check ?up", r"diagnos", r"health check", r"where do i stand")
DOMAIN_ROLE = {"tax": "tax_strategist", "debt": "debt_strategist", "cashflow": "cash_flow_controller",
               "wealth": "wealth_advisor", "retirement": "wealth_advisor", "property": "property_advisor",
               "protection": "protection_advisor"}
OVERVIEW_DOMAINS = ("tax", "cashflow", "debt", "wealth", "protection")


@dataclass
class Intake:
    """Everything gathered from the user."""

    country_code: str
    goals: List[str]
    profile: Dict[str, Any] = field(default_factory=dict)
    documents: List[Document] = field(default_factory=list)
    facts: Dict[str, str] = field(default_factory=dict)  # checklist id -> what the user typed instead of a document
    language: str = "English"

    @property
    def goals_text(self) -> str:
        return " ".join(self.goals)

    def provided_types(self) -> set:
        return {d.doc_type for d in self.documents if d.doc_type} | set(self.facts)


@dataclass
class TeamPlan:
    roles: List[str]
    reason: str
    domains: List[str]
    source: str = "heuristic"

    @property
    def multi_agent(self) -> bool:
        return len(self.roles) > 1


def slugify(text: str, max_length: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _plain(text)).strip("-")[:max_length].strip("-")
    return slug or "plan"


def detect_domains(goals_text: str) -> List[str]:
    plain = _plain(goals_text)
    domains = [d for d, pattern in DOMAIN_PATTERNS.items() if pattern.search(plain)]
    if OVERVIEW.search(plain) or not domains:
        domains = list(dict.fromkeys(list(OVERVIEW_DOMAINS) + domains))
    return domains


def relevant_documents(catalog: List[Dict[str, Any]], domains: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Checklist entries split into essential (for these goals) and helpful (complete picture)."""
    essential = [d for d in catalog if set(d.get("essential_for", [])) & set(domains)]
    helpful = [d for d in catalog if d not in essential and set(d.get("domains", [])) & set(domains)]
    others = [d for d in catalog if d not in essential and d not in helpful]
    return {"essential": essential, "helpful": helpful + others}


def coverage(intake: Intake, catalog: List[Dict[str, Any]], domains: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Per domain: which essential items are covered by a document or by typed facts."""
    provided = intake.provided_types()
    result = {}
    for domain in domains:
        needed = [d for d in catalog if domain in d.get("essential_for", [])]
        have = [d["name"] for d in needed if d["id"] in provided]
        missing = [d["name"] for d in needed if d["id"] not in provided]
        result[domain] = {"have": have, "missing": missing,
                          "level": "good" if not missing else ("partial" if have else "low")}
    return result


def _order(roles: Sequence[str]) -> List[str]:
    return [r for r in SPECIALIST_ORDER if r in set(roles)]


def heuristic_team(intake: Intake) -> TeamPlan:
    domains = detect_domains(intake.goals_text)
    specialists = list(dict.fromkeys(DOMAIN_ROLE[d] for d in domains))
    has_docs = bool(intake.documents)
    if len(specialists) == 1 and not has_docs:
        return TeamPlan(["cfo"], "One focused question with no documents to process: the CFO answers it directly, "
                        f"with the {ROLES[specialists[0]].title.lower()}'s tools.", domains)
    roles = (["bookkeeper"] if has_docs else []) + specialists + ["cfo"]
    parts = []
    if has_docs:
        parts.append("a bookkeeper first organises your documents")
    parts.append(f"{len(specialists)} specialist{'s' if len(specialists) > 1 else ''} cover{'' if len(specialists) > 1 else 's'} "
                 f"{', '.join(dict.fromkeys(domains))}")
    parts.append("the CFO combines everything into one plan")
    return TeamPlan(_order(roles), "; ".join(parts) + ".", domains)


def team_from_choice(numbers: Sequence[int], intake: Intake) -> TeamPlan:
    """Build a plan from the user's own pick of specialists (1-based indexes into SPECIALIST_ORDER)."""
    chosen = [SPECIALIST_ORDER[n - 1] for n in numbers if 1 <= n <= len(SPECIALIST_ORDER)]
    if not chosen:
        chosen = ["cfo"]
    return TeamPlan(_order(chosen), "Your own choice of specialists.", detect_domains(intake.goals_text), source="user")


def critique_choice(recommended: TeamPlan, chosen: TeamPlan, intake: Intake) -> Optional[str]:
    """One gentle, specific note when the user's team looks worse than the recommendation."""
    dropped = [r for r in recommended.roles if r not in chosen.roles]
    uncovered = [d for d in recommended.domains if DOMAIN_ROLE.get(d) in dropped]
    if chosen.roles == ["cfo"] and len(recommended.roles) > 1:
        return (f"Your goals touch {', '.join(dict.fromkeys(recommended.domains))}. The CFO alone can answer, but without "
                "the specialists' simulations (tax estimate, payoff plan, projections) the numbers will be rougher.")
    if uncovered:
        names = ", ".join(ROLES[DOMAIN_ROLE[d]].title.lower() for d in dict.fromkeys(uncovered))
        return f"Your goals mention {', '.join(dict.fromkeys(uncovered))}, but this team has no {names}; that part would get less depth."
    if "bookkeeper" in dropped and len(intake.documents) >= 3:
        return f"You provided {len(intake.documents)} documents; without the bookkeeper each specialist re-reads them separately and inconsistencies may slip through."
    if "cfo" not in chosen.roles and len(chosen.roles) > 1:
        return "Without the CFO, you get separate specialist answers instead of one prioritised plan."
    extra = [r for r in chosen.roles if r not in recommended.roles]
    if len(extra) >= 2:
        return "The extra specialists don't match your goals; they add time and cost without changing the answer much."
    return None


class LLMTeamPlanner:
    """Lets a real model choose the team from the fixed catalogue; falls back to the heuristic."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def plan(self, intake: Intake) -> TeamPlan:
        fallback = heuristic_team(intake)
        catalogue = "\n".join(f"- {r}: {ROLES[r].summary}" for r in SPECIALIST_ORDER)
        prompt = (
            "You choose a team of financial specialists for a personal-finance request.\n"
            f"Available roles (use only these ids):\n{catalogue}\n"
            "Rules: include only roles the goals need; 'cfo' always last when there are 2+ roles; 'bookkeeper' "
            "first when documents need organising; a single focused question can be answered by 'cfo' alone.\n"
            'Respond with JSON only: {"roles": [...], "reason": "one sentence"}'
        )
        request = {"goals": intake.goals, "profile": intake.profile,
                   "documents": [d.doc_type or "other" for d in intake.documents], "facts_given": list(intake.facts)}
        try:
            reply = self.llm.complete([{"role": "system", "content": prompt},
                                       {"role": "user", "content": json.dumps(request, ensure_ascii=False)}]).content.strip()
            if reply.startswith("```"):
                reply = reply.split("\n", 1)[-1].rsplit("```", 1)[0]
            data = json.loads(reply)
            roles = _order([r for r in data["roles"] if r in ROLES])
            if not roles:
                raise ValueError("no known roles")
            if len(roles) > 1 and "cfo" not in roles:
                roles.append("cfo")
            return TeamPlan(roles, str(data.get("reason", "")), fallback.domains, source="model")
        except Exception as exc:
            logger.warning("Model team planner failed (%s); using the built-in planner.", exc)
            return fallback


# --------------------------------------------------------------------------- config & payload


def build_payload(intake: Intake, pack: Dict[str, Any], catalog: List[Dict[str, Any]], redact_ids: bool = True) -> Dict[str, Any]:
    """The input every agent sees. Statements are pre-summarised; identifiers are masked."""
    names = {d["id"]: d["name"] for d in catalog}
    rules = pack.get("transaction_categories", {})
    documents, all_transactions = [], []
    budget = MAX_PAYLOAD_DOCUMENT_CHARS
    for doc in intake.documents:
        entry: Dict[str, Any] = {"file": doc.name, "type": doc.doc_type or "other",
                                 "type_name": names.get(doc.doc_type or "", "Other document")}
        if doc.kind == "statement":
            all_transactions.extend(doc.content)
            entry["summary"] = summarize(doc.content, rules)
            entry["recent_transactions"] = sorted(doc.content, key=lambda t: t["date"])[-RECENT_TRANSACTIONS:]
        else:
            text = doc.content if isinstance(doc.content, str) else json.dumps(doc.content, ensure_ascii=False, default=str)
            share = max(2_000, budget // max(1, len(intake.documents)))
            entry["content"] = text[:share] if isinstance(doc.content, str) else doc.content
            if len(text) > share:
                entry["note"] = f"Truncated to {share:,} characters."
        documents.append(entry)

    domains = detect_domains(intake.goals_text)
    payload: Dict[str, Any] = {
        "today": date.today().isoformat(),
        "jurisdiction": {"country": pack.get("country"), "currency": pack.get("currency"),
                         "tax_year": pack.get("tax_year"), "rules_verified": pack.get("verified")},
        "goals": intake.goals,
        "profile": intake.profile,
        "report_language": intake.language,
        "facts_given_by_user": {names.get(k, k): v for k, v in intake.facts.items()},
        "documents": documents,
        "coverage": coverage(intake, catalog, domains),
    }
    if all_transactions:
        payload["all_statements_summary"] = summarize(all_transactions, rules)
    if redact_ids:
        payload, masked = redact(payload)
        payload["privacy"] = f"{masked} identifier(s) (tax numbers, IBANs, cards, emails, phones) were masked before analysis."
    return payload


def build_config(team: TeamPlan, intake: Intake, pack: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, Any]:
    """A config that core.runner.run_config can execute (and run.py can re-run)."""
    extra: tuple = ()
    if team.roles == ["cfo"]:
        # Answering alone: give the CFO the tools of the specialists its goals need.
        for domain in team.domains:
            extra += ROLES[DOMAIN_ROLE[domain]].tools
    agents = [build_agent(r, pack, intake.language, extra_tools=extra if r == "cfo" else (), final=(i == len(team.roles) - 1))
              for i, r in enumerate(team.roles)]
    return {
        "name": "; ".join(intake.goals)[:100],
        "domain_context": (f"Personal CFO engagement. Jurisdiction: {pack.get('country')}. "
                           f"Goals: {'; '.join(intake.goals)}"),
        "input": "input.json",
        "model": model,
        "plugins": [
            "plugins.general_tools",
            "plugins.finance_tools",
            {"module": "plugins.tax_tools", "settings": {"jurisdiction": pack.get("code", "generic")}},
            "plugins.statement_tools",
        ],
        "agents": agents,
    }
