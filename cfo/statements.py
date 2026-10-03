"""
Bank-statement parsing and summarising, done in code so agents work from exact totals.

Handles the common export quirks: preamble lines before the header, ';' or ',' delimiters,
decimal commas ("1.234,56"), separate debit/credit columns, and dd-mm-yyyy dates.
"""

import csv
import io
import re
import statistics
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

DATE_HEADERS = ("data mov", "data", "date", "fecha", "datum")
DESCRIPTION_HEADERS = ("descri", "movimento", "details", "description", "concept", "narrative", "merchant", "detalhe")
AMOUNT_HEADERS = ("montante", "valor", "amount", "importância", "importancia", "quantia")
DEBIT_HEADERS = ("débito", "debito", "debit", "saída", "saida", "withdrawal")
CREDIT_HEADERS = ("crédito", "credito", "credit", "entrada", "deposit")
DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%d-%m-%y", "%d/%m/%y")


def _plain(text: str) -> str:
    """Lower-case without accents, for matching."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def parse_amount(raw: str) -> Optional[float]:
    text = re.sub(r"[^\d,.\-+]", "", (raw or "").strip())
    if not text or not re.search(r"\d", text):
        return None
    negative = text.startswith("-") or (raw.strip().startswith("(") and raw.strip().endswith(")"))
    text = text.lstrip("+-")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        text = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else text.replace(",", "")
    elif text.count(".") > 1:
        head, _, tail = text.rpartition(".")
        text = text.replace(".", "") if len(tail) == 3 else f"{head.replace('.', '')}.{tail}"
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def parse_date(raw: str) -> Optional[date]:
    raw = (raw or "").strip()[:10]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _find(header: Sequence[str], options: Sequence[str], exclude: Sequence[str] = ()) -> Optional[int]:
    """Index of the first column whose header contains one of ``options`` (and none of ``exclude``)."""
    plain = [_plain(h) for h in header]
    for option in options:
        for index, cell in enumerate(plain):
            if _plain(option) in cell and not any(_plain(x) in cell for x in exclude):
                return index
    return None


def parse_statement(text: str) -> Optional[List[Dict[str, Any]]]:
    """Return transactions [{date, description, amount}] or None if this isn't a statement."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    sample = "\n".join(lines[:20])
    delimiter = max((";", ",", "\t"), key=sample.count)
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    for start, header in enumerate(rows[:15]):
        date_i = _find(header, DATE_HEADERS)
        desc_i = _find(header, DESCRIPTION_HEADERS)
        amount_i = _find(header, AMOUNT_HEADERS, exclude=("data", "date", "saldo", "balance"))
        debit_i, credit_i = _find(header, DEBIT_HEADERS), _find(header, CREDIT_HEADERS)
        if date_i is None or desc_i is None or (amount_i is None and debit_i is None and credit_i is None):
            continue
        transactions = []
        for row in rows[start + 1:]:
            if len(row) <= max(i for i in (date_i, desc_i, amount_i, debit_i, credit_i) if i is not None):
                continue
            when = parse_date(row[date_i])
            if when is None:
                continue
            if amount_i is not None and debit_i is None:
                amount = parse_amount(row[amount_i])
            else:
                debit = parse_amount(row[debit_i]) if debit_i is not None else None
                credit = parse_amount(row[credit_i]) if credit_i is not None else None
                amount = (abs(credit) if credit else 0.0) - (abs(debit) if debit else 0.0)
                if debit is None and credit is None:
                    amount = None
            if amount is None:
                continue
            transactions.append({"date": when.isoformat(), "description": row[desc_i].strip(), "amount": round(amount, 2)})
        if transactions:
            return transactions
    return None


def categorize(description: str, rules: Dict[str, List[str]]) -> str:
    plain = _plain(description)
    for category, keywords in rules.items():
        if any(_plain(k) in plain for k in keywords):
            return category
    return "other"


def _normalise_merchant(description: str) -> str:
    text = _plain(description)
    text = re.sub(r"\d+[\d/.\-:]*", " ", text)
    text = re.sub(r"[^a-z ]", " ", text)
    return " ".join(text.split()[:4])


def summarize(transactions: List[Dict[str, Any]], rules: Dict[str, List[str]]) -> Dict[str, Any]:
    """Monthly averages by category, top merchants and recurring payments."""
    if not transactions:
        return {"transactions": 0}
    months = sorted({t["date"][:7] for t in transactions})
    n_months = max(1, len(months))
    income = defaultdict(float)
    spending = defaultdict(float)
    merchants: Dict[str, Dict[str, Any]] = {}
    for t in transactions:
        category = t.get("category") or categorize(t["description"], rules)
        amount = float(t["amount"])
        if amount >= 0:
            income[category] += amount
        else:
            spending[category] += -amount
            key = _normalise_merchant(t["description"]) or "unknown"
            entry = merchants.setdefault(key, {"total": 0.0, "by_month": defaultdict(float), "category": category, "example": t["description"]})
            entry["total"] += -amount
            entry["by_month"][t["date"][:7]] += -amount

    min_months = 2 if n_months <= 3 else 3
    recurring = []
    for name, entry in merchants.items():
        values = list(entry["by_month"].values())
        if len(values) >= min_months:
            mean = statistics.mean(values)
            spread = statistics.pstdev(values) / mean if mean else 1
            if spread <= 0.15:
                recurring.append({"payee": entry["example"], "avg_monthly": round(mean, 2), "months_seen": len(values), "category": entry["category"]})

    total_out = sum(spending.values())
    total_in = sum(income.values())
    return {
        "transactions": len(transactions),
        "period": {"from": min(t["date"] for t in transactions), "to": max(t["date"] for t in transactions), "months": n_months},
        "monthly_average_income": round(total_in / n_months, 2),
        "monthly_average_spending": round(total_out / n_months, 2),
        "monthly_average_net": round((total_in - total_out) / n_months, 2),
        "income_by_category_monthly": {k: round(v / n_months, 2) for k, v in sorted(income.items(), key=lambda kv: -kv[1])},
        "spending_by_category_monthly": {k: round(v / n_months, 2) for k, v in sorted(spending.items(), key=lambda kv: -kv[1])},
        "uncategorised_share_pct": round(100 * spending.get("other", 0.0) / total_out, 1) if total_out else 0.0,
        "top_payees": [{"payee": e["example"], "total": round(e["total"], 2), "category": e["category"]}
                       for e in sorted(merchants.values(), key=lambda e: -e["total"])[:10]],
        "recurring_payments": sorted(recurring, key=lambda r: -r["avg_monthly"]),
        "note": "Averages divide by the calendar months the statement touches; a partial first or last month "
                "lowers them. Check recent_transactions for one-off items (e.g. yearly insurance).",
    }
