"""Safe, exact arithmetic evaluator exposed to the agent as a tool.

The LLM is never trusted to do math: whenever it needs a derived number that no
analytics tool returns directly, it must call this calculator. Evaluation uses
Decimal (no float drift) and a whitelisted AST (no names, calls or attributes
other than a few pure functions).
"""
from __future__ import annotations

import ast
import re
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation, getcontext

getcontext().prec = 28

_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** int(b) if b == int(b) and abs(b) <= 1000 else _raise("Only integer exponents up to 1000"),
}


def _raise(msg: str):
    raise ValueError(msg)


def _round(x: Decimal, places: Decimal = Decimal(2)) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)


_FUNCS = {
    "round": _round,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": lambda *xs: sum(xs, Decimal(0)),
    "avg": lambda *xs: sum(xs, Decimal(0)) / len(xs),
    "pct": lambda part, whole: part / whole * 100,           # percentage
    "pct_change": lambda old, new: (new - old) / old * 100,  # percent change
}


def _eval(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return Decimal(str(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError(f"Unsupported expression element: {ast.dump(node)[:60]}")


def evaluate(expression: str) -> str:
    """Evaluate an arithmetic expression; returns the exact result as a string."""
    # strip currency symbols and thousands separators ("1,234.50") but keep argument commas ("min(1, 2)")
    cleaned = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expression.replace("$", "")).strip()
    if len(cleaned) > 300:
        raise ValueError("Expression too long")
    try:
        result = _eval(ast.parse(cleaned, mode="eval"))
    except (DivisionByZero, InvalidOperation, ZeroDivisionError, TypeError) as exc:
        raise ValueError("Math error (division by zero or invalid operation)") from exc
    except SyntaxError as exc:
        raise ValueError("Could not parse expression") from exc
    # normalise: at most 4 decimal places, strip trailing zeros
    result = result.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP).normalize()
    return format(result, "f")
