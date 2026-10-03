"""
Transaction categorisation for agents, using the active jurisdiction's keyword rules.
"""

from typing import Any, Dict, List

from cfo.statements import summarize
from plugins.base_tool import canvas_tool
from plugins.tax_tools import active_pack


@canvas_tool(param_descriptions={
    "transactions": "List of {date (YYYY-MM-DD), description, amount (negative = money out)}",
})
def categorize_transactions(transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Categorise transactions and summarise monthly income, spending by category and recurring payments."""
    return summarize(transactions, active_pack().get("transaction_categories", {}))
