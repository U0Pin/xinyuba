"""user 级画像存盘层（新架构，Phase 1）。

画像 user 级共享、跨 session；由沉淀线独占写入（增量更新），其余线只读。
文件：data/users/{user_id}/profile.json
"""

import os
from typing import Optional

from src.store.session_store import _atomic_write_json, _read_json
from src.utils.config import config


class ProfileStore:
    def __init__(self, data_root: Optional[str] = None):
        self.root = data_root or config.DATA_ROOT

    def _path(self, user_id: str) -> str:
        return os.path.join(self.root, "users", user_id, "profile.json")

    def get(self, user_id: str) -> dict:
        """读画像；不存在时返回空 dict（字段语义由沉淀线定义）。"""
        return _read_json(self._path(user_id), {})

    def save(self, user_id: str, profile: dict) -> None:
        """整份保存（合并逻辑归沉淀线，本层只负责读写）。"""
        _atomic_write_json(self._path(user_id), profile)
