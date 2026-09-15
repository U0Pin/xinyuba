package com.njuse.ea.data

import com.google.gson.Gson
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

data class ChatMessage(
    val role: Role,
    val text: String,
    val timestamp: Long
) {
    enum class Role { AGENT, USER }

    fun timeLabel(): String {
        val time = SimpleDateFormat("M.dd HH:mm", Locale.getDefault()).format(Date(timestamp))
        return when (role) {
            Role.AGENT -> "$time ( =•ω•= )"
            Role.USER  -> "$time 人(`･ω･′)"
        }
    }
}

private val gson = Gson()

fun List<ChatMessage>.toJson(): String = gson.toJson(this)

fun parseChatHistory(json: String?): List<ChatMessage> {
    if (json.isNullOrBlank()) return emptyList()
    return try {
        gson.fromJson(json, Array<ChatMessage>::class.java)?.toList() ?: emptyList()
    } catch (e: Exception) {
        emptyList()
    }
}

private fun parseIsoTimestamp(ts: String): Long {
    if (ts.isBlank()) return System.currentTimeMillis()
    // 后端返回带时区偏移的 ISO8601（如 2026-09-02T09:30:57.949240+08:00）。
    // 微秒精度（6 位）超出 SimpleDateFormat 的毫秒（3 位）支持，先截断小数秒到 3 位，
    // 并按字符串里的偏移量显式设定时区解析，得到与 System.currentTimeMillis() 一致的 epoch ms，
    // 再由 timeLabel() 用本地时区格式化展示。
    return try {
        var s = ts
        var tz: TimeZone? = null
        // 提取尾部时区：Z 或 ±HH:MM
        when {
            s.endsWith("Z") -> {
                tz = TimeZone.getTimeZone("UTC")
                s = s.dropLast(1)
            }
            s.length >= 6 && (s[s.length - 6] == '+' || s[s.length - 6] == '-') -> {
                val offset = s.substring(s.length - 6)          // e.g. +08:00
                tz = TimeZone.getTimeZone("GMT$offset")
                s = s.substring(0, s.length - 6)
            }
        }
        // 截断小数秒到毫秒（3 位）
        val dot = s.indexOf('.')
        if (dot >= 0) {
            val frac = s.substring(dot + 1)
            s = s.substring(0, dot) + "." + frac.take(3)
        }
        val fmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS", Locale.US)
        if (tz != null) fmt.timeZone = tz
        fmt.parse(s)?.time ?: System.currentTimeMillis()
    } catch (e: Exception) {
        System.currentTimeMillis()
    }
}

/** 单条历史消息 → ChatMessage；role 为 "user"/"agent"，其余按 agent 兜底。 */
fun HistoryItem.toChatMessage(): ChatMessage {
    val role = if (role.equals("user", ignoreCase = true))
        ChatMessage.Role.USER else ChatMessage.Role.AGENT
    return ChatMessage(role, text, parseIsoTimestamp(ts))
}