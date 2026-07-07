"""流式输出工具：idle timeout 保护"""
import asyncio


async def stream_with_idle_timeout(aiter, timeout: float = 30.0):
    """异步迭代器 idle timeout 包装。

    每个 chunk 之间设 idle 超时（不是总超时）。
    超过 timeout 秒未收到下一个 chunk 则抛出 TimeoutError。

    Args:
        aiter: 异步迭代器（如 llm.astream() 的返回值）
        timeout: 两个 chunk 之间允许的最大间隔秒数
    """
    while True:
        try:
            chunk = await asyncio.wait_for(aiter.__anext__(), timeout)
            yield chunk
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            raise TimeoutError(f"LLM 无响应超过 {timeout} 秒")
