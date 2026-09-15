package com.njuse.ea.data

/**
 * UI 侧的画像聚合模型。由 [ChatRepository.getStatus] 合并
 * GET /users/{id}/profile 与 GET /users/{id}/companion 两个端点拼成，
 * 字段名保留给 ProfileScreen / ProfileViewModel 使用。
 */
data class StatusResponse(
    val user_id: String = "",
    val session_exists: Boolean = false,
    val turn_count: Int = 0,
    val risk_state: String = "",
    val conversation_count: Int = 0,
    val attachment_style: String? = null,
    val core_fears: List<String> = emptyList(),
    val personal_values: List<String> = emptyList(),
    // 后端 rejection_sensitivity 是 high/medium/low 枚举；这里同时给出
    // 归一化到 0..1 的数值（供人格判定阈值）和原始标签。
    val rejection_sensitivity: Float? = null,
    val rejection_sensitivity_label: String? = null,
    val distress_tolerance: Float? = null
)

/**
 * 后端是否已为该用户构建出画像（任一核心字段有值即算有）。
 * 全空 = 用户聊得还不够、画像尚未生成，UI 应展示「暂无数据」空态
 * 而不是编造样例数据。纯函数，可 JVM 单测。
 */
fun StatusResponse.hasProfileData(): Boolean =
    attachment_style != null ||
        rejection_sensitivity != null ||
        rejection_sensitivity_label != null ||
        distress_tolerance != null ||
        core_fears.isNotEmpty() ||
        personal_values.isNotEmpty()
