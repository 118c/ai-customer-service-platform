import pytest

from core.llm_gateway import LocalContinuityProvider, ResilientLLMGateway
from memory.conversation_memory import MemoryManager, MsgRole


@pytest.mark.asyncio
async def test_memory_falls_back_when_redis_is_unavailable(tmp_path):
    memory = MemoryManager(
        redis_url="redis://127.0.0.1:1/0",
        chroma_host="127.0.0.1",
        chroma_port=1,
        chroma_path=str(tmp_path / "chroma"),
        llm_gateway=ResilientLLMGateway([LocalContinuityProvider()]),
    )
    try:
        await memory.add_message("E1001", "conv-1", MsgRole.USER, "查询今天考勤")
        context = await memory.get_context("E1001", "conv-1")
        assert [message.content for message in context.recent_messages] == ["查询今天考勤"]
        assert memory._redis_available is False
    finally:
        await memory.close()
