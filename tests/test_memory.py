"""记忆模块测试：episodic 事件去重逻辑"""
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
