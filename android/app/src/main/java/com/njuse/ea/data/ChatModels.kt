package com.njuse.ea.data

// ---- API DTOs（对齐后端 Emotion Support Agent API v0.2.0「三线并行」架构）----

// POST /chat
data class ChatRequest(
    val user_id: String,
    val message: String,
    // 会话标识；缺省（null）时后端自动使用/创建该用户的默认 session
    val session_id: String? = null
)

// 陪伴统计（GET /users/{user_id}/companion）—— 成长轨迹卡片数据源
data class CompanionResponse(
    val user_id: String = "",
    val companionship_seconds: Long = 0,
    val agent_message_count: Int = 0,
    val user_message_count: Int = 0,
    val days_together: Int = 0,
    val first_met_at: String? = null
)

// POST /sessions
data class SessionCreateRequest(
    val user_id: String
)

data class SessionCreateResponse(
    val user_id: String = "",
    val session_id: String = ""
)

// GET /users/{user_id}/sessions
data class SessionInfo(
    val session_id: String,
    val created_at: String? = null,
    val default: Boolean = false
)

data class SessionListResponse(
    val user_id: String = "",
    val sessions: List<SessionInfo> = emptyList()
)

// POST /sessions/{session_id}/reset
data class ResetResponse(
    val session_id: String = "",
    val status: String = ""
)

// GET /sessions/{session_id}/history
data class HistoryItem(
    val seq: Int = 0,
    val role: String = "",      // "user" | "agent"
    val owner: String? = null,  // 三线归属：daily / ...（后端诊断线索，UI 暂不展示）
    val text: String = "",
    val ts: String = ""         // ISO 本地时间（北京时间），带 +08:00
)

data class HistoryResponse(
    val session_id: String = "",
    val total: Int = 0,
    val page: Int = 0,
    val page_size: Int = 0,
    val total_pages: Int = 0,
    val items: List<HistoryItem> = emptyList()
)

// GET /users/{user_id}/profile
// 画像为后端异步增量构建（多轮对话后才有值），字段全部可空。
data class ProfileData(
    val attachment_style: String? = null,       // anxious / avoidant / secure ...
    val rejection_sensitivity: String? = null,  // high / medium / low
    val core_fears: List<String>? = null,
    val personal_values: List<String>? = null,
    val distress_tolerance: Float? = null
)

data class ProfileResponse(
    val user_id: String = "",
    val profile: ProfileData = ProfileData()
)
