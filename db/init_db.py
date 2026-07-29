"""数据库初始化与 additive upgrade 脚本

用法：
    python -m db.init_db             # 安全创建/升级缺失结构
    python -m db.init_db --upgrade   # 同上，供部署脚本显式调用
    python -m db.init_db --drop      # 先删再建（会清空所有数据）
"""
import argparse
from db.database import engine, Base
from db.models import *  # noqa: F401,F403 - 确保所有模型被导入
from db.schema import INDEX_SQL, upgrade_schema


def init_db(drop=False):
    if drop:
        print("删除所有现有表...")
        Base.metadata.drop_all(engine)
        print("done")

    print("创建或升级数据库结构...")
    upgrade_schema(engine)
    print("done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化或升级数据库结构")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--upgrade",
        action="store_true",
        help="补齐缺失表、列、索引和外键，不改写权威业务数据",
    )
    action.add_argument("--drop", action="store_true", help="删除全部表后重建（会清空所有数据）")
    args = parser.parse_args()
    init_db(drop=args.drop)
