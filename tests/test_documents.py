import tempfile
import unittest
from pathlib import Path

from cfo.documents import classify, collect_files, read_file, redact, redact_text
from cfo.jurisdictions import load_pack
from cfo.statements import parse_amount, parse_statement, summarize

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample-household"


class AmountAndStatementTest(unittest.TestCase):
    def test_amount_formats(self):
        cases = {"1.234,56": 1234.56, "-12,30": -12.3, "1,234.56": 1234.56, "12.30": 12.3, "€ 7,5": 7.5,
                 "1.234.567": 1234567.0, "(45,00)": -45.0, "": None, "abc": None}
        for raw, expected in cases.items():
            self.assertEqual(parse_amount(raw), expected, raw)

    def test_portuguese_export_with_preamble_and_debit_credit(self):
        text = "Banco X\nConta;123\nData Mov.;Data Valor;Descrição;Débito;Crédito;Saldo\n01-08-2026;01-08-2026;LIDL;12,50;;100,00\n02-08-2026;02-08-2026;SALARIO;;1.000,00;1.100,00\n"
        self.assertEqual(parse_statement(text), [
            {"date": "2026-08-01", "description": "LIDL", "amount": -12.5},
            {"date": "2026-08-02", "description": "SALARIO", "amount": 1000.0},
        ])

    def test_single_amount_column_not_confused_with_value_date(self):
        text = "Date,Value date,Description,Amount\n2026-08-01,2026-08-02,Coffee,-3.20\n"
        self.assertEqual(parse_statement(text)[0]["amount"], -3.2)

    def test_not_a_statement(self):
        self.assertIsNone(parse_statement("name,age\nAna,31\n"))

    def test_summary_of_sample_statement(self):
        doc = read_file(SAMPLE / "extrato_conta_jul-set_2026.csv")
        self.assertEqual(doc.kind, "statement")
        summary = summarize(doc.content, load_pack("pt")["transaction_categories"])
        self.assertEqual(summary["monthly_average_income"], 1582.0)
        self.assertEqual(summary["uncategorised_share_pct"], 0.0)
        payees = [r["payee"] for r in summary["recurring_payments"]]
        self.assertIn("PRESTACAO CREDITO HABITACAO", payees)
        self.assertIn("NETFLIX.COM", payees)


class ClassificationTest(unittest.TestCase):
    def test_sample_documents_are_recognised(self):
        catalog = load_pack("pt")["documents"]
        expected = {
            "extrato_conta_jul-set_2026.csv": "bank_statements",
            "mapa_responsabilidades_credito.txt": "credit_map",
            "nota_liquidacao_irs_2025.txt": "irs_return",
            "recibo_vencimento_2026-09.txt": "payslips",
        }
        for name, doc_type in expected.items():
            self.assertEqual(classify(read_file(SAMPLE / name), catalog), doc_type, name)

    def test_folder_collection_skips_readme_hidden_and_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("x")
            (root / ".hidden").mkdir()
            (root / ".hidden" / "a.txt").write_text("x")
            (root / "photo.jpg").write_bytes(b"x")
            (root / "sub").mkdir()
            (root / "sub" / "doc.txt").write_text("x")
            files, notes = collect_files([root])
            self.assertEqual([f.name for f in files], ["doc.txt"])
            self.assertTrue(notes)

    def test_latin1_and_missing_pdf_support_never_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.txt"
            path.write_bytes("Descrição: renda".encode("latin-1"))
            self.assertIn("renda", read_file(path).content)
            pdf = Path(tmp) / "x.pdf"
            pdf.write_bytes(b"%PDF-1.4 not really")
            self.assertTrue(read_file(pdf).warnings)


class RedactionTest(unittest.TestCase):
    def test_identifiers_are_masked_and_amounts_kept(self):
        text, count = redact_text("NIF 123456789; NISS 12345678901; IBAN PT50 0000 0000 0000 0000 0000 0; "
                                  "card 4111 1111 1111 1111; ana@example.pt; +351 912 345 678; total 1.234,56; ref 123456780")
        for label in ("[TAX-ID]", "[SOCIAL-SECURITY-ID]", "[IBAN]", "[CARD]", "[EMAIL]", "[PHONE]"):
            self.assertIn(label, text)
        self.assertIn("1.234,56", text)
        self.assertIn("123456780", text)  # fails the NIF checksum: not an identifier
        self.assertEqual(count, 6)

    def test_nested_structures(self):
        value, count = redact({"rows": [{"nif": "123456789"}], "n": 5})
        self.assertEqual(value, {"rows": [{"nif": "[TAX-ID]"}], "n": 5})
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
