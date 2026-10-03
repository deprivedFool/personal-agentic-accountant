import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

import start

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample-household"


class ScriptedConsole(start.Console):
    def __init__(self, answers):
        self.answers, self.prompts, self.lines = list(answers), [], []
        super().__init__(input_fn=self._answer, output_fn=self.lines.append)

    def _answer(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    @property
    def transcript(self):
        return "\n".join(self.lines + self.prompts)


PROFILE_SKIPPED = ["", "", "", "", "", "", ""]  # age, household, dependants, work, special status, risk, language


class IntakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def run_intake(self, answers):
        console = ScriptedConsole(answers)
        with contextlib.redirect_stdout(io.StringIO()):
            code = start.guided_intake(console, None, self.tmp / "ws")
        return code, console

    def saved(self):
        (folder,) = (self.tmp / "ws").iterdir()
        return folder, yaml.safe_load((folder / "config.yaml").read_text()), json.loads((folder / "input.json").read_text())

    def test_sample_folder_end_to_end(self):
        code, console = self.run_intake([
            "",                                                   # Portugal
            "Pay off my debts faster", "Pay less IRS and start investing", "",
            "31", "1", "", "1", "", "2", "",                      # profile
            str(SAMPLE),                                          # one folder with everything
            "", "", "", "about 3000 EUR in savings",              # e-fatura, loan contracts, cards: skip; savings: typed
            "",                                                   # optional documents
            "", "",                                               # accept team, run now
        ])
        self.assertEqual(code, 0)
        folder, config, payload = self.saved()
        self.assertEqual([a["name"] for a in config["agents"]],
                         ["bookkeeper", "tax_strategist", "debt_strategist", "wealth_advisor", "cfo"])
        self.assertEqual(len(payload["documents"]), 4)
        self.assertEqual(payload["profile"], {"age": 31, "household": "single", "work": "employee", "risk_tolerance": "medium"})
        self.assertEqual(payload["facts_given_by_user"], {"Savings and investment statements": "about 3000 EUR in savings"})
        self.assertEqual(payload["coverage"]["wealth"]["level"], "good")
        self.assertNotIn("123456789", json.dumps(payload))
        self.assertTrue((folder / "report.md").exists())
        self.assertIn("Credit responsibility map", "\n".join(console.lines))  # recognised from the folder
        self.assertNotIn("Mapa de Responsabilidades de Crédito)\n", console.transcript)  # so not asked again

    def test_documents_one_by_one_with_typed_figures_and_a_bad_path(self):
        code, console = self.run_intake([
            "", "Pay less IRS", "", *PROFILE_SKIPPED,
            "",                                              # no folder: go one by one
            "missing/recibo.pdf", "2100 gross, 14 payments",  # payslips: bad path, then typed figures
            str(SAMPLE / "nota_liquidacao_irs_2025.txt"),     # IRS return: a file
            "",                                               # e-fatura: skip
            "",                                               # optional
            "1", "", "n",                                     # CFO only, keep it (default), don't run
        ])
        self.assertEqual(code, 0)
        self.assertIn("couldn't find that file", console.transcript)
        _, config, payload = self.saved()
        self.assertEqual(payload["facts_given_by_user"], {"Payslips (last 3 months)": "2100 gross, 14 payments"})
        self.assertEqual(payload["documents"][0]["type"], "irs_return")
        self.assertEqual([a["name"] for a in config["agents"]], ["cfo"])

    def test_dropping_a_needed_specialist_gets_one_gentle_suggestion(self):
        code, console = self.run_intake([
            "", "Pay off my debts", "Start investing", "", *PROFILE_SKIPPED,
            "", "", "", "", "", "",                          # no documents at all
            "e", "6,8",                                      # wealth advisor + CFO only
            "y",                                             # accept the suggestion
            "n",
        ])
        self.assertIn("Suggestion: Your goals mention debt", console.transcript)
        self.assertIn("debt_strategist", [a["name"] for a in self.saved()[1]["agents"]])

    def test_country_without_pack(self):
        # No special tax regimes in the generic pack, so that profile question is not asked (6, not 7).
        console = ScriptedConsole(["Spain", "Plan my budget", "", *PROFILE_SKIPPED[:6], "", "", "", "", "q"])
        with self.assertRaises(start.Cancelled), contextlib.redirect_stdout(io.StringIO()):
            start.guided_intake(console, None, self.tmp / "ws")
        self.assertIn("No tax tables for 'Spain'", console.transcript)
        self.assertIn("Bank statements", console.transcript)  # generic checklist still asks for the essentials

    def test_goal_required_and_quit(self):
        with self.assertRaises(start.Cancelled):
            self.run_intake(["", "", "", ""])
        # folder, payslips, bank statements, optional documents: all skipped; then quit at the team review
        with self.assertRaises(start.Cancelled):
            self.run_intake(["", "Budget", "", *PROFILE_SKIPPED, "", "", "", "", "q"])
        self.assertFalse((self.tmp / "ws").exists())

if __name__ == "__main__":
    unittest.main()
