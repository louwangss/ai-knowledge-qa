"""记忆模块测试：episodic 事件去重逻辑"""
import logging
from unittest.mock import MagicMock

from memory.episodic import record_event


def test_record_event_new_writes():
    """新事件（无重复）正常写入"""
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None

    record_event(
        db, user_id="u1", session_id="s1",
        event_type="qa_completed",
        content="测试内容",
        question_text="测试问题",
    )

    assert db.add.called
    assert db.commit.called


def test_record_event_dedup_skips():
    """30 秒窗口内重复事件被跳过"""
    db = MagicMock()
    existing = MagicMock()  # 模拟已有记录
    db.execute.return_value.scalar_one_or_none.return_value = existing

    record_event(
        db, user_id="u1", session_id="s1",
        event_type="qa_completed",
        content="测试内容",
        question_text="测试问题",
    )

    assert not db.add.called
    assert not db.commit.called


def test_record_event_no_question_dedup_by_content():
    """无 question_text 的事件按 content 去重"""
    db = MagicMock()
    existing = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = existing

    record_event(
        db, user_id="u1", session_id="s1",
        event_type="note_saved",
        content="保存了笔记X",
    )

    assert not db.add.called


def test_record_event_different_content_writes():
    """不同 content 的事件正常写入"""
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None

    record_event(
        db, user_id="u1", session_id="s1",
        event_type="note_saved",
        content="笔记A",
    )
    record_event(
        db, user_id="u1", session_id="s1",
        event_type="note_saved",
        content="笔记B",
    )

    assert db.add.call_count == 2


from memory.short_term import _do_compress, _key


def test_do_compress_writes_summary_to_mysql():
    """_do_compress 生成新摘要后应 upsert 到 session_summary 表"""
    from db.models import SessionSummary

    # mock Redis
    r = MagicMock()
    r.get.return_value = ""  # 无已有摘要
    r.lrange.return_value = [
        '{"role": "user", "content": "hello"}',
        '{"role": "assistant", "content": "hi"}',
    ]

    # mock db_session：query 返回 None（无已有记录）
    db_session = MagicMock()
    db_session.query.return_value.filter.return_value.first.return_value = None

    # mock LLM
    with __import__("unittest.mock").mock.patch("memory.short_term.get_llm") as mock_llm:
        mock_resp = MagicMock()
        mock_resp.content = "这是合并后的摘要"
        mock_llm.return_value.invoke.return_value = mock_resp

        _do_compress(r, "u1", "s1", db_session=db_session)

    # 验证 MySQL 写入
    assert db_session.add.called, "应调用 db_session.add 写入 SessionSummary"
    assert db_session.commit.called, "应调用 db_session.commit"
    added_obj = db_session.add.call_args[0][0]
    assert isinstance(added_obj, SessionSummary)
    assert added_obj.session_id == "s1"
    assert "合并后的摘要" in added_obj.summary


def test_do_compress_updates_existing_summary():
    """已有 summary 记录时应更新而非新增"""
    from db.models import SessionSummary

    r = MagicMock()
    r.get.return_value = "旧摘要内容"
    r.lrange.return_value = [
        '{"role": "user", "content": "new question"}',
    ]

    existing_record = SessionSummary(session_id="s1", summary="旧摘要内容")
    db_session = MagicMock()
    db_session.query.return_value.filter.return_value.first.return_value = existing_record

    with __import__("unittest.mock").mock.patch("memory.short_term.get_llm") as mock_llm:
        mock_resp = MagicMock()
        mock_resp.content = "新摘要"
        mock_llm.return_value.invoke.return_value = mock_resp

        _do_compress(r, "u1", "s1", db_session=db_session)

    # 验证更新而非新增
    assert not db_session.add.called, "已有记录时应更新而非 add"
    assert db_session.commit.called
    assert "新摘要" in existing_record.summary


from memory.short_term import get_short_term_memory


def test_get_short_term_memory_does_not_log_message_content(caplog):
    secret_content = "不可进入日志的私密对话正文"
    r = MagicMock()
    r.get.return_value = None
    r.lrange.return_value = [
        '{"role": "user", "content": "不可进入日志的私密对话正文"}'
    ]
    caplog.set_level(logging.INFO)

    result = get_short_term_memory(r, "u1", "s1")

    assert result["messages"][0]["content"] == secret_content
    assert secret_content not in caplog.text


def test_get_short_term_memory_restores_summary_from_mysql():
    """Redis miss 时应从 MySQL session_summary 表恢复 summary"""
    from db.models import SessionSummary

    r = MagicMock()
    # Redis 全部 miss
    r.get.return_value = None
    r.lrange.return_value = []

    # mock db_session
    db_session = MagicMock()

    # mock _load_from_mysql 返回空列表（触发 init_session 但 summary 单独恢复）
    with __import__("unittest.mock").mock.patch(
        "memory.short_term._load_from_mysql", return_value=[]
    ):
        # mock MySQL 中有 summary 记录
        summary_record = SessionSummary(session_id="s1", summary="从MySQL恢复的摘要")
        db_session.query.return_value.filter.return_value.first.return_value = summary_record

        result = get_short_term_memory(r, "u1", "s1", db_session=db_session)

    assert result["summary"] == "从MySQL恢复的摘要"


def test_get_short_term_memory_no_mysql_summary_returns_none():
    """MySQL 中也无 summary 时返回 None"""
    r = MagicMock()
    r.get.return_value = None
    r.lrange.return_value = []

    db_session = MagicMock()
    db_session.query.return_value.filter.return_value.first.return_value = None

    with __import__("unittest.mock").mock.patch(
        "memory.short_term._load_from_mysql", return_value=[]
    ):
        result = get_short_term_memory(r, "u1", "s1", db_session=db_session)

    assert result["summary"] is None


from memory.short_term import restore_from_mysql_if_needed, append_message


def test_restore_from_mysql_when_redis_expired():
    """Redis 过期时，restore_from_mysql_if_needed 应从 MySQL 恢复历史消息"""
    r = MagicMock()
    r.lrange.return_value = []  # Redis 空（已过期）

    # mock pipeline（init_session 通过 pipeline 批量写入）
    pipe = MagicMock()
    r.pipeline.return_value = pipe

    db_session = MagicMock()
    mysql_messages = [
        {"role": "user", "content": "第一天的第一个问题"},
        {"role": "assistant", "content": "第一天的第一个回答"},
        {"role": "user", "content": "第一天的第二个问题"},
        {"role": "assistant", "content": "第一天的第二个回答"},
    ]

    with __import__("unittest.mock").mock.patch(
        "memory.short_term._load_from_mysql", return_value=mysql_messages
    ):
        restore_from_mysql_if_needed(r, "u1", "s1", db_session)

    # 验证通过 pipeline rpush 恢复消息
    assert pipe.rpush.called, "应通过 pipeline rpush 将 MySQL 消息写入 Redis"
    assert pipe.rpush.call_count == 4  # 4 条消息
    assert r.set.called, "应设置 round_count 和 next_compress_round"
    r.expire.assert_called()  # 续期


def test_restore_skipped_when_redis_has_full_data():
    """Redis 数据和 MySQL 一样多时，不应触发恢复"""
    r = MagicMock()
    # Redis 有 4 条
    r.lrange.return_value = [
        '{"role": "user", "content": "q1"}',
        '{"role": "assistant", "content": "a1"}',
        '{"role": "user", "content": "q2"}',
        '{"role": "assistant", "content": "a2"}',
    ]

    db_session = MagicMock()
    mysql_messages = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]

    with __import__("unittest.mock").mock.patch(
        "memory.short_term._load_from_mysql", return_value=mysql_messages
    ):
        restore_from_mysql_if_needed(r, "u1", "s1", db_session)

    r.delete.assert_not_called(), "Redis 数据完整时不应删除重建"
    r.pipeline.assert_not_called(), "Redis 数据完整时不应触发 pipeline 写入"


def test_restore_when_redis_has_partial_data():
    """Redis 消息数少于 MySQL（过期后部分重建）时，应清除残留并完整恢复"""
    r = MagicMock()
    # Redis 只有 2 条（最近一轮，之前过期了）
    r.lrange.return_value = [
        '{"role": "user", "content": "recent q"}',
        '{"role": "assistant", "content": "recent a"}',
    ]
    pipe = MagicMock()
    r.pipeline.return_value = pipe

    db_session = MagicMock()
    # MySQL 有 8 条（完整历史）
    mysql_messages = []
    for i in range(4):
        mysql_messages.append({"role": "user", "content": f"old q{i}"})
        mysql_messages.append({"role": "assistant", "content": f"old a{i}"})

    with __import__("unittest.mock").mock.patch(
        "memory.short_term._load_from_mysql", return_value=mysql_messages
    ):
        restore_from_mysql_if_needed(r, "u1", "s1", db_session)

    # 应先删除残留的 messages key，再通过 init_session 完整恢复
    assert r.delete.called, "应删除 Redis 中不完整的 messages"
    assert pipe.rpush.call_count == 8, "应通过 pipeline 写入全部 8 条 MySQL 消息"


def test_append_message_after_restore_preserves_history():
    """恢复后 append_message 应在历史消息之后追加，不覆盖"""
    r = MagicMock()
    r.lrange.return_value = []  # Redis 空

    pipe = MagicMock()
    r.pipeline.return_value = pipe

    db_session = MagicMock()
    mysql_messages = [
        {"role": "user", "content": "历史问题"},
        {"role": "assistant", "content": "历史回答"},
    ]

    with __import__("unittest.mock").mock.patch(
        "memory.short_term._load_from_mysql", return_value=mysql_messages
    ):
        restore_from_mysql_if_needed(r, "u1", "s1", db_session)

    # 模拟 restore 之后的 append_message（写入当前用户消息）
    append_message(r, "u1", "s1", "user", "今天的第一个问题")

    # pipeline rpush 恢复2条历史 + 直接 rpush 写1条当前 = 总共3次 rpush
    assert pipe.rpush.call_count == 2  # 2 条历史通过 pipeline
    assert r.rpush.call_count == 1  # 1 条当前消息直接 rpush
