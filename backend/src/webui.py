"""测试用 WebUI（挂载到现有 server，同进程）。

- GET  /ui                           测试页面（src/webui/index.html）
- GET  /ui/api/overview              某 user 的 session 列表
- GET  /ui/api/session/{sid}/state   调度状态 / 编排结果 / 摘要 / 流水尾部
- GET  /ui/api/user/{uid}/profile    用户画像
- GET  /ui/api/session/{sid}/metrics 该 session 的聚合指标（增量消费 llm_calls.jsonl）
- GET  /ui/api/logs                  日志尾部（app.jsonl + llm_calls.jsonl）

指标（按 session 聚合，逐次增量扫描 llm_calls.jsonl）：
总 tokens / 缓存命中率 / 平均首 token 时间 / 平均总延迟 / 调用数 / 失败数 /
估算成本 / 按 agent 分布。
"""

import json
import os
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src.core.logging_utils import now_iso
from src.utils.config import config

_INDEX_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "index.html")


# ═══════════════════════════════════════════════════════════════════════════════
# 指标聚合器：增量消费 llm_calls.jsonl（append-only）
# ═══════════════════════════════════════════════════════════════════════════════

_EMPTY = {
    "prompt_tokens": 0, "completion_tokens": 0,
    "cache_hit_tokens": 0, "cache_miss_tokens": 0, "cache_reported": False,
    "calls": 0, "errors": 0,
    "ttft_sum_ms": 0.0, "ttft_count": 0,
    "latency_sum_ms": 0.0, "cost": 0.0,
}


class CallMetricsAggregator:
    def __init__(self, llm_calls_path: str | None = None):
        self.path = llm_calls_path or os.path.join(config.LOG_DIR, "llm_calls.jsonl")
        self._offset = 0
        self._lock = threading.Lock()
        self.by_session: dict[str, dict] = {}
        self.by_agent: dict[tuple[str, str], dict] = {}

    def _aggregate(self, key: str, bucket: dict, rec: dict) -> None:
        cur = bucket.setdefault(key, dict(_EMPTY))
        cur["prompt_tokens"] += int(rec.get("prompt_tokens") or 0)
        cur["completion_tokens"] += int(rec.get("completion_tokens") or 0)
        cur["cache_hit_tokens"] += int(rec.get("cache_hit_tokens") or 0)
        cur["cache_miss_tokens"] += int(rec.get("cache_miss_tokens") or 0)
        if (rec.get("cache_hit_tokens") or rec.get("cache_miss_tokens")):
            cur["cache_reported"] = True
        cur["calls"] += 1
        if not rec.get("success", True):
            cur["errors"] += 1
        cur["latency_sum_ms"] += float(rec.get("latency_ms") or 0)
        ttft = float(rec.get("ttft_ms") or 0)
        if ttft > 0:
            cur["ttft_sum_ms"] += ttft
            cur["ttft_count"] += 1
        cur["cost"] += float(rec.get("estimated_cost") or 0)

    def refresh(self) -> None:
        """消费自上次以来的新行（文件缩短时整体重置）。"""
        with self._lock:
            if not os.path.exists(self.path):
                return
            total_lines = 0
            with open(self.path, "r", encoding="utf-8") as f:
                for i, _ in enumerate(f, start=1):
                    total_lines = i
            if total_lines < self._offset:  # 文件被截断/轮转
                self._offset = 0
                self.by_session.clear()
                self.by_agent.clear()
            with open(self.path, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, start=1):
                    if i <= self._offset:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sid = rec.get("session_id") or ""
                    if sid:
                        self._aggregate(sid, self.by_session, rec)
                    agent = rec.get("agent") or ""
                    if sid and agent:
                        self._aggregate(f"{sid}|{agent}", self.by_agent, rec)
            self._offset = total_lines

    def session_metrics(self, session_id: str) -> dict:
        self.refresh()
        m = self.by_session.get(session_id, dict(_EMPTY))
        hit = m["cache_hit_tokens"]
        miss = m["cache_miss_tokens"]
        if not miss:
            miss = max(m["prompt_tokens"] - hit, 0)
        rate = None
        if m["cache_reported"] and (hit + miss) > 0:
            rate = hit / (hit + miss)
        return {
            "session_id": session_id,
            "total_tokens": m["prompt_tokens"] + m["completion_tokens"],
            "prompt_tokens": m["prompt_tokens"],
            "completion_tokens": m["completion_tokens"],
            "cache_hit_tokens": hit,
            "cache_miss_tokens": miss,
            "cache_hit_rate": None if rate is None else round(rate, 4),
            "avg_ttft_ms": round(m["ttft_sum_ms"] / m["ttft_count"], 1) if m["ttft_count"] else None,
            "avg_latency_ms": round(m["latency_sum_ms"] / m["calls"], 1) if m["calls"] else None,
            "calls": m["calls"],
            "errors": m["errors"],
            "estimated_cost_usd": round(m["cost"], 6),
            "by_agent": {
                k.split("|", 1)[1]: {
                    "calls": v["calls"],
                    "tokens": v["prompt_tokens"] + v["completion_tokens"],
                    "avg_ttft_ms": round(v["ttft_sum_ms"] / v["ttft_count"], 1) if v["ttft_count"] else None,
                    "errors": v["errors"],
                }
                for k, v in self.by_agent.items() if k.split("|", 1)[0] == session_id
            },
            "updated_at": now_iso(),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 日志尾部读取（app.jsonl + llm_calls.jsonl）
# ═══════════════════════════════════════════════════════════════════════════════

def _tail_jsonl(path: str, tail: int) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    start = max(0, len(lines) - tail)
    for i in range(start, len(lines)):
        try:
            data = json.loads(lines[i])
        except json.JSONDecodeError:
            data = {"raw": lines[i].strip()}
        out.append({"line": i + 1, "data": data})
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════════════════════════

def build_webui_router(scheduler) -> APIRouter:
    router = APIRouter(prefix="/ui", tags=["webui"])
    metrics = CallMetricsAggregator()

    @router.get("")
    async def index():
        # no-store：测试页面频繁更新，禁止浏览器/中间层缓存旧版本
        return FileResponse(_INDEX_HTML, headers={"Cache-Control": "no-store"})

    @router.get("/api/overview")
    async def overview(user_id: str):
        sessions = scheduler.sessions.list_user_sessions(user_id)
        for s in sessions:
            sid = s["session_id"]
            state = scheduler.sessions.read_state(sid)
            s["owner"] = state.get("owner")
            s["therapy"] = (state.get("therapy") or {}).get("name")
            s["risk_level"] = state.get("risk_level")
            s["crisis"] = state.get("crisis")
            s["flow_lines"] = len(scheduler.sessions.read_flow_tail(sid, 100000))
        return {"user_id": user_id, "sessions": sessions}

    @router.get("/api/users")
    async def users():
        return {"users": scheduler.sessions.list_users()}

    @router.post("/api/user")
    async def create_user(payload: dict):
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id required")
        scheduler.sessions.ensure_user(user_id)
        return {"user_id": user_id}

    @router.get("/api/session/{sid}/state")
    async def session_state(sid: str):
        if not scheduler.sessions.exists(sid):
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "state": scheduler.sessions.read_state(sid),
            "orchestration": scheduler.sessions.read_orchestration(sid),
            "summary": scheduler.sessions.read_summary(sid),
            "flow_tail": scheduler.sessions.read_flow_tail(sid, 50),
        }

    @router.get("/api/user/{uid}/profile")
    async def user_profile(uid: str):
        return {"user_id": uid, "profile": scheduler.profiles.get(uid)}

    @router.get("/api/session/{sid}/metrics")
    async def session_metrics(sid: str):
        if not scheduler.sessions.exists(sid):
            raise HTTPException(status_code=404, detail="session not found")
        return metrics.session_metrics(sid)

    @router.get("/api/logs")
    async def logs(tail: int = 400):
        return {
            "app": _tail_jsonl(os.path.join(config.LOG_DIR, "app.jsonl"), tail),
            "llm": _tail_jsonl(os.path.join(config.LOG_DIR, "llm_calls.jsonl"), tail),
            "ts": now_iso(),
        }

    return router
