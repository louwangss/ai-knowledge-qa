"""web_search 工具：Tavily 搜索"""
from langchain_core.tools import tool

from config import TAVILY_API_KEY


@tool
def web_search(query: str) -> str:
    """搜索互联网获取最新信息。当文档中没有相关内容或需要最新信息时使用。"""
    from langchain_community.tools import TavilySearchResults

    tavily = TavilySearchResults(max_results=3)
    results = tavily.invoke(query)
    return str(results)
