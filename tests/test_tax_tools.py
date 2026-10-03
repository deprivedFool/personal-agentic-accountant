import unittest

import plugins.tax_tools as tax


class PortugalTaxTest(unittest.TestCase):
    def setUp(self):
        tax.configure({"jurisdiction": "pt"})

    def irs(self, **kwargs):
        return tax.estimate_income_tax.func(**kwargs)

    def test_single_employee_30k_matches_hand_calculation(self):
        # 30,000 - specific deduction 4,587.09 = 25,412.91 taxable; progressive 2026 brackets.
        result = self.irs(employment_income=30000)
        self.assertEqual(result["taxable_income"], 25412.91)
        self.assertEqual(result["tax_before_deductions"], 4810.65)
        self.assertEqual(result["marginal_rate_pct"], 31.1)

    def test_social_security_replaces_specific_deduction_when_higher(self):
        result = self.irs(employment_income=60000)
        self.assertEqual(result["per_person"][0]["specific_deduction"], 6600.0)  # 11% of 60,000

    def test_minimum_income_rule(self):
        self.assertEqual(self.irs(employment_income=12000)["tax_due"], 0.0)

    def test_dependant_deductions(self):
        # First dependant aged 2 -> 726; second aged 5 (<= 6, from the second) -> 900.
        result = self.irs(employment_income=40000, dependants_ages=[5, 2])
        self.assertEqual(result["deductions"]["dependants"], 1626.0)

    def test_general_family_cap_per_taxpayer(self):
        single = self.irs(employment_income=30000, general_family_expenses=5000)
        joint = self.irs(employment_income=30000, filing="joint", spouse_employment_income=20000, general_family_expenses=5000)
        self.assertEqual(single["deductions"]["general_family"], 250.0)
        self.assertEqual(joint["deductions"]["general_family"], 500.0)

    def test_global_cap_limits_capped_deductions(self):
        result = self.irs(employment_income=110000, health_expenses=10000, education_expenses=5000, rent_paid=10000)
        self.assertEqual(result["deductions"]["global_cap"], 1000.0)
        self.assertTrue(any("Global deduction cap" in n for n in result["notes"]))

    def test_global_cap_slides_with_income(self):
        # 90,000 - 11% social security = 80,100 taxable: 1000 + 1500 * (86634 - 80100) / (86634 - 8342)
        result = self.irs(employment_income=90000, health_expenses=10000, education_expenses=5000, rent_paid=10000)
        self.assertEqual(result["deductions"]["global_cap"], 1125.19)

    def test_irs_jovem(self):
        self.assertEqual(self.irs(employment_income=25000, young_regime_year=1, taxpayer_age=25)["tax_due"], 0.0)
        year5 = self.irs(employment_income=30000, young_regime_year=5, taxpayer_age=30)
        self.assertEqual(year5["per_person"][0]["exempt"], 15000.0)  # 50%, under the 55 x IAS cap

    def test_refund_or_payment(self):
        result = self.irs(employment_income=30000, general_family_expenses=1000, tax_withheld=5000)
        self.assertEqual(result["refund_or_payment"], round(5000 - result["tax_due"], 2))

    def test_imt_matches_published_tables(self):
        cost = tax.property_purchase_costs.func
        self.assertEqual(cost(250000, "permanent_home")["transfer_tax"], 7042.04)   # 7% - 10,457.96
        self.assertEqual(cost(150000, "permanent_home")["transfer_tax"], 1008.98)   # 5% - 6,491.02
        self.assertEqual(cost(250000, "other_home")["transfer_tax"], 8105.5)        # 7% - 9,394.50
        self.assertEqual(cost(700000, "permanent_home")["transfer_tax"], 42000.0)   # flat 6%
        self.assertEqual(cost(250000, "permanent_home")["stamp_duty_purchase"], 2000.0)

    def test_young_buyer_relief(self):
        cost = tax.property_purchase_costs.func
        self.assertEqual(cost(300000, buyer_age=30, first_home=True)["total_taxes"], 0.0)
        self.assertEqual(cost(400000, buyer_age=30, first_home=True)["transfer_tax"], 5556.88)  # 8% above 330,539
        self.assertGreater(cost(300000, buyer_age=40, first_home=True)["transfer_tax"], 0)

    def test_capital_income(self):
        flat = tax.capital_income_tax.func(1000, "securities_gain", other_taxable_income=8000)
        self.assertEqual(flat["tax_at_flat_rate"], 280.0)
        self.assertEqual(flat["better_option"], "aggregate")  # 12.5% bracket beats 28%
        self.assertEqual(tax.capital_income_tax.func(5000, "crypto_gain", holding_days=400)["tax"], 0.0)

    def test_retirement_plan_worthless_without_tax(self):
        result = tax.retirement_savings_benefit.func(2000, 30, tax_before_deductions=0, other_deductions_used=0)
        self.assertEqual(result["deduction_actually_saved"], 0.0)
        self.assertIn("little or no", result["verdict"])

    def test_net_salary(self):
        result = tax.estimate_net_salary.func(1500)
        self.assertEqual(result["gross_yearly"], 21000.0)
        self.assertEqual(result["social_security_yearly"], 2310.0)
        self.assertEqual(result["income_tax_yearly"], 2270.31)  # 2,520.31 - 250 general family deduction

    def test_facts_lookup(self):
        self.assertIn("filing", tax.jurisdiction_facts.func("all_topics")["topics"])
        self.assertEqual(tax.jurisdiction_facts.func("banking")["facts"]["early_repayment"]["consumer_credit_notice_days"], 30)


class GenericJurisdictionTest(unittest.TestCase):
    def setUp(self):
        tax.configure({"jurisdiction": "generic"})

    def tearDown(self):
        tax.configure({"jurisdiction": "pt"})

    def test_no_invented_tax_figures(self):
        for call in (lambda: tax.estimate_income_tax.func(employment_income=1),
                     lambda: tax.property_purchase_costs.func(100000),
                     lambda: tax.capital_income_tax.func(100, "interest")):
            self.assertFalse(call()["available"])

    def test_investment_options_still_work(self):
        self.assertTrue(tax.investment_options.func()["options"])


if __name__ == "__main__":
    unittest.main()
