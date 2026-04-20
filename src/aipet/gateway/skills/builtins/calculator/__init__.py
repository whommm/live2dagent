"""Calculator skill."""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Any

OPERATORS: dict[type[ast.operator], Callable[[Any, Any], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[Any], float]] = {
    ast.USub: operator.neg,
}


def calculate(expression: str) -> str:
    """Safely evaluate a simple math expression."""
    try:
        result = _eval_node(ast.parse(expression, mode="eval").body)
        return str(result)
    except Exception as exc:
        return f"计算错误: {exc}"


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    elif isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        bin_op = type(node.op)
        if bin_op in OPERATORS:
            return OPERATORS[bin_op](left, right)
    elif isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        unary_op = type(node.op)
        if unary_op in UNARY_OPERATORS:
            return UNARY_OPERATORS[unary_op](operand)
    raise ValueError("Unsupported expression")


tools = {
    "calculate": calculate,
}
