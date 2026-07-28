"""笔记内容版本：用于自动保存的乐观并发控制。"""

import hashlib


def build_note_version(concept: str | None, content: str) -> str:
    """根据完整标题和正文生成稳定版本，不持久化派生状态。"""
    payload = f"{concept or ''}\0{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
