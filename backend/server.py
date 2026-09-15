"""新架构 HTTP 入口（SSE 流式）。

端点（v2 沉淀稿第十四节，作者已定稿）：
- POST /sessions                   {user_id} → {session_id}
- GET  /users/{user_id}/sessions   列出该 user 全部 session 元数据
- POST /chat                       {user_id, session_id?, message} → SSE 流
- GET  /sessions/{session_id}/history  分页历史（倒序，最新在前）
- GET  /users/{user_id}/profile    user 级画像
- POST /sessions/{session_id}/reset    清空 session 上下文（保留画像）
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.bootstrap import build_scheduler
from src.core.scheduler import Scheduler

# 兼容旧导入路径（tests/test_server.py 等）：build_scheduler 现居于 src/bootstrap.py
__all__ = ["build_app", "build_scheduler"]


# ── 请求/响应模型 ──────────────────────────────────────────────────


class SessionCreateRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")


class ChatRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一标识")
    session_id: Optional[str] = Field(None, description="会话标识；缺省使用该用户的默认 session")
    message: str = Field(..., description="用户输入的消息内容")


class ResetRequest(BaseModel):
    session_id: str = Field(..., description="会话标识")


class UserInfoRequest(BaseModel):
    """设置页四项用户信息（整份覆盖写；空值 = 未设置，「重置形象」= 全空）。"""

    nickname: Optional[str] = Field(None, max_length=12, description="昵称，≤12 字符")
    gender: Optional[Literal["male", "female"]] = Field(None, description="性别")
    age: Optional[int] = Field(None, ge=1, le=120, description="年龄，1~120")
    tone_preference: Optional[Literal["gentle", "lively"]] = Field(
        None, description="语气偏好：gentle 温和 / lively 活泼"
    )


# 「重新生成报告」频控：每用户 10s 一次
PERSONALITY_REGEN_INTERVAL_SECONDS = 10.0


def _parse_iso(ts) -> datetime | None:
    """宽松解析 ISO 时间戳；失败返回 None。"""
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


# ── 应用组装 ──────────────────────────────────────────────────────


def build_app(scheduler: Scheduler | None = None) -> FastAPI:
    scheduler = scheduler or build_scheduler()

    app = FastAPI(
        title="Emotion Support Agent API (v2)",
        description="情绪疏导 Agent：三线并行新架构",
        version="0.2.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 测试用 WebUI（/ui）
    from src.webui import build_webui_router
    app.include_router(build_webui_router(scheduler))

    def _default_session_id(user_id: str) -> str:
        sessions = scheduler.sessions.list_user_sessions(user_id)
        if sessions:
            return sessions[0]["session_id"]
        return scheduler.sessions.create(user_id)

    @app.post("/sessions")
    async def create_session(req: SessionCreateRequest):
        sid = scheduler.sessions.create(req.user_id)
        return {"user_id": req.user_id, "session_id": sid}

    @app.get("/users/{user_id}/sessions")
    async def list_sessions(user_id: str):
        return {
            "user_id": user_id,
            "sessions": scheduler.sessions.list_user_sessions(user_id),
        }

    @app.post("/chat")
    async def chat(req: ChatRequest):
        sid = req.session_id or _default_session_id(req.user_id)
        if not scheduler.sessions.exists(sid):
            raise HTTPException(status_code=404, detail=f"session {sid} not found")

        async def sse():
            async for event in scheduler.handle_message(req.user_id, sid, req.message):
                payload = json.dumps(event.get("data", {}), ensure_ascii=False)
                yield f"event: {event['event']}\ndata: {payload}\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    @app.get("/sessions/{session_id}/history")
    async def history(
        session_id: str,
        page: int = Query(1, ge=1, description="页码，从 1 开始"),
        page_size: int = Query(20, ge=1, le=100, description="每页条数，最大 100"),
    ):
        if not scheduler.sessions.exists(session_id):
            raise HTTPException(status_code=404, detail=f"session {session_id} not found")
        flow = scheduler.sessions.read_flow_all(session_id)
        total = len(flow)
        total_pages = max(1, (total + page_size - 1) // page_size)
        reversed_flow = list(reversed(flow))  # 倒序：最新在前
        start = (page - 1) * page_size
        items = [
            {
                "seq": e.get("seq"), # 流水序号
                "role": e.get("role"), # 角色：user / agent
                "owner": e.get("owner"), # 接管 Agent：Daily / CBT / ...
                "text": e.get("text", ""), # 正文
                "ts": e.get("ts", ""), # 时间戳（ISO格式）
            }
            for e in reversed_flow[start:start + page_size]
        ]
        return {
            "session_id": session_id,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "items": items,
        }

    @app.get("/users/{user_id}/profile")
    async def profile(user_id: str):
        return {"user_id": user_id, "profile": scheduler.profiles.get(user_id)}

    @app.get("/users/{user_id}/companion")
    async def companion(user_id: str):
        """陪伴统计（供 Android「成长轨迹」卡片）：陪伴时长 / 猫猫发言数 / 相识天数。

        陪伴时长估算：逐条流水计算相邻时间间隔，单段间隔超过 5 分钟按 5 分钟
        计（视为用户已离开），避免长时间挂机污染数据。
        """
        GAP_CAP_SECONDS = 300
        sessions = scheduler.sessions.list_user_sessions(user_id)
        agent_messages = 0
        user_messages = 0
        active_seconds = 0.0
        first_met: datetime | None = None

        for meta in sessions:
            created = _parse_iso(meta.get("created_at"))
            if created and (first_met is None or created < first_met):
                first_met = created
            prev_ts = None
            for e in scheduler.sessions.read_flow_all(meta["session_id"]):
                role = e.get("role")
                if role == "agent":
                    agent_messages += 1
                elif role == "user":
                    user_messages += 1
                ts = _parse_iso(e.get("ts"))
                if ts is None:
                    continue
                if prev_ts is not None:
                    active_seconds += min((ts - prev_ts).total_seconds(), GAP_CAP_SECONDS)
                prev_ts = ts

        if first_met is not None:
            now = datetime.now(first_met.tzinfo)
            days_together = (now.date() - first_met.date()).days + 1
        else:
            days_together = 0

        return {
            "user_id": user_id,
            "companionship_seconds": int(active_seconds),
            "agent_message_count": agent_messages,
            "user_message_count": user_messages,
            "days_together": days_together,
            "first_met_at": first_met.isoformat() if first_met else None,
        }

    @app.post("/sessions/{session_id}/reset")
    async def reset(session_id: str):
        if not scheduler.sessions.exists(session_id):
            raise HTTPException(status_code=404, detail=f"session {session_id} not found")
        scheduler.sessions.reset(session_id)
        return {"session_id": session_id, "status": "reset_done"}

    # ── 画像页 / 设置页 ─────────────────────────────────────────────

    @app.get("/users/{user_id}/portrait")
    async def portrait(user_id: str):
        """画像页数据：ACT 六维（一组）、人格（一枚举）、价值词（一组）。

        每模块三态：ready（有值，价值词空列表也算 ready——算完但没提取到）/
        pending（正在生成，稍后重拉）/ none（确定没有，前端展示空态引导）；
        全部 none 时返回空对象。
        """
        data = scheduler.portraits.get(user_id)
        computing = user_id in scheduler.settlement_line.portrait_pending
        # 价值词以 portrait.json 为准（含空列表）；兼容旧数据回退画像 personal_values
        if "value_words" in data:
            value_words = data["value_words"]
        else:
            value_words = scheduler.profiles.get(user_id).get("personal_values") or None

        def module(value, key):
            if value:
                return {"status": "ready", key: value}
            if isinstance(value, list):  # 空列表 = 已算完、没提取到
                return {"status": "ready", key: value}
            return {"status": "pending"} if computing else {"status": "none"}

        modules = {
            "act_metrics": module(data.get("act_metrics"), "values"),
            "personality": module(data.get("personality"), "value"),
            "value_words": module(value_words, "values"),
        }
        if all(m["status"] == "none" for m in modules.values()):
            return {}
        return {"user_id": user_id, **modules}

    _last_personality_regen: dict[str, float] = {}

    @app.post("/users/{user_id}/portrait/personality/regenerate")
    async def regenerate_personality(user_id: str):
        """「重新生成报告」：只重判人格类型，结果经 GET portrait 可见。

        频控：每用户 10s 一次。无需画像积累（无画像时基于最近对话保守判定）。
        """
        now = time.monotonic()
        last = _last_personality_regen.get(user_id, 0.0)
        if now - last < PERSONALITY_REGEN_INTERVAL_SECONDS:
            raise HTTPException(status_code=429, detail="too frequent, retry later")
        _last_personality_regen[user_id] = now
        asyncio.create_task(scheduler.settlement_line.regenerate_personality(user_id))
        return {"user_id": user_id, "status": "pending"}

    @app.put("/users/{user_id}/user-info")
    async def put_user_info(user_id: str, req: UserInfoRequest):
        """设置页用户信息同步（客户端只写不读；空值剥离后整份覆盖存盘）。"""
        user_info = {k: v for k, v in req.model_dump().items() if v is not None}
        scheduler.user_infos.save(user_id, user_info)
        return {"user_id": user_id, "user_info": user_info}

    return app


app = build_app()
