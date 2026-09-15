"""多 session 存盘层（新架构，Phase 1）。

文件布局：

    data/users/{user_id}/index.json        # 该 user 的 session 列表（调度器写）
    data/users/{user_id}/profile.json      # 画像（沉淀线写；由 ProfileStore 管理）
    data/sessions/{sid}/flow.jsonl         # 对话流水，追加式（对话线写）
    data/sessions/{sid}/flow.jsonl.meta    # 流水序号计数（对话线写）
    data/sessions/{sid}/state.json         # 调度状态（决策线/调度器写）
    data/sessions/{sid}/orchestration.json # 最新编排结果（决策线写）
    data/sessions/{sid}/summary.json       # 结构化摘要（沉淀线写）

单写者纪律由调用方保证；本层只提供唯一的文件读写通道，
以及只读的流水查询辅助（如 current_therapy_transcript）。
"""

import json
import os
import uuid
from typing import Optional

from src.core.logging_utils import now_iso
from src.utils.config import config


def _atomic_write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


DEFAULT_STATE = {"owner": "daily", "crisis": False, "risk_level": "SAFE", "therapy": None}
DEFAULT_SUMMARY = {"updated_seq": 0, "token_estimate": 0, "summary": []}
DEFAULT_ORCHESTRATION = {}


def current_therapy_transcript(session_store: "SessionStore", session_id: str) -> list[dict]:
    """从流水末尾向前收集连续 owner 以 therapy_ 开头的条目，反转为时间序。

    接管标识 = 第一条 owner 为 therapy_* 的条目；结束 = 之后出现非 therapy_*
    条目。无进行中疗法时返回空列表。
    """
    run: list[dict] = []
    for entry in reversed(session_store.read_flow_all(session_id)):
        if str(entry.get("owner", "")).startswith("therapy_"):
            run.append(entry)
        else:
            break
    run.reverse()
    return run


class SessionStore:
    """多 session 管理 + 各存盘产物的读写通道。"""

    def __init__(self, data_root: Optional[str] = None):
        self.root = data_root or config.DATA_ROOT

    # ── 路径 ────────────────────────────────────────────────────────

    def _session_dir(self, sid: str) -> str:
        return os.path.join(self.root, "sessions", sid)

    def _user_dir(self, uid: str) -> str:
        return os.path.join(self.root, "users", uid)

    def _path(self, sid: str, name: str) -> str:
        return os.path.join(self._session_dir(sid), name)

    def _flow_path(self, sid: str) -> str:
        return self._path(sid, "flow.jsonl")

    def _flow_meta_path(self, sid: str) -> str:
        return self._path(sid, "flow.jsonl.meta")

    def _state_path(self, sid: str) -> str:
        return self._path(sid, "state.json")

    def _orchestration_path(self, sid: str) -> str:
        return self._path(sid, "orchestration.json")

    def _summary_path(self, sid: str) -> str:
        return self._path(sid, "summary.json")

    def _index_path(self, uid: str) -> str:
        return os.path.join(self._user_dir(uid), "index.json")

    # ── session 生命周期 ───────────────────────────────────────────

    def create(self, user_id: str) -> str:
        """创建新 session（含初始 state/summary/orchestration/流水与 user 索引）。"""
        sid = uuid.uuid4().hex[:16]
        os.makedirs(self._session_dir(sid), exist_ok=True)
        _atomic_write_json(self._state_path(sid), dict(DEFAULT_STATE))
        _atomic_write_json(self._summary_path(sid), dict(DEFAULT_SUMMARY))
        _atomic_write_json(self._orchestration_path(sid), dict(DEFAULT_ORCHESTRATION))
        open(self._flow_path(sid), "a", encoding="utf-8").close()
        _atomic_write_json(self._flow_meta_path(sid), {"next_seq": 1})

        index = _read_json(self._index_path(user_id), {"sessions": []})
        sessions = index.get("sessions", [])
        sessions.append({
            "session_id": sid,
            "created_at": now_iso(),
            "default": len(sessions) == 0,
        })
        _atomic_write_json(self._index_path(user_id), {"sessions": sessions})
        return sid

    def exists(self, sid: str) -> bool:
        return os.path.exists(self._flow_path(sid))

    def list_users(self) -> list[str]:
        """列出存盘中已有的全部 user_id（users/ 下的子目录名）。"""
        users_dir = os.path.join(self.root, "users")
        if not os.path.isdir(users_dir):
            return []
        return sorted(
            name for name in os.listdir(users_dir)
            if not name.startswith(".") and os.path.isdir(os.path.join(users_dir, name))
        )

    def ensure_user(self, user_id: str) -> None:
        """确保 user 存在（写空 session 索引；webui 新建 user 用）。"""
        if not os.path.exists(self._index_path(user_id)):
            _atomic_write_json(self._index_path(user_id), {"sessions": []})

    def list_user_sessions(self, user_id: str) -> list[dict]:
        index = _read_json(self._index_path(user_id), {"sessions": []})
        return list(index.get("sessions", []))

    # ── 对话流水（对话线专用） ─────────────────────────────────────

    def append_flow(self, sid: str, entry: dict) -> dict:
        """追加一条流水（自动分配 seq 与时间戳；调用方提供 role/owner/text 等）。"""
        meta = _read_json(self._flow_meta_path(sid), {"next_seq": 1})
        seq = int(meta.get("next_seq", 1))
        record = {"seq": seq, "ts": now_iso(), **entry}
        with open(self._flow_path(sid), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        _atomic_write_json(self._flow_meta_path(sid), {"next_seq": seq + 1})
        return record

    def read_flow_tail(self, sid: str, n: int) -> list[dict]:
        """读最近 n 条（按时间序）。"""
        if n <= 0 or not os.path.exists(self._flow_path(sid)):
            return []
        with open(self._flow_path(sid), "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(line) for line in lines]

    def read_flow_range(self, sid: str, start_seq: Optional[int] = None, end_seq: Optional[int] = None) -> list[dict]:
        """读 [start_seq, end_seq] 区间（含端点，None 表示不限）。流水规模下线性扫描即可。"""
        if not os.path.exists(self._flow_path(sid)):
            return []
        out = []
        with open(self._flow_path(sid), "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seq = entry.get("seq")
                if start_seq is not None and seq < start_seq:
                    continue
                if end_seq is not None and seq > end_seq:
                    break
                out.append(entry)
        return out

    def read_flow_all(self, sid: str) -> list[dict]:
        return self.read_flow_range(sid)

    def next_flow_seq(self, sid: str) -> int:
        """读下一个流水序号（只读 meta；供决策线计算疗程起始序号）。"""
        meta = _read_json(self._flow_meta_path(sid), {"next_seq": 1})
        return int(meta.get("next_seq", 1))

    # ── state / orchestration / summary ────────────────────────────

    def read_state(self, sid: str) -> dict:
        return _read_json(self._state_path(sid), dict(DEFAULT_STATE))

    def write_state(self, sid: str, data: dict) -> None:
        _atomic_write_json(self._state_path(sid), data)

    def read_orchestration(self, sid: str) -> dict:
        return _read_json(self._orchestration_path(sid), dict(DEFAULT_ORCHESTRATION))

    def write_orchestration(self, sid: str, data: dict) -> None:
        _atomic_write_json(self._orchestration_path(sid), data)

    def read_summary(self, sid: str) -> dict:
        return _read_json(self._summary_path(sid), dict(DEFAULT_SUMMARY))

    def write_summary(self, sid: str, data: dict) -> None:
        _atomic_write_json(self._summary_path(sid), data)

    # ── 重置 ───────────────────────────────────────────────────────

    def reset(self, sid: str) -> None:
        """清空该 session 的上下文产物（user 画像不动）。"""
        _atomic_write_json(self._state_path(sid), dict(DEFAULT_STATE))
        _atomic_write_json(self._summary_path(sid), dict(DEFAULT_SUMMARY))
        _atomic_write_json(self._orchestration_path(sid), dict(DEFAULT_ORCHESTRATION))
        open(self._flow_path(sid), "w", encoding="utf-8").close()
        _atomic_write_json(self._flow_meta_path(sid), {"next_seq": 1})
