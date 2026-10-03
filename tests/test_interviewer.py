import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import start
from cfo.interviewer import Interviewer
from cfo.jurisdictions import load_pack
from cfo.planner import Intake
from core.llm import LLMResponse
from tests.test_start import ScriptedConsole

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample-household"


def ask(question, label, accepts="either"):
    return json.dumps({"done": False, "question": question, "why": f"Needed for {label}.", "label": label, "accepts": accepts})


DONE = json.dumps({"done": True, "understanding": ["Single employee with a car loan"], "gaps": ["Card interest rate"]})


class FakeModel:
    """Answers the interviewer from a script, the team planner with a fixed team, agents with text."""

    def __init__(self, interview_replies):
        self.interview_replies = list(interview_replies)
        self.interview_inputs = []

    def complete(self, messages, tools=None):
        system = messages[0]["content"]
        if system.startswith("You are the intake interviewer"):
            self.interview_inputs.append(json.loads(messages[1]["content"]))
            return LLMResponse(content=self.interview_replies.pop(0) if self.interview_replies else DONE)
        if system.startswith("You choose a team"):
            return LLMResponse(content='{"roles": ["debt_strategist", "cfo"], "reason": "Debt question."}')
        return LLMResponse(content="# Plan\n\nPay the card first.")


class ModelInterviewFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def run_flow(self, answers, model):
        console = ScriptedConsole(answers)
        with mock.patch("start.create_llm_client", return_value=model), contextlib.redirect_stdout(io.StringIO()):
            code = start.guided_intake(console, "anthropic/claude-sonnet-5-5", self.tmp / "ws")
        return code, console

    def payload(self):
        (folder,) = (self.tmp / "ws").iterdir()
        return json.loads((folder / "input.json").read_text())

    def test_model_asks_only_what_it_needs(self):
        model = FakeModel([
            ask("Could you share your Banco de Portugal credit map?", "credit map", "document"),
            ask("What is your monthly take-home pay?", "net monthly income", "answer"),
            ask("Do you have any savings?", "savings", "answer"),
            DONE,
        ])
        code, console = self.run_flow([
            "", "Pay off my debts faster", "",          # country, goals
            "",                                          # language
            "",                                          # no folder: answer questions instead
            str(SAMPLE / "mapa_responsabilidades_credito.txt"),
            "1582 net, 14 payments",
            "",                                          # skip the savings question
            "", "n",                                     # accept team, don't run
        ], model)
        self.assertEqual(code, 0)
        self.assertNotIn("Your age", console.transcript)          # no fixed profile questions in model mode
        self.assertNotIn("For your goals these matter most", console.transcript)  # no fixed checklist
        self.assertIn("Why: Needed for credit map.", console.transcript)
        self.assertIn("What I understand so far", console.transcript)
        payload = self.payload()
        self.assertEqual(payload["documents"][0]["type"], "credit_map")
        self.assertEqual(payload["facts_given_by_user"], {"net monthly income": "1582 net, 14 payments"})
        self.assertEqual(payload["intake_interview"]["gaps"], ["Card interest rate"])
        # The model was told what happened, including the skipped question, and saw the document preview redacted.
        last = model.interview_inputs[-1]
        self.assertEqual([t["answer"] for t in last["conversation"]],
                         ["(provided: mapa_responsabilidades_credito.txt)", "1582 net, 14 payments", "(skipped)"])
        self.assertNotIn("123456789", json.dumps(model.interview_inputs))

    def test_done_starts_immediately(self):
        model = FakeModel([ask("What is your monthly take-home pay?", "income")])
        code, console = self.run_flow(["", "Invest", "", "", "", "done", "", "n"], model)
        self.assertEqual(code, 0)
        self.assertIn("starting with what we have", console.transcript)
        self.assertEqual(len(model.interview_inputs), 1)

    def test_bad_path_then_figures(self):
        model = FakeModel([ask("Payslip?", "payslip", "document"), DONE])
        self.run_flow(["", "Pay less IRS", "", "", "", "nope/recibo.pdf", "2100 gross", "", "n"], model)
        self.assertEqual(self.payload()["facts_given_by_user"], {"payslip": "2100 gross"})

    def test_unusable_model_falls_back_to_checklist(self):
        model = FakeModel(["I'd like to know more about you!"])
        with self.assertLogs("cfo.interviewer", "WARNING"):
            code, console = self.run_flow(["", "Pay off my debts", "", "", "", "", "", "", "", "", "n"], model)
        self.assertEqual(code, 0)
        self.assertIn("standard checklist", console.transcript)
        self.assertIn("Credit responsibility map", console.transcript)


class InterviewerUnitTest(unittest.TestCase):
    def intake(self):
        return Intake("pt", ["Pay off my debts"])

    def test_repeated_question_stops_instead_of_nagging(self):
        q = ask("Monthly income?", "income")
        interviewer = Interviewer(FakeModel([q, q]), load_pack("pt"))
        first = interviewer.next_step(self.intake())
        interviewer.record(first, "(skipped)")
        self.assertTrue(interviewer.next_step(self.intake()).done)

    def test_question_limit(self):
        interviewer = Interviewer(FakeModel([ask(f"Q{i}?", f"q{i}") for i in range(5)]), load_pack("pt"), limit=2)
        for _ in range(2):
            interviewer.record(interviewer.next_step(self.intake()), "x")
        self.assertTrue(interviewer.next_step(self.intake()).done)

    def test_prompt_uses_country_language_and_checklist(self):
        model = FakeModel([DONE])

        class Spy(FakeModel):
            def complete(self, messages, tools=None):
                self.system = messages[0]["content"]
                return super().complete(messages, tools)

        spy = Spy([DONE])
        Interviewer(spy, load_pack("pt"), language="Portuguese (Portugal)").next_step(self.intake())
        self.assertIn("resident in Portugal", spy.system)
        self.assertIn("Write questions in Portuguese (Portugal)", spy.system)
        self.assertIn("Portal do Cliente Bancário", spy.system)
        self.assertIn("Never re-ask a question the client\n  skipped", spy.system)


if __name__ == "__main__":
    unittest.main()
