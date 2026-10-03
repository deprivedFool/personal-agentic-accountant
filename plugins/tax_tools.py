"""
Tax calculators driven by the active jurisdiction pack (see jurisdictions/<code>/pack.yaml).

The code holds no country's numbers. ``configure({"jurisdiction": "pt"})`` selects the pack;
countries without tax tables get a clear "not available" answer instead of invented figures.
These are planning estimates; each result carries the pack's verification date and disclaimer.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from cfo.jurisdictions import has_tax_tables, load_pack
from plugins.base_tool import canvas_tool

_PACK: Dict[str, Any] = load_pack("generic")


def configure(settings: Dict[str, Any]) -> None:
    global _PACK
    _PACK = load_pack(settings.get("jurisdiction", "generic"))


def active_pack() -> Dict[str, Any]:
    return _PACK


def _r(value: float) -> float:
    return round(value + 0.0, 2)


def _meta() -> Dict[str, Any]:
    return {"jurisdiction": _PACK["country"], "tax_year": _PACK.get("tax_year"),
            "rules_verified": _PACK.get("verified"), "disclaimer": _PACK.get("disclaimer", "").strip()}


def _unavailable(what: str) -> Dict[str, Any]:
    return {"available": False,
            "message": f"No {what} tables for {_PACK['country']} yet. Give indicative reasoning only, "
                       "clearly labelled, and recommend confirming with the local tax authority.",
            **_meta()}


def progressive_tax(income: float, brackets: Sequence[Sequence[Optional[float]]]) -> float:
    """Tax on ``income`` with [upper_limit, marginal_rate] brackets (None = no limit)."""
    tax, lower = 0.0, 0.0
    for upper, rate in brackets:
        top = income if upper is None else min(income, upper)
        if top > lower:
            tax += (top - lower) * rate
        if upper is None or income <= upper:
            break
        lower = upper
    return tax


def marginal_rate(income: float, brackets: Sequence[Sequence[Optional[float]]]) -> float:
    for upper, rate in brackets:
        if upper is None or income <= upper:
            return rate
    return brackets[-1][1]


def _surcharge(income: float, bands: Sequence[Sequence[Optional[float]]]) -> float:
    total = 0.0
    for lower, upper, rate in bands:
        top = income if upper is None else min(income, upper)
        if top > lower:
            total += (top - lower) * rate
    return total


def _person_income(
    employment: float, self_employment: float, activity: str, young_year: int, age: Optional[int]
) -> Dict[str, float]:
    """Taxable (net) income of one person after exemptions and specific deductions."""
    it = _PACK["income_tax"]
    young = it.get("young_people_regime", {})
    exempt = 0.0
    if young_year and (age is None or age <= young.get("max_age", 35)):
        share = young.get("exempt_share_by_year", {}).get(int(young_year), 0.0)
        exempt = min(share * (employment + self_employment), young.get("annual_exempt_cap", 0.0))
    gross = employment + self_employment
    exempt_a = exempt * employment / gross if gross else 0.0
    exempt_b = exempt - exempt_a

    emp = it["employment"]
    social_security = employment * emp["employee_social_security_rate"]
    employment_after = employment - exempt_a
    specific = min(max(emp["specific_deduction"], social_security), employment_after) if employment_after > 0 else 0.0
    coefficient = it["self_employment"]["simplified_regime_coefficients"].get(activity, 0.75)
    net_a = max(0.0, employment_after - specific)
    net_b = max(0.0, (self_employment - exempt_b) * coefficient)
    return {"gross": gross, "exempt": exempt, "social_security": social_security, "specific_deduction": specific,
            "employment_net": net_a, "self_employment_net": net_b, "taxable": net_a + net_b}


def _dependant_deductions(ages: Sequence[int]) -> float:
    d = _PACK["income_tax"]["deductions"]
    total = 0.0
    for index, age in enumerate(sorted(ages)):
        if index >= 1 and age <= 6:
            total += d["dependant_aged_6_or_less_from_second"]
        elif age <= 3:
            total += d["dependant_aged_3_or_less"]
        else:
            total += d["dependant"]
    return total


def _retirement_cap(age: Optional[int]) -> float:
    caps = _PACK["income_tax"]["deductions"]["retirement_savings"]["caps_by_age"]
    for limit, cap in caps:
        if limit is None or (age is not None and age <= limit):
            return cap
    return caps[-1][1]


def _global_cap(taxable_per_head: float, dependants: int) -> Optional[float]:
    g = _PACK["income_tax"]["deductions"]["global_cap"]
    if taxable_per_head <= g["no_cap_below"]:
        return None
    if taxable_per_head > g["upper_income"]:
        cap = g["min"]
    else:
        span = g["upper_income"] - g["lower_income"]
        cap = g["min"] + (g["max"] - g["min"]) * (g["upper_income"] - taxable_per_head) / span
    if dependants >= 3:
        cap *= 1 + g["increase_per_dependant_from_three"] * dependants
    return cap


@canvas_tool(param_descriptions={
    "employment_income": "Taxpayer's gross yearly employment income (all payments, e.g. 14 salaries)",
    "self_employment_income": "Taxpayer's gross yearly self-employment income",
    "self_employment_activity": "Simplified-regime activity type, e.g. 'professional_services', 'other_services', 'sales_and_hospitality'",
    "filing": "'single' or 'joint' (married/civil union filing together)",
    "spouse_employment_income": "Spouse's gross yearly employment income (joint filing)",
    "spouse_self_employment_income": "Spouse's gross yearly self-employment income (joint filing)",
    "dependants_ages": "Ages of dependants, e.g. [2, 7]",
    "taxpayer_age": "Taxpayer's age on 1 January",
    "spouse_age": "Spouse's age on 1 January",
    "single_parent": "True for single-parent households",
    "health_expenses": "Yearly health expenses (household)",
    "education_expenses": "Yearly education expenses (household)",
    "rent_paid": "Yearly rent paid for the permanent home",
    "vat_on_eligible_invoices": "VAT paid on invoices that give a VAT deduction (restaurants, repairs, hairdressers...)",
    "general_family_expenses": "Yearly general family expenses (most households reach the cap)",
    "retirement_savings_contributions": "Taxpayer's yearly retirement-plan (e.g. PPR) contributions",
    "spouse_retirement_savings_contributions": "Spouse's yearly retirement-plan contributions",
    "young_regime_year": "Year of the young-people regime being used (e.g. IRS Jovem: 1-10), 0 if none",
    "spouse_young_regime_year": "Spouse's year of the young-people regime, 0 if none",
    "special_regime": "'' or a special regime id from the pack (e.g. 'ifici')",
    "tax_withheld": "Tax already withheld during the year (to estimate refund or payment)",
})
def estimate_income_tax(
    employment_income: float = 0.0,
    self_employment_income: float = 0.0,
    self_employment_activity: str = "professional_services",
    filing: str = "single",
    spouse_employment_income: float = 0.0,
    spouse_self_employment_income: float = 0.0,
    dependants_ages: Optional[List[int]] = None,
    taxpayer_age: Optional[int] = None,
    spouse_age: Optional[int] = None,
    single_parent: bool = False,
    health_expenses: float = 0.0,
    education_expenses: float = 0.0,
    rent_paid: float = 0.0,
    vat_on_eligible_invoices: float = 0.0,
    general_family_expenses: float = 0.0,
    retirement_savings_contributions: float = 0.0,
    spouse_retirement_savings_contributions: float = 0.0,
    young_regime_year: int = 0,
    spouse_young_regime_year: int = 0,
    special_regime: str = "",
    tax_withheld: float = 0.0,
) -> Dict[str, Any]:
    """Estimate yearly personal income tax: taxable income, tax, deductions, refund/payment."""
    if not has_tax_tables(_PACK):
        return _unavailable("income tax")
    it = _PACK["income_tax"]
    d = it["deductions"]
    joint = filing == "joint"
    ages = list(dependants_ages or [])
    notes: List[str] = []

    people = [_person_income(employment_income, self_employment_income, self_employment_activity, young_regime_year, taxpayer_age)]
    if joint:
        people.append(_person_income(spouse_employment_income, spouse_self_employment_income,
                                     self_employment_activity, spouse_young_regime_year, spouse_age))
    divisor = it.get("joint_filing_divisor", 2) if joint else 1

    flat_tax = 0.0
    regime = it.get("special_regimes", {}).get(special_regime) if special_regime else None
    if regime:
        flat_tax = people[0]["taxable"] * regime["flat_rate"]
        notes.append(f"{regime['name']}: taxpayer's eligible income taxed at {regime['flat_rate']:.0%} (assumed all eligible).")
        progressive_base = sum(p["taxable"] for p in people[1:])
    else:
        progressive_base = sum(p["taxable"] for p in people)

    taxable = sum(p["taxable"] for p in people)
    per_head = progressive_base / divisor
    gross_tax = divisor * (progressive_tax(per_head, it["brackets"]) + _surcharge(per_head, it.get("solidarity_surcharge", [])))
    gross_tax += flat_tax

    # Minimum income: tax may not push income below the legal minimum (simplified).
    gross_income = sum(p["gross"] - p["exempt"] for p in people)
    floor = it.get("minimum_income", 0) * (2 if joint else 1)
    if floor and gross_income - gross_tax < floor:
        limited = max(0.0, gross_income - floor)
        if limited < gross_tax:
            notes.append("Minimum-income rule (mínimo de existência) limited the tax.")
            gross_tax = limited

    taxpayers = 2 if joint else 1
    gf = d["general_family"]
    gf_rate, gf_cap = (gf["single_parent_rate"], gf["single_parent_cap"]) if single_parent else (gf["rate"], gf["cap"])
    capped = {
        "health": min(health_expenses * d["health"]["rate"], d["health"]["cap"]),
        "education": min(education_expenses * d["education"]["rate"], d["education"]["cap"]),
        "rent": min(rent_paid * d["rent"]["rate"], d["rent"]["cap"]),
        "vat_invoices": min(vat_on_eligible_invoices * d["vat_invoices"]["rate"], d["vat_invoices"]["cap"]),
        "retirement_savings": min(retirement_savings_contributions * d["retirement_savings"]["rate"], _retirement_cap(taxpayer_age))
        + (min(spouse_retirement_savings_contributions * d["retirement_savings"]["rate"], _retirement_cap(spouse_age)) if joint else 0.0),
    }
    capped_total = sum(capped.values())
    cap = _global_cap(per_head if progressive_base else taxable / divisor, len(ages))
    if cap is not None and capped_total > cap:
        notes.append(f"Global deduction cap of {_r(cap)} applied to health/education/rent/VAT/retirement deductions.")
        capped_total = cap
    uncapped = {
        "dependants": _dependant_deductions(ages),
        "general_family": min(general_family_expenses * gf_rate, gf_cap * taxpayers),
    }
    deductions = capped_total + sum(uncapped.values())
    tax_due = max(0.0, gross_tax - deductions)
    unused = max(0.0, deductions - gross_tax)
    if unused > 0:
        notes.append(f"{_r(unused)} of deductions can't be used: the tax before deductions is lower. Extra deductions (e.g. PPR) would be worth nothing.")
    if young_regime_year:
        notes.append("Young-people regime (IRS Jovem) exemption applied to employment/self-employment income (simplified).")
    effective_base = sum(p["gross"] for p in people)

    return {
        "available": True,
        "filing": filing,
        "per_person": [{k: _r(v) for k, v in p.items()} for p in people],
        "taxable_income": _r(taxable),
        "marginal_rate_pct": _r(100 * marginal_rate(per_head, it["brackets"])),
        "tax_before_deductions": _r(gross_tax),
        "deductions": {**{k: _r(v) for k, v in capped.items()}, **{k: _r(v) for k, v in uncapped.items()},
                       "global_cap": _r(cap) if cap is not None else None, "total_used": _r(min(deductions, gross_tax))},
        "tax_due": _r(tax_due),
        "effective_rate_pct": _r(100 * tax_due / effective_base) if effective_base else 0.0,
        "tax_withheld": _r(tax_withheld),
        "refund_or_payment": _r(tax_withheld - tax_due),
        "refund_or_payment_meaning": "positive = refund, negative = amount to pay",
        "notes": notes,
        **_meta(),
    }


@canvas_tool(param_descriptions={
    "gross_monthly_salary": "Gross monthly base salary",
    "payments_per_year": "Salary payments per year (14 in Portugal when subsidies are paid separately)",
    "dependants_ages": "Ages of dependants",
    "taxpayer_age": "Age on 1 January",
    "young_regime_year": "Young-people regime year (e.g. IRS Jovem 1-10), 0 if none",
    "filing": "'single' or 'joint'",
    "spouse_employment_income": "Spouse's gross yearly employment income (joint filing)",
})
def estimate_net_salary(
    gross_monthly_salary: float,
    payments_per_year: int = 14,
    dependants_ages: Optional[List[int]] = None,
    taxpayer_age: Optional[int] = None,
    young_regime_year: int = 0,
    filing: str = "single",
    spouse_employment_income: float = 0.0,
) -> Dict[str, Any]:
    """Approximate take-home pay from a gross salary (yearly tax spread over the payments)."""
    if not has_tax_tables(_PACK):
        return _unavailable("salary")
    annual = gross_monthly_salary * payments_per_year
    tax = estimate_income_tax.func(employment_income=annual, dependants_ages=dependants_ages, taxpayer_age=taxpayer_age,
                              young_regime_year=young_regime_year, filing=filing,
                              spouse_employment_income=spouse_employment_income, general_family_expenses=10**6)
    ss = annual * _PACK["income_tax"]["employment"]["employee_social_security_rate"]
    own_share = tax["tax_due"] if filing != "joint" else tax["tax_due"] * annual / max(annual + spouse_employment_income, 1)
    net = annual - ss - own_share
    return {
        "gross_yearly": _r(annual),
        "social_security_yearly": _r(ss),
        "income_tax_yearly": _r(own_share),
        "net_yearly": _r(net),
        "net_per_payment": _r(net / payments_per_year),
        "net_monthly_average": _r(net / 12),
        "note": "Monthly withholding tables differ from the final yearly tax; the difference is settled when filing.",
        **_meta(),
    }


@canvas_tool(param_descriptions={
    "amount": "Interest, dividends or gain (sale price minus purchase price and costs)",
    "kind": "'interest', 'dividends', 'securities_gain', 'crypto_gain' or 'real_estate_gain'",
    "holding_days": "Days the asset was held (matters for crypto)",
    "other_taxable_income": "Other taxable income in the year (to compare the aggregation option)",
})
def capital_income_tax(amount: float, kind: str, holding_days: Optional[int] = None, other_taxable_income: float = 0.0) -> Dict[str, Any]:
    """Tax on investment income or capital gains, with the aggregation option where relevant."""
    if not has_tax_tables(_PACK) or "capital_income" not in _PACK:
        return _unavailable("capital income")
    ci = _PACK["capital_income"]
    brackets = _PACK["income_tax"]["brackets"]
    def at_marginal(extra: float) -> float:
        return progressive_tax(other_taxable_income + extra, brackets) - progressive_tax(other_taxable_income, brackets)

    if kind == "crypto_gain" and holding_days is not None and holding_days >= ci["crypto_exempt_after_days"]:
        return {"tax": 0.0, "rule": f"Held {holding_days} days: exempt (>= {ci['crypto_exempt_after_days']} days).", **_meta()}
    if kind == "real_estate_gain":
        taxable = amount * ci["real_estate_gain_taxable_share"]
        return {"tax": _r(at_marginal(taxable)), "taxable_part": _r(taxable),
                "rule": f"{ci['real_estate_gain_taxable_share']:.0%} of the gain is added to other income and taxed at progressive rates.",
                "notes": ci.get("notes", []), **_meta()}
    flat = amount * ci["flat_rate"]
    result = {"tax_at_flat_rate": _r(flat), "flat_rate_pct": _r(100 * ci["flat_rate"]), **_meta()}
    if ci.get("aggregation_optional"):
        aggregated = at_marginal(amount)
        result.update(tax_if_aggregated=_r(aggregated),
                      better_option="aggregate" if aggregated < flat else "flat rate",
                      aggregation_note="Choosing aggregation applies to all income of that kind in the year.")
    result["notes"] = ci.get("notes", [])
    return result


def _transfer_tax(price: float, table: Dict[str, Any]) -> float:
    marginal = table["marginal"]
    if price <= marginal[-1][0]:
        return progressive_tax(price, marginal)
    for upper, rate in table["flat"]:
        if upper is None or price <= upper:
            return price * rate
    return price * table["flat"][-1][1]


@canvas_tool(param_descriptions={
    "price": "Purchase price (or tax value, if higher)",
    "use": "'permanent_home' or 'other_home'",
    "buyer_age": "Age of the buyer (young-buyer relief may apply)",
    "first_home": "True if it's the buyer's first permanent home",
    "mortgage_amount": "Mortgage amount (stamp duty on the loan)",
})
def property_purchase_costs(
    price: float,
    use: str = "permanent_home",
    buyer_age: Optional[int] = None,
    first_home: bool = False,
    mortgage_amount: float = 0.0,
) -> Dict[str, Any]:
    """Property transfer tax and stamp duties when buying a home."""
    prop = _PACK.get("property")
    if not prop:
        return _unavailable("property tax")
    tt = prop["transfer_tax"]
    table = tt["permanent_home"] if use == "permanent_home" else tt["other_home"]
    transfer = _transfer_tax(price, table)
    stamp = price * prop["stamp_duty_purchase_rate"]
    notes = []
    young = tt.get("young_buyer")
    if young and use == "permanent_home" and first_home and buyer_age is not None and buyer_age <= young["max_age"]:
        if price <= young["fully_exempt_up_to"]:
            transfer, stamp = 0.0, 0.0
            notes.append("Young-buyer relief: fully exempt from transfer tax and purchase stamp duty.")
        elif price <= young["partial_relief_up_to"]:
            excess = price - young["fully_exempt_up_to"]
            transfer = excess * young["partial_rate"]
            stamp = excess * prop["stamp_duty_purchase_rate"]
            notes.append("Young-buyer relief: only the part above the exempt limit is taxed.")
    loan_stamp = mortgage_amount * prop.get("stamp_duty_mortgage_rate", 0.0)
    total = transfer + stamp + loan_stamp
    notes.append("Add deed/registration and bank fees (often 1,000-2,500 EUR) to these taxes.")
    return {
        "transfer_tax": _r(transfer), "transfer_tax_name": tt.get("name", "transfer tax"),
        "stamp_duty_purchase": _r(stamp), "stamp_duty_mortgage": _r(loan_stamp),
        "total_taxes": _r(total), "total_pct_of_price": _r(100 * total / price) if price else 0.0,
        "notes": notes, **_meta(),
    }


@canvas_tool(param_descriptions={
    "contribution": "Planned yearly contribution to the retirement plan (e.g. PPR)",
    "age": "Age on 1 January",
    "tax_before_deductions": "Tax before deductions (from estimate_income_tax)",
    "other_deductions_used": "Deductions already used against that tax",
    "remaining_global_cap": "Room left under the global deduction cap (None if no cap)",
})
def retirement_savings_benefit(
    contribution: float,
    age: Optional[int],
    tax_before_deductions: float,
    other_deductions_used: float,
    remaining_global_cap: Optional[float] = None,
) -> Dict[str, Any]:
    """Real tax saving from a retirement-plan contribution, given the tax actually due and the caps."""
    if not has_tax_tables(_PACK):
        return _unavailable("retirement-plan deduction")
    rs = _PACK["income_tax"]["deductions"]["retirement_savings"]
    nominal = min(contribution * rs["rate"], _retirement_cap(age))
    room_in_tax = max(0.0, tax_before_deductions - other_deductions_used)
    usable = min(nominal, room_in_tax, remaining_global_cap if remaining_global_cap is not None else nominal)
    best = _retirement_cap(age) / rs["rate"]
    return {
        "deduction_nominal": _r(nominal),
        "deduction_actually_saved": _r(usable),
        "effective_return_pct": _r(100 * usable / contribution) if contribution else 0.0,
        "contribution_for_full_benefit": _r(best),
        "verdict": "worth it for the tax benefit" if usable >= 0.5 * nominal and usable > 0 else "little or no tax benefit at this income/deduction level",
        **_meta(),
    }


@canvas_tool(param_descriptions={"stage": "Optional filter: first, core, when_tax_is_due, optional, later, small_only, avoid, anytime, depends"})
def investment_options(stage: str = "") -> Dict[str, Any]:
    """Catalogue of savings/investment options with risk, liquidity, tax and when each fits."""
    options = [o for o in _PACK.get("investment_options", []) if not stage or o.get("stage") == stage]
    return {"options": options, "order_of_operations": [
        "1. Pay all bills and minimums; keep a small cash cushion.",
        "2. Clear expensive debt (highest rate first).",
        "3. Build the emergency fund.",
        "4. Use tax-advantaged accounts where the benefit is real.",
        "5. Invest long-term money in low-cost diversified funds; rebalance yearly.",
    ], **_meta()}


@canvas_tool(param_descriptions={"topic": "One of: calendar, filing, banking, salary, retirement, property, capital_income, reference_values, sources, all_topics"})
def jurisdiction_facts(topic: str) -> Dict[str, Any]:
    """Look up verified country facts (deadlines, bank rules, salary structure...) instead of recalling them."""
    lookup = {
        "calendar": _PACK.get("calendar"),
        "filing": _PACK.get("income_tax", {}).get("filing"),
        "banking": _PACK.get("banking"),
        "salary": _PACK.get("salary"),
        "retirement": _PACK.get("retirement"),
        "property": _PACK.get("property"),
        "capital_income": _PACK.get("capital_income"),
        "reference_values": _PACK.get("reference_values"),
        "sources": _PACK.get("sources"),
    }
    if topic == "all_topics" or topic not in lookup:
        return {"topics": [k for k, v in lookup.items() if v], **_meta()}
    return {"topic": topic, "facts": lookup[topic], **_meta()}


def tax_tool_names() -> Tuple[str, ...]:
    return ("estimate_income_tax", "estimate_net_salary", "capital_income_tax", "property_purchase_costs",
            "retirement_savings_benefit", "investment_options", "jurisdiction_facts")
