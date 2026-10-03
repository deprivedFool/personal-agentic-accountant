import contextlib
import io
import json
import unittest

import plugins.tax_tools as tax
from cfo.documents import Document
from cfo.jurisdictions import load_pack
from cfo.planner import (
    Intake, LLMTeamPlanner, TeamPlan, build_config, build_payload, coverage, critique_choice,
    detect_domains, heuristic_team, relevant_documents, team_from_choice,
)
from core.llm import LLMResponse
from core.runner import run_config, validate_config

PACK = load_pack("pt")
CATALOG = PACK["documents"]


def intake(*goals, documents=(), facts=None, **profile):
    return Intake("pt", list(goals), profile=profile, documents=list(documents), facts=facts or {})


def doc(doc_type, kind="text", content="x"):
    return Document(path=f"/tmp/{doc_type}.txt", kind=kind, content=content, doc_type=doc_type)


class DomainTest(unittest.TestCase):
    def test_english_and_portuguese_goals(self):
        self.assertEqual(detect_domains("Pay off my car loan faster"), ["debt"])
        self.assertEqual(detect_domains("Quero pagar menos IRS"), ["tax"])
        self.assertEqual(set(detect_domains("Começar a investir para a reforma")), {"wealth", "retirement"})
        self.assertEqual(detect_domains("Posso comprar casa?"), ["property"])

    def test_overview_or_unclear_goal_gets_the_full_picture(self):
        for goal in ("A complete financial plan", "Ajuda-me", "Where do I stand?"):
            self.assertTrue({"tax", "cashflow", "debt", "wealth", "protection"} <= set(detect_domains(goal)), goal)

    def test_relevant_documents(self):
        groups = relevant_documents(CATALOG, ["debt"])
        essential = {d["id"] for d in groups["essential"]}
        self.assertEqual(essential, {"credit_map", "loan_contracts", "credit_cards"})
        self.assertIn("bank_statements", {d["id"] for d in groups["helpful"]})

    def test_coverage_counts_typed_facts(self):
        cov = coverage(intake("pay less tax", documents=[doc("payslips")], facts={"irs_return": "refund 300"}), CATALOG, ["tax"])
        self.assertEqual(cov["tax"]["level"], "partial")
        self.assertEqual(cov["tax"]["missing"], ["e-fatura deductible expenses summary"])


class TeamTest(unittest.TestCase):
    def test_focused_question_without_documents_is_cfo_only(self):
        team = heuristic_team(intake("Should I pay off my car loan early?"))
        self.assertEqual(team.roles, ["cfo"])
        config = build_config(team, intake("Should I pay off my car loan early?"), PACK, {"provider": "mock"})
        self.assertIn("early_repayment_analysis", config["agents"][0]["tools"])

    def test_documents_bring_the_bookkeeper_and_cfo_last(self):
        team = heuristic_team(intake("pay off debts", "pay less IRS", documents=[doc("payslips")]))
        self.assertEqual(team.roles, ["bookkeeper", "tax_strategist", "debt_strategist", "cfo"])

    def test_overview_team(self):
        roles = heuristic_team(intake("complete financial plan")).roles
        self.assertEqual(roles[-1], "cfo")
        self.assertIn("protection_advisor", roles)

    def test_critique(self):
        i = intake("pay off debts and invest", documents=[doc("payslips"), doc("credit_map"), doc("irs_return")])
        recommended = heuristic_team(i)
        self.assertIn("CFO alone", critique_choice(recommended, TeamPlan(["cfo"], "", recommended.domains), i))
        no_debt = team_from_choice([1, 6, 8], i)  # bookkeeper, wealth, cfo
        self.assertIn("debt", critique_choice(recommended, no_debt, i))
        self.assertIsNone(critique_choice(recommended, recommended, i))

    def test_llm_team_planner_and_fallback(self):
        class Fake:
            def __init__(self, content):
                self.content = content
            def complete(self, messages, tools=None):
                return LLMResponse(content=self.content)
        i = intake("pay off debts")
        plan = LLMTeamPlanner(Fake(json.dumps({"roles": ["debt_strategist", "astrologer", "bookkeeper"], "reason": "r"}))).plan(i)
        self.assertEqual(plan.roles, ["bookkeeper", "debt_strategist", "cfo"])
        with self.assertLogs("cfo.planner", "WARNING"):
            self.assertEqual(LLMTeamPlanner(Fake("nonsense")).plan(i).source, "heuristic")


class ConfigAndPayloadTest(unittest.TestCase):
    def test_config_runs_and_configures_the_jurisdiction(self):
        tax.configure({"jurisdiction": "generic"})
        i = intake("pay less IRS", "invest", documents=[doc("payslips", content="NIF 123456789 salário 2.000,00")], language="Portuguese")
        i.language = "Portuguese (Portugal)"
        config = build_config(heuristic_team(i), i, PACK, {"provider": "mock"})
        validate_config(config)
        payload = build_payload(i, PACK, CATALOG)
        self.assertIn("[TAX-ID]", json.dumps(payload, ensure_ascii=False))
        self.assertIn("Portuguese (Portugal)", config["agents"][-1]["instructions"])
        with contextlib.redirect_stdout(io.StringIO()):
            state = run_config(config, payload)
        self.assertEqual(tax.active_pack()["code"], "pt")
        self.assertEqual(list(state["artifacts"])[-1], "cfo")

    def test_statement_documents_are_summarised(self):
        tx = [{"date": "2026-08-01", "description": "LIDL", "amount": -50.0},
              {"date": "2026-08-25", "description": "VENCIMENTO", "amount": 1500.0}]
        payload = build_payload(intake("budget", documents=[doc("bank_statements", "statement", tx)]), PACK, CATALOG)
        self.assertEqual(payload["all_statements_summary"]["spending_by_category_monthly"], {"groceries": 50.0})


if __name__ == "__main__":
    unittest.main()
