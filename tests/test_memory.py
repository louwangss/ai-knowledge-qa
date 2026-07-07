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
