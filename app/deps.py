"""依赖注入：DB session、Redis 连接"""
from db.database import get_db
from memory.short_term import get_redis
