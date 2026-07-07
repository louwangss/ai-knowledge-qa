"""calculator 工具：numexpr 安全数学运算"""
import numexpr
from langchain_core.tools import tool


@tool
def calculator(expression: str) -> str:
    """执行数学计算。当需要数值计算时使用。传入的 expression 应为纯数学表达式（如 '2*3+1'），不包含变量赋值或函数调用。"""
    return str(numexpr.evaluate(expression))
