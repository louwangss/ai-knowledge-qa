"""_extract_search_sources 测试"""
from app.api.routes_chat import _extract_search_sources


def test_extract_from_valid_tavily_output():
    """正常 Tavily 输出提取 URL + title"""
    output = str([
        {"url": "https://example.com/1", "title": "示例1", "content": "..."},
        {"url": "https://example.com/2", "title": "示例2", "content": "..."},
    ])
    sources = _extract_search_sources(output)
    assert len(sources) == 2
    assert "https://example.com/1" in sources[0]["source"]
    assert "示例1" in sources[0]["source"]


def test_extract_from_empty_output():
    """空输出返回空列表"""
    assert _extract_search_sources("") == []
    assert _extract_search_sources("not a list") == []


def test_extract_skips_entries_without_url():
    """跳过没有 url 的条目"""
    output = str([{"title": "无URL", "content": "..."}])
    assert _extract_search_sources(output) == []
