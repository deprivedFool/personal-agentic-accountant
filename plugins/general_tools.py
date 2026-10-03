"""
General-purpose tools: exact arithmetic and pattern matching.
"""

import ast
import operator
import re
from typing import Any, Dict

from plugins.base_tool import canvas_tool


@canvas_tool(
    param_descriptions={
        "pattern": "Python regular expression; if it has groups, the groups are returned",
        "text": "Text to search",
        "ignore_case": "Match case-insensitively",
    }
)
def regex_extract(pattern: str, text: str, ignore_case: bool = False) -> Dict[str, Any]:
    """Find every match of a regular expression in a piece of text."""
    try:
        matches = re.findall(pattern, text, flags=re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        return {"error": f"Invalid pattern: {exc}"}
    return {"count": len(matches), "matches": matches}


_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _evaluate(node: ast.AST) -> float:
    """Evaluate an arithmetic AST, rejecting anything but numbers and operators."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_evaluate(node.operand))
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


@canvas_tool(param_descriptions={"expression": "Arithmetic expression, e.g. '(12 + 30) / 7 * 100'"})
def calculator(expression: str) -> Dict[str, Any]:
    """Safely evaluate an arithmetic expression (+ - * / // % ** and parentheses)."""
    try:
        result = _evaluate(ast.parse(expression, mode="eval").body)
    except (SyntaxError, ValueError, ZeroDivisionError) as exc:
        return {"expression": expression, "error": str(exc)}
    return {"expression": expression, "result": result}
