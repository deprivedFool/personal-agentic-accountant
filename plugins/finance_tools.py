"""
Country-independent personal-finance calculators.

Agents must use these for every figure instead of doing arithmetic themselves.
All rates are given in percent per year (e.g. 3.5 for 3.5%). Money is rounded to cents.
"""

from typing import Any, Dict, List, Optional

from plugins.base_tool import canvas_tool

MAX_MONTHS = 600  # 50-year simulation horizon


def _r(value: float) -> float:
    return round(value + 0.0, 2)


def _monthly_rate(annual_rate_pct: float) -> float:
    return annual_rate_pct / 100 / 12


def _payment(principal: float, monthly_rate: float, months: int) -> float:
    if months <= 0:
        return principal
    if monthly_rate == 0:
        return principal / months
    return principal * monthly_rate / (1 - (1 + monthly_rate) ** -months)


def _amortize(balance: float, monthly_rate: float, payment: float) -> Dict[str, Any]:
    """Pay a fixed amount every month until the balance is gone."""
    months, interest = 0, 0.0
    while balance > 0.005 and months < MAX_MONTHS:
        month_interest = balance * monthly_rate
        if payment <= month_interest:
            return {"error": "The payment doesn't cover the monthly interest; the balance never falls."}
        interest += month_interest
        balance = balance + month_interest - payment
        months += 1
    return {"months": months, "interest": interest}


@canvas_tool(param_descriptions={
    "principal": "Amount borrowed",
    "annual_rate_pct": "Nominal annual interest rate in % (e.g. 3.9)",
    "years": "Loan term in years",
})
def loan_payment(principal: float, annual_rate_pct: float, years: float) -> Dict[str, Any]:
    """Monthly payment, total paid and total interest for an amortising loan."""
    months = int(round(years * 12))
    payment = _payment(principal, _monthly_rate(annual_rate_pct), months)
    total = payment * months
    return {"monthly_payment": _r(payment), "months": months, "total_paid": _r(total), "total_interest": _r(total - principal)}


@canvas_tool(param_descriptions={
    "balance": "Current outstanding balance",
    "annual_rate_pct": "Current annual interest rate in %",
    "monthly_payment": "Current monthly payment (principal + interest)",
    "extra_payment": "One-off extra repayment now",
    "fee_pct": "Early-repayment fee in % of the extra amount (e.g. 0.5)",
    "mode": "'reduce_term' (keep the payment, finish sooner) or 'reduce_payment' (keep the end date, pay less)",
})
def early_repayment_analysis(
    balance: float,
    annual_rate_pct: float,
    monthly_payment: float,
    extra_payment: float,
    fee_pct: float = 0.0,
    mode: str = "reduce_term",
) -> Dict[str, Any]:
    """Compare a loan with and without a one-off extra repayment."""
    rate = _monthly_rate(annual_rate_pct)
    base = _amortize(balance, rate, monthly_payment)
    if "error" in base:
        return base
    extra = min(extra_payment, balance)
    fee = extra * fee_pct / 100
    new_balance = balance - extra
    if mode == "reduce_payment":
        new_payment = _payment(new_balance, rate, base["months"])
        after = _amortize(new_balance, rate, new_payment) if new_balance > 0 else {"months": 0, "interest": 0.0}
    else:
        new_payment = monthly_payment
        after = _amortize(new_balance, rate, monthly_payment) if new_balance > 0 else {"months": 0, "interest": 0.0}
    interest_saved = base["interest"] - after["interest"]
    return {
        "mode": mode,
        "without_extra": {"months_left": base["months"], "interest_left": _r(base["interest"])},
        "with_extra": {"months_left": after["months"], "interest_left": _r(after["interest"]), "monthly_payment": _r(new_payment)},
        "months_saved": base["months"] - after["months"],
        "interest_saved": _r(interest_saved),
        "fee": _r(fee),
        "net_saving": _r(interest_saved - fee),
        "guaranteed_return_pct": annual_rate_pct,
        "note": "Each euro repaid 'earns' the loan's rate risk-free; compare with what savings earn after tax.",
    }


@canvas_tool(param_descriptions={
    "debts": "List of {name, balance, annual_rate_pct, min_payment, no_extra (optional bool: never pay more than the minimum, e.g. subsidised loans)}",
    "monthly_budget": "Total amount available for all debt payments each month (minimums + extra)",
    "strategy": "'avalanche' (highest rate first, cheapest), 'snowball' (smallest balance first) or 'listed' (the order given)",
    "lump_sums": "Optional list of {month (1 = next month), amount} one-off amounts to put towards debt",
})
def debt_payoff_plan(
    debts: List[Dict[str, Any]],
    monthly_budget: float,
    strategy: str = "avalanche",
    lump_sums: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Simulate paying off several debts with a fixed monthly budget; compare with minimum payments only."""
    def simulate(budget: Optional[float], lumps: Dict[int, float]) -> Dict[str, Any]:
        state = [{**d, "balance": float(d["balance"]), "interest": 0.0, "paid_off_month": None} for d in debts]
        if strategy == "snowball":
            order = sorted(state, key=lambda d: d["balance"])
        elif strategy == "listed":
            order = list(state)
        else:
            order = sorted(state, key=lambda d: -float(d.get("annual_rate_pct", 0)))
        month = 0
        while any(d["balance"] > 0.005 for d in state) and month < MAX_MONTHS:
            month += 1
            for d in state:
                if d["balance"] > 0.005:
                    i = d["balance"] * _monthly_rate(float(d.get("annual_rate_pct", 0)))
                    d["balance"] += i
                    d["interest"] += i
            spent = 0.0
            for d in state:
                if d["balance"] > 0.005:
                    pay = min(float(d.get("min_payment", 0)), d["balance"])
                    d["balance"] -= pay
                    spent += pay
            extra = (budget - spent if budget is not None else 0.0) + lumps.get(month, 0.0)
            for d in order:
                if extra <= 0.005:
                    break
                if d["balance"] > 0.005 and not d.get("no_extra"):
                    pay = min(extra, d["balance"])
                    d["balance"] -= pay
                    extra -= pay
            for d in state:
                if d["balance"] <= 0.005 and d["paid_off_month"] is None:
                    d["paid_off_month"] = month
        return {
            "months": month,
            "all_paid": all(d["balance"] <= 0.005 for d in state),
            "total_interest": _r(sum(d["interest"] for d in state)),
            "debts": [{"name": d["name"], "paid_off_month": d["paid_off_month"], "interest": _r(d["interest"])} for d in state],
        }

    minimums = sum(float(d.get("min_payment", 0)) for d in debts)
    warnings = []
    if monthly_budget < minimums:
        warnings.append(f"The budget ({monthly_budget}) is below the sum of minimum payments ({_r(minimums)}).")
    lumps = {int(l["month"]): float(l["amount"]) for l in lump_sums or []}
    plan = simulate(monthly_budget, lumps)
    baseline = simulate(None, {})
    for result in (plan, baseline):
        if not result["all_paid"]:
            warnings.append(f"Some debts are not paid off within {MAX_MONTHS // 12} years in one of the scenarios.")
            break
    return {
        "strategy": strategy,
        "plan": plan,
        "minimum_payments_only": baseline,
        "interest_saved_vs_minimums": _r(baseline["total_interest"] - plan["total_interest"]),
        "warnings": warnings,
        "note": "Avalanche minimises interest; snowball gives quicker early wins. Month 1 = next month.",
    }


@canvas_tool(param_descriptions={
    "initial": "Amount invested today",
    "monthly_contribution": "Amount added every month",
    "annual_return_pct": "Assumed nominal annual return in %",
    "years": "Number of years",
    "annual_inflation_pct": "Assumed inflation in % (to express the result in today's money)",
    "annual_contribution_increase_pct": "Yearly increase of the monthly contribution in %",
})
def savings_projection(
    initial: float,
    monthly_contribution: float,
    annual_return_pct: float,
    years: float,
    annual_inflation_pct: float = 2.0,
    annual_contribution_increase_pct: float = 0.0,
) -> Dict[str, Any]:
    """Project savings or investments with monthly contributions; nominal and in today's money."""
    months = int(round(years * 12))
    rate = _monthly_rate(annual_return_pct)
    value, contributed, contribution = float(initial), float(initial), float(monthly_contribution)
    yearly = []
    for m in range(1, months + 1):
        value = value * (1 + rate) + contribution
        contributed += contribution
        if m % 12 == 0:
            year = m // 12
            real = value / (1 + annual_inflation_pct / 100) ** year
            yearly.append({"year": year, "value": _r(value), "value_today_money": _r(real), "contributed": _r(contributed)})
            contribution *= 1 + annual_contribution_increase_pct / 100
    real_value = value / (1 + annual_inflation_pct / 100) ** (months / 12)
    step = max(1, len(yearly) // 10)
    return {
        "final_value": _r(value),
        "final_value_today_money": _r(real_value),
        "total_contributed": _r(contributed),
        "growth": _r(value - contributed),
        "by_year": yearly[step - 1::step] if len(yearly) > 10 else yearly,
        "note": "Returns are assumptions, not promises, and are before tax.",
    }


@canvas_tool(param_descriptions={
    "target_amount": "Amount to reach",
    "initial": "Amount already saved",
    "annual_return_pct": "Assumed annual return in % (0 for cash)",
    "years": "Deadline in years (to compute the monthly amount needed)",
    "monthly_contribution": "Planned monthly amount (to compute how long it takes); leave 0 to compute the amount needed",
})
def goal_planner(
    target_amount: float,
    initial: float = 0.0,
    annual_return_pct: float = 0.0,
    years: float = 0.0,
    monthly_contribution: float = 0.0,
) -> Dict[str, Any]:
    """Either the monthly saving needed to reach a goal by a date, or how long a monthly saving takes."""
    rate = _monthly_rate(annual_return_pct)
    if monthly_contribution > 0:
        value, months = float(initial), 0
        while value < target_amount and months < MAX_MONTHS:
            value = value * (1 + rate) + monthly_contribution
            months += 1
        if value < target_amount:
            return {"error": f"Not reached within {MAX_MONTHS // 12} years at this pace."}
        return {"months_needed": months, "years_needed": round(months / 12, 1), "final_value": _r(value)}
    months = int(round(years * 12))
    if months <= 0:
        return {"error": "Give either years (to compute the monthly amount) or monthly_contribution."}
    future_initial = initial * (1 + rate) ** months
    remaining = max(0.0, target_amount - future_initial)
    factor = months if rate == 0 else ((1 + rate) ** months - 1) / rate
    return {"monthly_needed": _r(remaining / factor), "months": months, "already_covered_by_initial": _r(min(target_amount, future_initial))}


@canvas_tool(param_descriptions={
    "monthly_essential_costs": "Essential monthly spending (housing, bills, food, transport, debt minimums, insurance)",
    "income_type": "'stable' (permanent employee), 'variable' (commission, contracts) or 'self_employed'",
    "dependants": "Number of people depending on this income",
    "single_income_household": "True if the household relies on one income",
    "current_savings": "Easily accessible savings today",
})
def emergency_fund_target(
    monthly_essential_costs: float,
    income_type: str = "stable",
    dependants: int = 0,
    single_income_household: bool = False,
    current_savings: float = 0.0,
) -> Dict[str, Any]:
    """Recommended emergency fund size and the gap to it."""
    months = {"stable": 3, "variable": 6, "self_employed": 6}.get(income_type, 4)
    reasons = [f"{months} months as a base for {income_type} income"]
    if dependants:
        months += 1
        reasons.append("+1 month for dependants")
    if single_income_household:
        months += 1
        reasons.append("+1 month for a single-income household")
    months = min(months, 12)
    target = monthly_essential_costs * months
    return {
        "recommended_months": months,
        "target": _r(target),
        "current": _r(current_savings),
        "gap": _r(max(0.0, target - current_savings)),
        "reasons": reasons,
        "note": "Keep it in safe, accessible savings, separate from spending money.",
    }


@canvas_tool(param_descriptions={
    "annual_bills": "List of {name, amount, month (1-12)} for bills paid once a year (insurance, car tax, subscriptions)",
    "start_month": "Calendar month (1-12) of the first monthly transfer",
    "current_balance": "Amount already set aside",
    "minimum_balance": "Amount that must always stay in the account",
})
def sinking_fund_plan(
    annual_bills: List[Dict[str, Any]],
    start_month: int,
    current_balance: float = 0.0,
    minimum_balance: float = 0.0,
) -> Dict[str, Any]:
    """Level monthly transfer that covers all yearly bills, and the top-up needed so it never runs short."""
    total = sum(float(b["amount"]) for b in annual_bills)
    monthly = total / 12
    balance, lowest = float(current_balance), float(current_balance)
    for i in range(24):
        month = (start_month - 1 + i) % 12 + 1
        balance += monthly
        balance -= sum(float(b["amount"]) for b in annual_bills if int(b["month"]) == month)
        lowest = min(lowest, balance)
    top_up = max(0.0, minimum_balance - lowest)
    return {
        "yearly_total": _r(total),
        "monthly_transfer": _r(monthly),
        "initial_top_up_needed": _r(top_up),
        "lowest_balance_without_top_up": _r(lowest),
        "bills_by_month": sorted(({"month": int(b["month"]), "name": b["name"], "amount": float(b["amount"])} for b in annual_bills), key=lambda b: b["month"]),
        "note": "Move each bill's amount back to the current account a few days before it is charged.",
    }


NEEDS = {"housing", "rent", "mortgage", "utilities", "groceries", "transport", "health", "insurance",
         "education", "childcare", "debt", "debt_payments", "taxes"}


@canvas_tool(param_descriptions={
    "net_monthly_income": "Take-home pay per month (average, including subsidies spread over 12 if relevant)",
    "expenses": "Monthly spending by category, e.g. {\"housing\": 650, \"groceries\": 300, \"eating_out\": 120}",
    "debt_payments": "Monthly loan and card payments, if not already inside expenses",
    "max_debt_service_ratio": "Prudential limit for debt payments / net income (e.g. 0.5)",
})
def budget_analysis(
    net_monthly_income: float,
    expenses: Dict[str, float],
    debt_payments: float = 0.0,
    max_debt_service_ratio: float = 0.5,
) -> Dict[str, Any]:
    """Savings rate, needs/wants split and debt-service ratio for a monthly budget."""
    spending = sum(float(v) for v in expenses.values())
    debt_in_expenses = sum(float(v) for k, v in expenses.items() if "debt" in k.lower() or "loan" in k.lower() or "mortgage" in k.lower())
    total_debt = debt_payments + debt_in_expenses
    outgoing = spending + debt_payments
    needs = sum(float(v) for k, v in expenses.items() if k.lower() in NEEDS) + debt_payments
    surplus = net_monthly_income - outgoing
    dsti = total_debt / net_monthly_income if net_monthly_income else 0.0
    return {
        "total_outgoing": _r(outgoing),
        "surplus": _r(surplus),
        "savings_rate_pct": _r(100 * surplus / net_monthly_income) if net_monthly_income else 0,
        "needs_pct": _r(100 * needs / net_monthly_income) if net_monthly_income else 0,
        "wants_pct": _r(100 * (outgoing - needs) / net_monthly_income) if net_monthly_income else 0,
        "debt_service_ratio_pct": _r(100 * dsti),
        "debt_service_above_limit": dsti > max_debt_service_ratio,
        "largest_categories": sorted(expenses.items(), key=lambda kv: -float(kv[1]))[:5],
        "benchmark": "A common guide: needs <= 50%, wants <= 30%, saving/extra debt >= 20% of net income.",
    }


@canvas_tool(param_descriptions={
    "assets": "Map of asset name -> current value, e.g. {\"current account\": 1200, \"home\": 250000}",
    "liabilities": "Map of debt name -> outstanding balance",
    "liquid_assets": "Names (keys of assets) that can be accessed within days",
})
def net_worth(assets: Dict[str, float], liabilities: Dict[str, float], liquid_assets: Optional[List[str]] = None) -> Dict[str, Any]:
    """Net worth, liquidity and leverage from lists of assets and debts."""
    total_assets = sum(float(v) for v in assets.values())
    total_liabilities = sum(float(v) for v in liabilities.values())
    liquid = sum(float(assets.get(k, 0)) for k in liquid_assets or [])
    return {
        "total_assets": _r(total_assets),
        "total_liabilities": _r(total_liabilities),
        "net_worth": _r(total_assets - total_liabilities),
        "liquid_assets": _r(liquid),
        "debt_to_assets_pct": _r(100 * total_liabilities / total_assets) if total_assets else None,
    }


@canvas_tool(param_descriptions={
    "current_age": "Age today",
    "retirement_age": "Planned retirement age",
    "current_savings": "Retirement savings today",
    "monthly_contribution": "Monthly amount saved for retirement (in today's money)",
    "desired_monthly_income": "Monthly income wanted in retirement, in today's money",
    "expected_state_pension_monthly": "Expected public pension per month, in today's money (0 if unknown)",
    "annual_return_pct": "Assumed nominal annual return in %",
    "annual_inflation_pct": "Assumed inflation in %",
    "withdrawal_rate_pct": "Sustainable yearly withdrawal rate in % (3-4 is common)",
})
def retirement_projection(
    current_age: int,
    retirement_age: int,
    current_savings: float,
    monthly_contribution: float,
    desired_monthly_income: float,
    expected_state_pension_monthly: float = 0.0,
    annual_return_pct: float = 5.0,
    annual_inflation_pct: float = 2.0,
    withdrawal_rate_pct: float = 3.5,
) -> Dict[str, Any]:
    """Capital needed for retirement vs the capital on track, everything in today's money."""
    years = max(0, retirement_age - current_age)
    real_annual = (1 + annual_return_pct / 100) / (1 + annual_inflation_pct / 100) - 1
    rate = (1 + real_annual) ** (1 / 12) - 1
    months = years * 12
    projected = current_savings * (1 + rate) ** months
    factor = months if rate == 0 else ((1 + rate) ** months - 1) / rate
    projected += monthly_contribution * factor
    gap_income = max(0.0, desired_monthly_income - expected_state_pension_monthly)
    needed = gap_income * 12 / (withdrawal_rate_pct / 100)
    shortfall = max(0.0, needed - projected)
    extra_monthly = shortfall / factor if factor else shortfall
    return {
        "years_to_retirement": years,
        "real_return_pct": _r(100 * real_annual),
        "capital_needed_today_money": _r(needed),
        "capital_projected_today_money": _r(projected),
        "shortfall": _r(shortfall),
        "extra_monthly_needed": _r(extra_monthly),
        "on_track": shortfall == 0,
        "note": "All figures in today's money. State pensions decades ahead are uncertain; returns are assumptions.",
    }


@canvas_tool(param_descriptions={
    "starting_balance": "Money in the account today",
    "monthly_income": "Regular monthly income",
    "monthly_expenses": "Regular monthly spending including debt payments",
    "months": "Number of months to project",
    "extra_income": "List of {month (1 = next month), amount, label}: bonuses, subsidies, refunds",
    "extra_expenses": "List of {month, amount, label}: yearly bills, planned purchases",
})
def cash_flow_projection(
    starting_balance: float,
    monthly_income: float,
    monthly_expenses: float,
    months: int = 12,
    extra_income: Optional[List[Dict[str, Any]]] = None,
    extra_expenses: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Month-by-month balance projection, with the lowest point and any months below zero."""
    balance, rows = float(starting_balance), []
    for m in range(1, int(months) + 1):
        inc = sum(float(e["amount"]) for e in extra_income or [] if int(e["month"]) == m)
        out = sum(float(e["amount"]) for e in extra_expenses or [] if int(e["month"]) == m)
        balance += monthly_income + inc - monthly_expenses - out
        rows.append({"month": m, "balance": _r(balance), "extra_in": _r(inc), "extra_out": _r(out)})
    lowest = min(rows, key=lambda r: r["balance"]) if rows else {"month": 0, "balance": _r(balance)}
    return {
        "end_balance": _r(balance),
        "lowest_balance": lowest["balance"],
        "lowest_month": lowest["month"],
        "months_below_zero": [r["month"] for r in rows if r["balance"] < 0],
        "by_month": rows,
    }
