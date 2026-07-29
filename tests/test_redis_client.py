"""Redis 临时状态客户端及升级清理测试。"""

from memory import redis_client


class RecordingRedis:
    def __init__(self):
        self.deleted_keys = ()

    def delete(self, *keys):
        self.deleted_keys = keys
        return len(keys)


def test_delete_legacy_conversation_keys_only_targets_one_session(monkeypatch):
    client = RecordingRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: client)

    deleted = redis_client.delete_legacy_conversation_keys("user-1", "session-1")

    assert deleted == 4
    assert client.deleted_keys == (
        "qa:user-1:session:session-1:summary",
        "qa:user-1:session:session-1:messages",
        "qa:user-1:session:session-1:round_count",
        "qa:user-1:session:session-1:next_compress_round",
    )
