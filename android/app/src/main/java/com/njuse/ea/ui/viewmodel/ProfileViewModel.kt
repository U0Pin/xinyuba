package com.njuse.ea.ui.viewmodel

import android.app.Application
import android.os.SystemClock
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.njuse.ea.data.ChatRepository
import com.njuse.ea.data.CompanionResponse
import com.njuse.ea.data.PortraitResponse
import com.njuse.ea.data.StatusResponse
import com.njuse.ea.data.hasPendingModules
import com.njuse.ea.data.hasProfileData
import com.njuse.ea.ui.screen.regenerateFailureMessage
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * [status] 非空 = 有真实画像数据；[empty] = 后端尚未构建画像（展示空态）；
 * [error] = 加载失败（展示重试）。三者互斥（loading 期间都为初始值）。
 * [portrait] = 新画像三态接口的原始数据（null = 尚未拉到，视同全 none）；
 * [regenerating] = 「重新生成报告」POST 在途（兼挡频控重复点击）。
 * [transientMessage] = 一次性轻提示（toast），不进 error 以免覆盖整页失败态。
 */
data class ProfileState(
    val status: StatusResponse? = null,
    val companion: CompanionResponse? = null,
    val portrait: PortraitResponse? = null,
    val regenerating: Boolean = false,
    val loading: Boolean = false,
    val error: String? = null,
    val empty: Boolean = false
)

class ProfileViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = ChatRepository()
    private val userId: String by lazy {
        android.provider.Settings.Secure.getString(
            getApplication<Application>().contentResolver,
            android.provider.Settings.Secure.ANDROID_ID
        )
    }

    private val _state = MutableStateFlow(ProfileState())
    val state: StateFlow<ProfileState> = _state.asStateFlow()

    private val _transientMessage = MutableStateFlow<String?>(null)
    val transientMessage: StateFlow<String?> = _transientMessage.asStateFlow()

    private var pollJob: Job? = null

    companion object {
        private const val TAG = "EA_Profile"
        private const val POLL_INTERVAL_MS = 4_000L
        // 轮询预算上限：超时后静默停止（占位保留，重进页面重新开始）
        private const val POLL_BUDGET_MS = 90_000L
    }

    init {
        loadProfile()
    }

    fun loadProfile() {
        _state.value = _state.value.copy(loading = true, error = null, empty = false)
        viewModelScope.launch {
            repository.getStatus(userId).fold(
                onSuccess = { response ->
                    val companion = repository.getCompanionStats(userId).getOrNull()
                    if (response.hasProfileData()) {
                        _state.value = ProfileState(status = response, companion = companion, loading = false)
                        Log.d(TAG, "Profile loaded from API")
                        startPollingIfNeeded()
                    } else {
                        // 画像由后端异步增量构建（多轮对话后才有值）→ 展示空态，
                        // 不再用编造的样例数据兜底（见 2026-09 改版）。
                        _state.value = ProfileState(empty = true, companion = companion, loading = false)
                        Log.d(TAG, "Profile empty (backend has no data yet)")
                    }
                    // portrait 是次要点：失败不推翻上面的数据态/空态判断
                    repository.getPortrait(userId).getOrNull()?.let { portrait ->
                        _state.value = _state.value.copy(portrait = portrait)
                        startPollingIfNeeded()
                    }
                },
                onFailure = { e ->
                    Log.e(TAG, "Failed to load profile", e)
                    _state.value = _state.value.copy(loading = false, error = "网络好像开小差了，稍后再试试吧")
                }
            )
        }
    }

    /**
     * 画像页轮询：存在 pending（生成中）模块时定时重拉 portrait，
     * 全部就绪 / 预算耗尽 / 网络失败即停。ViewModel 绑定 NavBackStackEntry，
     * 离开页面 onCleared → viewModelScope 取消 → 「离开即停」自动满足。
     */
    private fun startPollingIfNeeded() {
        val portrait = _state.value.portrait ?: return
        if (pollJob?.isActive == true || !portrait.hasPendingModules()) return
        pollJob = viewModelScope.launch {
            val deadline = SystemClock.elapsedRealtime() + POLL_BUDGET_MS
            while (isActive && SystemClock.elapsedRealtime() < deadline) {
                delay(POLL_INTERVAL_MS)
                repository.getPortrait(userId).fold(
                    onSuccess = { fresh ->
                        _state.value = _state.value.copy(portrait = fresh)
                        if (!fresh.hasPendingModules()) return@launch
                    },
                    onFailure = { return@launch }   // 网络抖动即停，重进页面会重试
                )
            }
        }
    }

    /**
     * 「重新生成报告」：POST 重判人格 → 本地把 personality 模块标 pending
     * 驱动 UI 转圈与轮询 → 轮询到 ready 后人格卡自动更新。
     * 失败（404 无画像可判 / 429 频控 / 其他）给一次性轻提示。
     */
    fun regeneratePersonality() {
        if (_state.value.regenerating) return
        viewModelScope.launch {
            _state.value = _state.value.copy(regenerating = true)
            repository.regeneratePersonality(userId).fold(
                onSuccess = {
                    val current = _state.value.portrait ?: PortraitResponse(user_id = userId)
                    val marked = current.copy(
                        personality = current.personality?.copy(status = "pending")
                            ?: com.njuse.ea.data.PortraitModule(status = "pending")
                    )
                    _state.value = _state.value.copy(portrait = marked, regenerating = false)
                    startPollingIfNeeded()
                },
                onFailure = { e ->
                    Log.e(TAG, "regeneratePersonality failed", e)
                    _state.value = _state.value.copy(regenerating = false)
                    _transientMessage.value = regenerateFailureMessage(e)
                }
            )
        }
    }

    fun consumeTransientMessage() {
        _transientMessage.value = null
    }
}
