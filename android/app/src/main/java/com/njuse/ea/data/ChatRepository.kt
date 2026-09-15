package com.njuse.ea.data

import android.util.Log
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.coroutineContext

/** SSE 流式 chat 的最终结果。 */
data class ChatStreamResult(
    val text: String,
    val riskState: String,
    val crisis: Boolean
)

class ChatRepository {

    private val gson = Gson()

    // 普通 JSON 端点用：带 BODY 日志
    private val okHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .addInterceptor(HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BODY
        })
        .build()

    // SSE chat 用：不挂 BODY 日志拦截器（它会缓冲整个响应体，破坏逐 token 流式）。
    private val streamClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .build()

    private val retrofit = Retrofit.Builder()
        .baseUrl(BASE_URL)
        .client(okHttpClient)
        .addConverterFactory(GsonConverterFactory.create())
        .build()

    private val api = retrofit.create(ApiService::class.java)

    /** 进程内缓存 user_id -> 默认 session_id，避免每次都先 list。 */
    private val sessionCache = HashMap<String, String>()

    /**
     * 解析用户的默认会话 id：优先用缓存；否则列出会话取 default/第一个；
     * 都没有则新建一个。
     */
    private suspend fun resolveSessionId(userId: String): String {
        sessionCache[userId]?.let { return it }
        return try {
            val list = api.listSessions(userId)
            val sid = list.sessions.firstOrNull { it.default }?.session_id
                ?: list.sessions.firstOrNull()?.session_id
                ?: api.createSession(SessionCreateRequest(userId)).session_id
            if (sid.isNotBlank()) sessionCache[userId] = sid
            sid
        } catch (e: Exception) {
            Log.e(TAG, "resolveSessionId failed", e)
            // 退回：让后端在 /chat 时隐式使用默认会话（session_id 留空）
            ""
        }
    }

    /**
     * POST /chat —— SSE 流式。逐 token 通过 [onToken] 回传（运行在 IO 调度器），
     * 流结束后以 [Result] 返回完整文本与风险标志；失败返回 [Result.failure]。
     * 不传 session_id 时后端会自动使用/创建默认会话，因此即便会话解析失败也能聊。
     */
    suspend fun streamChat(
        userId: String,
        message: String,
        onToken: (delta: String) -> Unit
    ): Result<ChatStreamResult> = withContext(Dispatchers.IO) {
        val payload = gson.toJson(ChatRequest(user_id = userId, message = message))
            .toRequestBody(JSON_MEDIA)
        val request = Request.Builder()
            .url(BASE_URL + "chat")
            .post(payload)
            // SSE：声明事件流、禁用响应压缩（gzip 会让代理/客户端攒批）、不缓存，
            // 保证后端每生成一个 token 就能立刻到达、逐字上屏。
            .header("Accept", "text/event-stream")
            .header("Accept-Encoding", "identity")
            .header("Cache-Control", "no-cache")
            .build()
        Log.d(TAG, "POST /chat user_id=$userId message=$message")
        try {
            streamClient.newCall(request).execute().use { resp ->
                if (!resp.isSuccessful) {
                    throw IOException("HTTP ${resp.code}")
                }
                val source = resp.body?.source()
                    ?: throw IOException("Empty response body")

                var event = ""
                val fullText = StringBuilder()
                var riskState = ""
                var crisis = false

                while (true) {
                    coroutineContext.ensureActive()
                    val line = source.readUtf8Line() ?: break
                    when {
                        line.startsWith("event:") ->
                            event = line.removePrefix("event:").trim()
                        line.startsWith("data:") -> {
                            val data = line.removePrefix("data:").trim()
                            when (event) {
                                "token" -> {
                                    // data 是一个 JSON 字符串字面量（带引号），解码出增量文本
                                    val delta = runCatching {
                                        gson.fromJson(data, String::class.java)
                                    }.getOrNull() ?: data.trim('"')
                                    fullText.append(delta)
                                    onToken(delta)
                                }
                                "final" -> {
                                    runCatching {
                                        JsonParser.parseString(data).asJsonObject
                                    }.getOrNull()?.let { obj: JsonObject ->
                                        riskState = obj.get("risk_state")?.takeUnless { it.isJsonNull }?.asString ?: ""
                                        crisis = obj.get("crisis")?.takeUnless { it.isJsonNull }?.asBoolean ?: false
                                    }
                                }
                                else -> { /* dialogue_done 等事件目前无需处理 */ }
                            }
                            event = ""
                        }
                    }
                }

                val text = fullText.toString()
                Log.d(TAG, "POST /chat → done risk=$riskState crisis=$crisis len=${text.length}")
                Result.success(ChatStreamResult(text, riskState, crisis))
            }
        } catch (e: Exception) {
            Log.e(TAG, "POST /chat failed", e)
            Result.failure(e)
        }
    }

    suspend fun resetSession(userId: String): Result<Unit> {
        Log.d(TAG, "resetSession user_id=$userId")
        return try {
            val sid = resolveSessionId(userId)
            if (sid.isBlank()) {
                // 无会话可重置（用户从未聊过）—— 视为成功
                Log.d(TAG, "resetSession: no session, skip")
                return Result.success(Unit)
            }
            api.resetSession(sid)
            sessionCache.remove(userId)
            Log.d(TAG, "POST /sessions/$sid/reset → done")
            Result.success(Unit)
        } catch (e: Exception) {
            Log.e(TAG, "resetSession failed", e)
            Result.failure(e)
        }
    }

    /**
     * 画像 + 陪伴统计合并成 UI 侧的 [StatusResponse]（旧契约字段名保留给 UI 层）。
     * profile 端点失败 = 整体失败（区分不出「画像为空」与「网络错误」，空态判断会失真）；
     * companion / sessions 尽力而为，缺失时置零/为空。
     */
    suspend fun getStatus(userId: String): Result<StatusResponse> {
        Log.d(TAG, "getStatus user_id=$userId")
        return try {
            val profile = api.profile(userId).profile
            val companion = runCatching { api.companion(userId) }.getOrNull()
            val sessions = runCatching { api.listSessions(userId) }.getOrNull()?.sessions.orEmpty()

            val totalTurns = companion?.user_message_count ?: 0
            Result.success(
                StatusResponse(
                    user_id = userId,
                    session_exists = sessions.isNotEmpty(),
                    turn_count = totalTurns,
                    conversation_count = sessions.count(),
                    attachment_style = profile.attachment_style,
                    core_fears = profile.core_fears.orEmpty(),
                    personal_values = profile.personal_values.orEmpty(),
                    rejection_sensitivity = mapRejectionSensitivity(profile.rejection_sensitivity),
                    rejection_sensitivity_label = profile.rejection_sensitivity,
                    distress_tolerance = profile.distress_tolerance
                )
            )
        } catch (e: Exception) {
            Log.e(TAG, "getStatus failed", e)
            Result.failure(e)
        }
    }

    suspend fun getCompanionStats(userId: String): Result<CompanionResponse> {
        Log.d(TAG, "GET /users/$userId/companion")
        return try {
            val response = api.companion(userId)
            Log.d(TAG, "GET /companion → days=${response.days_together} agent_msgs=${response.agent_message_count}")
            Result.success(response)
        } catch (e: Exception) {
            Log.e(TAG, "GET /companion failed", e)
            Result.failure(e)
        }
    }

    /** 画像三态查询（次要点，缺失/失败由上层 getOrNull() 尽力而为） */
    suspend fun getPortrait(userId: String): Result<PortraitResponse> {
        return try {
            val response = api.portrait(userId)
            Log.d(TAG, "GET /users/$userId/portrait → ${response.isAllNone()} pending=${response.hasPendingModules()}")
            Result.success(response)
        } catch (e: Exception) {
            Log.e(TAG, "GET /portrait failed", e)
            Result.failure(e)
        }
    }

    /**
     * 重判人格（「重新生成报告」）。异常原样保留——上层需按
     * HttpException.code() 区分 404（无画像可判）/ 429（频控）/ 其他。
     */
    suspend fun regeneratePersonality(userId: String): Result<Unit> {
        Log.d(TAG, "POST /users/$userId/portrait/personality/regenerate")
        return try {
            api.regeneratePersonality(userId)
            Result.success(Unit)
        } catch (e: Exception) {
            Log.e(TAG, "POST /regenerate failed", e)
            Result.failure(e)
        }
    }

    /** 用户信息覆盖写（设置页保存 / 重置形象全空覆盖）。 */
    suspend fun updateUserInfo(userId: String, request: UserInfoRequest): Result<Unit> {
        Log.d(TAG, "PUT /users/$userId/user-info")
        return try {
            api.updateUserInfo(userId, request)
            Result.success(Unit)
        } catch (e: Exception) {
            Log.e(TAG, "PUT /user-info failed", e)
            Result.failure(e)
        }
    }

    suspend fun fetchHistory(
        userId: String,
        page: Int = 1,
        pageSize: Int = 100
    ): Result<HistoryResponse> {
        Log.d(TAG, "fetchHistory user_id=$userId page=$page page_size=$pageSize")
        return try {
            val sid = resolveSessionId(userId)
            if (sid.isBlank()) {
                // 从未建会话 → 空历史
                return Result.success(HistoryResponse())
            }
            val response = api.history(sid, page, pageSize)
            Log.d(TAG, "GET /sessions/$sid/history → total=${response.total} items=${response.items.size}")
            Result.success(response)
        } catch (e: Exception) {
            Log.e(TAG, "GET /history failed", e)
            Result.failure(e)
        }
    }

    private fun mapRejectionSensitivity(label: String?): Float? = when (label?.lowercase()) {
        "high" -> 0.8f
        "medium", "mid" -> 0.5f
        "low" -> 0.2f
        else -> null
    }

    companion object {
        private const val TAG = "EA_Chat"
        private const val BASE_URL = "https://ea.eznju.com/"
        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    }
}
