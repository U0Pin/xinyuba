package com.njuse.ea.data

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * 后端 Emotion Support Agent API v0.2.0（三线并行架构）。
 *
 * 注意：POST /chat 为 SSE 流式响应（text/event-stream），不走 Retrofit，
 * 见 [ChatRepository.streamChat]。
 */
interface ApiService {

    @POST("sessions")
    suspend fun createSession(@Body request: SessionCreateRequest): SessionCreateResponse

    @GET("users/{user_id}/sessions")
    suspend fun listSessions(@Path("user_id") userId: String): SessionListResponse

    @POST("sessions/{session_id}/reset")
    suspend fun resetSession(@Path("session_id") sessionId: String): ResetResponse

    @GET("sessions/{session_id}/history")
    suspend fun history(
        @Path("session_id") sessionId: String,
        @Query("page") page: Int = 1,
        @Query("page_size") pageSize: Int = 100
    ): HistoryResponse

    @GET("users/{user_id}/profile")
    suspend fun profile(@Path("user_id") userId: String): ProfileResponse

    @GET("users/{user_id}/companion")
    suspend fun companion(@Path("user_id") userId: String): CompanionResponse

    // ---- 画像页新接口（docs/ProfileScreen-api-contract.md，2026-09 上线）----

    @GET("users/{user_id}/portrait")
    suspend fun portrait(@Path("user_id") userId: String): PortraitResponse

    @POST("users/{user_id}/portrait/personality/regenerate")
    suspend fun regeneratePersonality(@Path("user_id") userId: String): RegenerateResponse

    /** 响应体 schema 未声明，只看 code */
    @PUT("users/{user_id}/user-info")
    suspend fun updateUserInfo(@Path("user_id") userId: String, @Body body: UserInfoRequest): Response<Unit>
}
