"""stream_with_idle_timeout 测试"""
import asyncio
from app.stream_utils import stream_with_idle_timeout


def test_normal_stream_passes_through():
    """正常流式不受 timeout 影响"""
    async def run():
        async def gen():
            yield "a"
            yield "b"
            yield "c"

        result = []
        async for item in stream_with_idle_timeout(gen(), timeout=1.0):
            result.append(item)
        return result

    assert asyncio.run(run()) == ["a", "b", "c"]


def test_timeout_raises_timeout_error():
    """idle 超过阈值抛出 TimeoutError"""
    async def run():
        async def gen():
            yield "a"
            await asyncio.sleep(2)  # 超过 1 秒
            yield "b"

        async for _ in stream_with_idle_timeout(gen(), timeout=1.0):
            pass

    try:
        asyncio.run(run())
        assert False, "应该抛出 TimeoutError"
    except TimeoutError:
        pass  # 符合预期
