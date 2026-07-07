"""LangGraph 工作流定义：START -> Agent A -> Agent B -> Agent C -> END"""
import logging

from langgraph.graph import StateGraph, START, END

from agents.state import ResearchState
from agents.decomposer import agent_a_decompose
from agents.doc_searcher import agent_b_retrieve
from agents.summarizer import agent_c_summarize

logger = logging.getLogger(__name__)


def build_graph():
    """构建 LangGraph 工作流"""
    graph = StateGraph(ResearchState)

    graph.add_node("agent_a", agent_a_decompose)
    graph.add_node("agent_b", agent_b_retrieve)
    graph.add_node("agent_c", agent_c_summarize)

    graph.add_edge(START, "agent_a")
    graph.add_edge("agent_a", "agent_b")
    graph.add_edge("agent_b", "agent_c")
    graph.add_edge("agent_c", END)

    return graph.compile()


# 单例
_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
