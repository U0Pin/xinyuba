package com.njuse.ea.data

import com.google.gson.JsonElement
import com.njuse.ea.ui.viewmodel.UserInfo

// ---- 画像接口 DTO（GET /users/{id}/portrait 等，2026-09 新增）----
//
// 三模块（act_metrics / personality / value_words）同构包装：{"status": ..., "value": ...}。
// status ∈ ready / pending / none；value 仅 ready 时有值，且不同模块结构不同
// （六维对象 / 枚举字符串 / 词数组）。为兼容后端结构演进，value 用 JsonElement
// 宽容承接，具体解析收敛到下方纯函数（可 JVM 单测），结构不符落到 Invalid 态由 UI 回退。

/** 三态模块原始包装：value 的具体类型随 status 变化，解析在纯函数里做 */
data class PortraitModule(
    val status: String = "none",
    val value: JsonElement? = null
)

data class PortraitResponse(
    val user_id: String = "",
    val act_metrics: PortraitModule? = null,   // 缺失(null) 视同 none
    val personality: PortraitModule? = null,
    val value_words: PortraitModule? = null
)

/** POST /users/{user_id}/portrait/personality/regenerate */
data class RegenerateResponse(
    val user_id: String = "",
    val status: String = ""
)

/** 归一化三态（可 JVM 单测）。Invalid = ready 但 value 结构不符，UI 按无数据回退 */
sealed class ModuleState<out T> {
    data class Ready<T>(val value: T) : ModuleState<T>()
    object Pending : ModuleState<Nothing>()
    object None : ModuleState<Nothing>()
    data class Invalid(val reason: String) : ModuleState<Nothing>()
}

/** ACT 六维，全部可空（后端缺键容忍） */
data class ActMetrics(
    val psychologicalFlexibility: Float? = null,
    val emotionalOpenness: Float? = null,
    val cognitiveReadiness: Float? = null,
    val cognitiveFusion: Float? = null,
    val experientialAvoidance: Float? = null,
    val valuesAlignment: Float? = null
)

/**
 * 把原始模块归一化到三态。null / "none" / 未知 status → None；
 * "pending" → Pending；"ready" → value 解析成功则 Ready 否则 Invalid。
 */
fun <T> parseModule(raw: PortraitModule?, parse: (JsonElement) -> T?): ModuleState<T> {
    if (raw == null) return ModuleState.None
    return when (raw.status) {
        "pending" -> ModuleState.Pending
        "ready" -> {
            val el = raw.value
            if (el == null) {
                ModuleState.Invalid("ready but value missing")
            } else {
                val parsed = runCatching { parse(el) }.getOrNull()
                if (parsed != null) ModuleState.Ready(parsed)
                else ModuleState.Invalid("ready but value unparseable")
            }
        }
        else -> ModuleState.None   // "none" 及未知 status 一律按没有处理
    }
}

/** 六维对象解析：逐键取浮点，缺键/类型不符容忍（字符串数字也接） */
fun parseActMetrics(el: JsonElement): ActMetrics? {
    val obj = el.asJsonObjectOrNull() ?: return null
    fun num(key: String): Float? {
        val v = obj.get(key) ?: return null
        return runCatching { v.asFloat }.getOrNull()
    }
    return ActMetrics(
        psychologicalFlexibility = num("psychological_flexibility"),
        emotionalOpenness = num("emotional_openness"),
        cognitiveReadiness = num("cognitive_readiness"),
        cognitiveFusion = num("cognitive_fusion"),
        experientialAvoidance = num("experiential_avoidance"),
        valuesAlignment = num("values_alignment")
    )
}

/** 人格枚举字符串解析 */
fun parsePersonalityKey(el: JsonElement): String? {
    return if (el.isJsonPrimitive && el.asJsonPrimitive.isString) el.asString else null
}

/** 价值词数组解析 */
fun parseValueWords(el: JsonElement): List<String>? {
    if (!el.isJsonArray) return null
    return el.asJsonArray.mapNotNull { item ->
        if (item.isJsonPrimitive && item.asJsonPrimitive.isString) item.asString else null
    }
}

/** 任一模块仍在生成中（驱动画像页轮询） */
fun PortraitResponse.hasPendingModules(): Boolean =
    listOf(act_metrics, personality, value_words).any { it?.status == "pending" }

/** 空画像 / 全模块 none 或缺失（诊断与边缘判断用） */
fun PortraitResponse.isAllNone(): Boolean =
    listOf(act_metrics, personality, value_words).all { it == null || it.status == "none" }

// ---- PUT /users/{user_id}/user-info ----

/**
 * 设置页四项用户信息（整份覆盖写；空值 = 未设置，「重置形象」= 全空）。
 * 响应体 schema 未声明，ApiService 返回 Response<Unit> 只看 code。
 */
data class UserInfoRequest(
    val nickname: String? = null,       // ≤12
    val gender: String? = null,         // "male" | "female" | null
    val age: Int? = null,               // 1..120 | null
    val tone_preference: String? = null // "gentle" | "lively" | null
)

/** 本地 UserInfo → 请求体：""/0 视为未设置 → null。纯函数，可单测 */
fun UserInfo.toRequest(): UserInfoRequest = UserInfoRequest(
    nickname = nickname.takeIf { it.isNotBlank() },
    gender = gender.takeIf { it.isNotBlank() },
    age = age.takeIf { it > 0 },
    tone_preference = tonePreference.takeIf { it.isNotBlank() }
)

private fun JsonElement.asJsonObjectOrNull() = if (isJsonObject) asJsonObject else null
