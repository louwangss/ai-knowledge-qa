"""建表脚本

用法：
    python -m db.init_db          # 仅建表
    python -m db.init_db --drop   # 先删再建（清空所有数据）
"""
import sys
from db.database import engine, Base
from db.models import *  # noqa: F401,F403 - 确保所有模型被导入


INDEX_SQL = [
    "CREATE INDEX idx_chat_session ON chat_history(user_id, session_id, created_at)",
    "CREATE INDEX idx_chat_user_time ON chat_history(user_id, created_at)",
    "CREATE INDEX idx_episodic_dedup ON episodic_memory(user_id, event_type, created_at)",
    "CREATE INDEX idx_episodic_user_time ON episodic_memory(user_id, created_at)",
    "CREATE INDEX idx_semantic_chroma ON semantic_memory(chroma_id)",
    "CREATE INDEX idx_semantic_user ON semantic_memory(user_id)",
    "CREATE UNIQUE INDEX idx_doc_hash ON documents(user_id, content_hash)",
    "CREATE INDEX idx_session_user ON sessions(user_id, status, last_active)",
    "CREATE UNIQUE INDEX idx_session_summary ON session_summary(session_id)",
]


def init_db(drop=False):
    if drop:
        print("删除所有现有表...")
        Base.metadata.drop_all(engine)
        print("done")

    print("创建所有表...")
    Base.metadata.create_all(engine)
    print("done")

    print("创建索引...")
    with engine.connect() as conn:
        for sql in INDEX_SQL:
            try:
                conn.execute(__import__("sqlalchemy").text(sql))
            except Exception as e:
                # 索引已存在则跳过
                if "Duplicate" in str(e):
                    pass
                else:
                    print(f"  跳过: {sql[:60]}... ({e})")
        conn.commit()
    print("done")


if __name__ == "__main__":
    drop = "--drop" in sys.argv
    init_db(drop=drop)
