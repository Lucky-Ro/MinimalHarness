import ast
import math
import operator
from typing import Any

from agent.adapters.tool_schemas import BUILTIN_SCHEMAS
from agent.domain.tool import ToolContext


_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_INT_BITS = 4096
_MAX_POWER = 1000


def _evaluate(node: ast.expr) -> int | float:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        result = node.value
    elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        result = _UNARY[type(node.op)](_evaluate(node.operand))
    elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > _MAX_POWER:
                raise ValueError("指数过大")
            if isinstance(left, int) and isinstance(right, int) and right > 0:
                # A lower bound rules out huge powers before allocating the integer.
                if (left.bit_length() - 1) * right >= _MAX_INT_BITS:
                    raise ValueError("计算结果过大")
        result = _BINARY[type(node.op)](left, right)
    else:
        raise ValueError("表达式仅支持实数和算术运算")
    if isinstance(result, int):
        if result.bit_length() > _MAX_INT_BITS:
            raise ValueError("整数结果过大")
    elif not isinstance(result, float) or not math.isfinite(result):
        raise ValueError("结果必须是有限实数")
    return result


class CalculatorTool:
    name = "calculator"
    description = "计算四则运算、括号和幂运算，幂使用 **。"
    parameters = BUILTIN_SCHEMAS[name]

    def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        expression = arguments.get("expression")
        if not isinstance(expression, str) or not expression.strip() or len(expression) > 1000:
            raise ValueError("expression 必须是 1 至 1000 字符的算式")
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except (SyntaxError, RecursionError) as exc:
            raise ValueError("算式语法错误或嵌套过深") from exc
        if sum(1 for _ in ast.walk(tree)) > 256:
            raise ValueError("算式过于复杂")
        return str(_evaluate(tree.body))
