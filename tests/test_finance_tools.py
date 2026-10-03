import unittest

import plugins.finance_tools as f


class FinanceToolsTest(unittest.TestCase):
    def test_loan_payment(self):
        self.assertEqual(f.loan_payment.func(100000, 3.0, 30)["monthly_payment"], 421.6)
        self.assertEqual(f.loan_payment.func(1200, 0, 1)["monthly_payment"], 100.0)

    def test_early_repayment(self):
        reduce_term = f.early_repayment_analysis.func(10000, 9.0, 250, 2000, fee_pct=0.5)
        self.assertGreater(reduce_term["months_saved"], 0)
        self.assertEqual(reduce_term["fee"], 10.0)
        self.assertGreater(reduce_term["net_saving"], 0)
        reduce_payment = f.early_repayment_analysis.func(10000, 9.0, 250, 2000, mode="reduce_payment")
        self.assertLess(reduce_payment["with_extra"]["monthly_payment"], 250)
        self.assertIn("error", f.early_repayment_analysis.func(10000, 12.0, 50, 0))

    def test_avalanche_beats_snowball_on_interest(self):
        debts = [{"name": "card", "balance": 2000, "annual_rate_pct": 18, "min_payment": 50},
                 {"name": "car", "balance": 9000, "annual_rate_pct": 9, "min_payment": 200},
                 {"name": "small", "balance": 500, "annual_rate_pct": 5, "min_payment": 25}]
        avalanche = f.debt_payoff_plan.func(debts, 600, "avalanche")
        snowball = f.debt_payoff_plan.func(debts, 600, "snowball")
        self.assertLessEqual(avalanche["plan"]["total_interest"], snowball["plan"]["total_interest"])
        self.assertGreater(avalanche["interest_saved_vs_minimums"], 0)
        first_paid = min(avalanche["plan"]["debts"], key=lambda d: d["paid_off_month"])
        self.assertEqual(first_paid["name"], "card")

    def test_no_extra_debts_only_get_minimums(self):
        debts = [{"name": "subsidised", "balance": 1000, "annual_rate_pct": 1, "min_payment": 10, "no_extra": True},
                 {"name": "card", "balance": 1000, "annual_rate_pct": 18, "min_payment": 30}]
        plan = f.debt_payoff_plan.func(debts, 300)["plan"]["debts"]
        self.assertLess(next(d for d in plan if d["name"] == "card")["paid_off_month"], 12)

    def test_lump_sums_and_budget_warning(self):
        debts = [{"name": "car", "balance": 5000, "annual_rate_pct": 8, "min_payment": 150}]
        base = f.debt_payoff_plan.func(debts, 150)["plan"]["months"]
        lump = f.debt_payoff_plan.func(debts, 150, lump_sums=[{"month": 2, "amount": 2000}])["plan"]["months"]
        self.assertLess(lump, base)
        self.assertTrue(f.debt_payoff_plan.func(debts, 100)["warnings"])

    def test_savings_and_goals(self):
        flat = f.savings_projection.func(1000, 100, 0, 2, annual_inflation_pct=0)
        self.assertEqual(flat["final_value"], 3400.0)
        self.assertEqual(f.goal_planner.func(1200, years=1)["monthly_needed"], 100.0)
        self.assertEqual(f.goal_planner.func(1000, monthly_contribution=100)["months_needed"], 10)

    def test_emergency_fund(self):
        result = f.emergency_fund_target.func(1500, "self_employed", dependants=1, single_income_household=True, current_savings=2000)
        self.assertEqual(result["recommended_months"], 8)
        self.assertEqual(result["gap"], 10000.0)

    def test_sinking_fund_never_runs_short_after_top_up(self):
        bills = [{"name": "car insurance", "amount": 300, "month": 1}, {"name": "home insurance", "amount": 150, "month": 6}]
        result = f.sinking_fund_plan.func(bills, start_month=11)
        self.assertEqual(result["monthly_transfer"], 37.5)
        self.assertEqual(result["initial_top_up_needed"], 187.5)  # Nov+Dec+Jan transfers = 112.5 before the 300 bill

    def test_budget_and_net_worth(self):
        budget = f.budget_analysis.func(2000, {"housing": 700, "groceries": 300, "eating_out": 200}, debt_payments=400)
        self.assertEqual(budget["surplus"], 400.0)
        self.assertEqual(budget["debt_service_ratio_pct"], 20.0)
        worth = f.net_worth.func({"cash": 5000, "home": 200000}, {"mortgage": 150000}, ["cash"])
        self.assertEqual(worth["net_worth"], 55000.0)
        self.assertEqual(worth["liquid_assets"], 5000.0)

    def test_retirement_and_cash_flow(self):
        on_track = f.retirement_projection.func(30, 65, 100000, 1000, 1500, 800, 6, 2, 4)
        self.assertTrue(on_track["on_track"])
        flow = f.cash_flow_projection.func(100, 1000, 1100, 3, extra_income=[{"month": 3, "amount": 500, "label": "bonus"}])
        self.assertEqual(flow["months_below_zero"], [2])  # month 1 ends at exactly 0
        self.assertEqual(flow["end_balance"], 300.0)


if __name__ == "__main__":
    unittest.main()
