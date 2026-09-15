"""user 级画像页评估结果存盘层：ACT 六维 + 人格类型。

由沉淀线独占写入（每轮对话后重估 + 「重新生成报告」触发的人格重判），
画像页查询端点只读。
文件：data/users/{user_id}/portrait.json
"""

import os
from typing import Optional

from src.store.session_store import _atomic_write_json, _read_json
from src.utils.config import config


class PortraitStore:
    def __init__(self, data_root: Optional[str] = None):
        self.root = data_root or config.DATA_ROOT

    def _path(self, user_id: str) -> str:
        return os.path.join(self.root, "users", user_id, "portrait.json")

    def get(self, user_id: str) -> dict:
        """读评估结果；不存在时返回空 dict（画像页展示空态/生成中）。"""
        return _read_json(self._path(user_id), {})

    def save(self, user_id: str, portrait: dict) -> None:
        _atomic_write_json(self._path(user_id), portrait)
