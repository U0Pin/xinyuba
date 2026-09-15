"""user 级用户信息存盘层：设置页收集的 nickname / gender / age / tone_preference。

由设置页同步端点（PUT）独占写入（整份覆盖，空值 = 未设置），
调度器只读——合并进画像供 Host 系统提示词使用（Agent 对话可见）。
与沉淀线独占写的 profile.json 分离，避免写竞态。
文件：data/users/{user_id}/user_info.json
"""

import os
from typing import Optional

from src.store.session_store import _atomic_write_json, _read_json
from src.utils.config import config


class UserInfoStore:
    def __init__(self, data_root: Optional[str] = None):
        self.root = data_root or config.DATA_ROOT

    def _path(self, user_id: str) -> str:
        return os.path.join(self.root, "users", user_id, "user_info.json")

    def get(self, user_id: str) -> dict:
        """读用户信息；不存在时返回空 dict（四项均未设置）。"""
        return _read_json(self._path(user_id), {})

    def save(self, user_id: str, user_info: dict) -> None:
        """整份覆盖保存（重置 = 客户端 PUT 全空值，调用方先剥离空值）。"""
        _atomic_write_json(self._path(user_id), user_info)
