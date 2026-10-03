"""
Model-driven intake: the model decides what to ask next, based on the goals and what it already has.

The interviewer sees the goals, the profile and facts gathered so far, the documents provided
(type, file name and a short redacted preview) and the conversation. Each turn it either asks the
single most useful next question, saying why, or decides it has enough and summarises.

The jurisdiction's document checklist is passed as a reference it may use or ignore.
Any problem with the model's answer returns ``None`` so the caller can fall back to the checklist.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cfo.documents import redact
from cfo.planner import Intake
from cfo.statements import summarize
from core.llm import LLMClient

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 12
PREVIEW_CHARS = 600

_PROMPT = """You are the intake interviewer for a personal CFO team (tax, cash flow, debt, property, investing,
protection) working for a client resident in {country}.

Your job: gather what the specialists need to answer the client's goals well, and no more.
- Ask ONE question per turn: the one whose answer would change the advice the most.
- When a document is the most reliable source, ask for it by name and say exactly where to get it in
  {country} (portal and menu, app, or who issues it), and accept typed key figures instead.
- Never ask for anything already covered by the documents or answers. Never re-ask a question the client
  skipped: they chose not to answer, so work around it.
- Never ask for passwords, access codes, PINs or full card numbers.
- Stop as soon as the remaining unknowns would not change the advice much. You have asked {asked} of at
  most {limit} questions.
- Use plain language. Write questions in {language}.

A reference checklist of documents that are often useful in {country} (use, adapt or ignore):
{checklist}

Respond with JSON only, one of:
{{"done": false, "question": "...", "why": "one short sentence", "label": "short name of what you're asking for",
  "accepts": "document" | "answer" | "either"}}
{{"done": true, "understanding": ["3-6 short bullets summarising the client's situation"],
  "gaps": ["important unknowns the specialists should state assumptions about"]}}"""


@dataclass
class Turn:
    label: str
    question: str
    answer: str  # what the user typed, "(skipped)", or "(provided: file names)"


@dataclass
class Step:
    done: bool
    question: str = ""
    why: str = ""
    label: str = ""
    accepts: str = "either"
    understanding: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)


class Interviewer:
    def __init__(self, llm: LLMClient, pack: Dict[str, Any], language: str = "English", limit: int = MAX_QUESTIONS):
        self.llm = llm
        self.pack = pack
        self.language = language
        self.limit = limit
        self.history: List[Turn] = []

    def _checklist(self) -> str:
        return "\n".join(f"- {d['name']} ({d.get('local_name') or '-'}): {d['where']}" for d in self.pack.get("documents", []))

    def _state(self, intake: Intake) -> str:
        documents = []
        transactions = []
        for doc in intake.documents:
            entry = {"file": doc.name, "recognised_as": doc.doc_type or "unrecognised"}
            if doc.kind == "statement":
                transactions.extend(doc.content)
            else:
                entry["preview"] = doc.preview_text(PREVIEW_CHARS)
            documents.append(entry)
        state: Dict[str, Any] = {
            "goals": intake.goals,
            "profile": intake.profile,
            "answers_so_far": intake.facts,
            "documents": documents,
            "conversation": [t.__dict__ for t in self.history],
        }
        if transactions:
            summary = summarize(transactions, self.pack.get("transaction_categories", {}))
            state["bank_statement_summary"] = {k: summary[k] for k in ("period", "monthly_average_income",
                                                                      "monthly_average_spending", "recurring_payments")}
        state, _ = redact(state)
        return json.dumps(state, ensure_ascii=False, default=str)

    def next_step(self, intake: Intake) -> Optional[Step]:
        """The next question, or a final summary; None if the model's answer can't be used."""
        if len(self.history) >= self.limit:
            return Step(done=True)
        prompt = _PROMPT.format(country=self.pack.get("country", "their country"), asked=len(self.history),
                                limit=self.limit, language=self.language, checklist=self._checklist())
        try:
            reply = self.llm.complete([{"role": "system", "content": prompt},
                                       {"role": "user", "content": self._state(intake)}]).content.strip()
            if reply.startswith("```"):
                reply = reply.split("\n", 1)[-1].rsplit("```", 1)[0]
            data = json.loads(reply)
            if data.get("done"):
                return Step(done=True, understanding=[str(x) for x in data.get("understanding") or []],
                            gaps=[str(x) for x in data.get("gaps") or []])
            question = str(data["question"]).strip()
            if not question:
                raise ValueError("empty question")
            if any(t.question == question for t in self.history):
                return Step(done=True)  # the model is repeating itself: stop rather than nag
            return Step(done=False, question=question, why=str(data.get("why", "")),
                        label=str(data.get("label") or question[:60]), accepts=str(data.get("accepts", "either")))
        except Exception as exc:
            logger.warning("Model interviewer failed (%s); using the standard checklist.", exc)
            return None

    def record(self, step: Step, answer: str) -> None:
        self.history.append(Turn(label=step.label, question=step.question, answer=answer))
