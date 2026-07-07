"""Agent 工作流测试：graph 结构 + state 定义 + 子问题解析"""
from agents.state import ResearchState, RetrievedDoc, RetrievedNote
from agents.graph import build_graph


def test_research_state_has_required_fields():
    """ResearchState 包含所有必需字段"""
    required = {
        "original_question", "sub_questions", "retrieved_docs",
        "notes", "episodic_memory", "short_term_memory",
        "final_answer", "user_id",
    }
    # TypedDict 的 __annotations__ 包含所有字段
    assert required.issubset(set(ResearchState.__annotations__.keys()))


def test_retrieved_doc_fields():
    """RetrievedDoc 包含 content/source/sub_question/score"""
    expected = {"content", "source", "sub_question", "score"}
    assert expected == set(RetrievedDoc.__annotations__.keys())


def test_retrieved_note_fields():
    """RetrievedNote 包含 content/concept"""
    expected = {"content", "concept"}
    assert expected == set(RetrievedNote.__annotations__.keys())


def test_graph_builds_without_error():
    """graph 能正常编译"""
    graph = build_graph()
    assert graph is not None


def test_graph_has_three_nodes():
    """graph 包含 agent_a / agent_b / agent_c 三个节点"""
    graph = build_graph()
    # LangGraph 编译后的 graph.nodes 包含所有节点（含 __start__ / __end__）
    node_names = set(graph.nodes.keys())
    assert "agent_a" in node_names
    assert "agent_b" in node_names
    assert "agent_c" in node_names


def test_graph_linear_flow():
    """graph 是线性流程：start -> a -> b -> c -> end

    LangGraph 不同版本的 CompiledStateGraph 暴露的边结构不同：
    - 旧版本：graph.edges 为 dict[str, str | set[str]]
    - 新版本：graph.builder.edges 为 set[tuple[source, target]]

    这里两种都兼容，只要能验证出 a->b 和 b->c 的线性后继即可。
    """
    graph = build_graph()

    # 收集所有 (source, target) 边对
    edges = _collect_edges(graph)

    # 验证 agent_a 的后继是 agent_b
    assert ("agent_a", "agent_b") in edges, \
        f"未找到 agent_a -> agent_b 边，实际边: {edges}"
    # 验证 agent_b 的后继是 agent_c
    assert ("agent_b", "agent_c") in edges, \
        f"未找到 agent_b -> agent_c 边，实际边: {edges}"


def _collect_edges(graph):
    """从 CompiledStateGraph 提取所有 (source, target) 边对。

    兼容多种 LangGraph 版本：
    - graph.builder.edges：set[tuple[str, str]]（新版本）
    - graph.edges：dict[str, str | set[str]]（旧版本）
    """
    edges = set()

    # 新版本：builder.edges 是 set[tuple[source, target]]
    builder = getattr(graph, "builder", None)
    if builder is not None and hasattr(builder, "edges"):
        for edge in builder.edges:
            # edge 可能是 tuple/list (source, target)
            if isinstance(edge, (tuple, list)) and len(edge) == 2:
                edges.add((edge[0], edge[1]))

    # 旧版本：graph.edges 是 dict[source, target | set[target]]
    if hasattr(graph, "edges") and isinstance(graph.edges, dict):
        for src, dst in graph.edges.items():
            if isinstance(dst, str):
                edges.add((src, dst))
            elif isinstance(dst, (set, list, tuple)):
                for d in dst:
                    edges.add((src, d))

    return edges
