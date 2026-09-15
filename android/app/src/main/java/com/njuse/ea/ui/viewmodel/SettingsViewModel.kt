package com.njuse.ea.ui.viewmodel

import android.app.Application
import android.util.Log
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.njuse.ea.data.ChatHistoryStore
import com.njuse.ea.data.ChatRepository
import com.njuse.ea.data.DefaultLayout
import com.njuse.ea.data.LayoutStore
import com.njuse.ea.data.OnboardingStore
import com.njuse.ea.data.SceneLayout
import com.njuse.ea.data.UserInfoRequest
import com.njuse.ea.data.toRequest
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

private val Application.dataStore: DataStore<Preferences> by preferencesDataStore(name = "ea_settings")

data class UserInfo(
    val nickname: String = "",
    val gender: String = "",       // "male", "female", ""
    val age: Int = 0,
    val tonePreference: String = "" // "gentle", "lively", ""
)

/** 用户信息 dirty 判定：null draft = 无未保存改动；draft 与 baseline 相同也不算 dirty。纯函数，可单测 */
fun draftIsDirty(draft: UserInfo?, baseline: UserInfo): Boolean =
    draft != null && draft != baseline

class SettingsViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = ChatRepository()
    private val dataStore = application.dataStore
    private val historyStore = ChatHistoryStore(application)
    private val layoutStore = LayoutStore(application)
    private val onboardingStore = OnboardingStore(application)
    private val userId: String by lazy {
        android.provider.Settings.Secure.getString(
            getApplication<Application>().contentResolver,
            android.provider.Settings.Secure.ANDROID_ID
        )
    }

    companion object {
        private const val TAG = "EA_Settings"
        private val KEY_NICKNAME = stringPreferencesKey("nickname")
        private val KEY_GENDER = stringPreferencesKey("gender")
        private val KEY_AGE = intPreferencesKey("age")
        private val KEY_TONE = stringPreferencesKey("tone_preference")
        private val KEY_PARALLAX = booleanPreferencesKey("parallax_enabled")
        private val KEY_RAIN = booleanPreferencesKey("rain_enabled")
    }

    // --- 陀螺仪视差（设备偏好，默认开启） ---
    // 反应式：直接订阅 DataStore，MainScreen 与 SettingsScreen 各自的 ViewModel 实例
    // （分属不同 nav entry 的独立 ViewModelStore）共享同一 DataStore 文件，开关切换后
    // 所有订阅者立即收到新值——避免「init 只读一次」造成的跨实例开关失效。
    val parallaxEnabled: StateFlow<Boolean> = dataStore.data
        .map { it[KEY_PARALLAX] ?: true }
        .stateIn(viewModelScope, SharingStarted.Eagerly, true)

    // --- 下雨效果（视觉偏好，默认开启） ---
    // 反应式：与 parallaxEnabled 同构，复用同一 DataStore 文件，MainScreen 与 SettingsScreen
    // 各自的 ViewModel 实例（分属不同 nav entry）共享同一文件，开关切换后所有订阅者立即收到新值。
    val rainEnabled: StateFlow<Boolean> = dataStore.data
        .map { it[KEY_RAIN] ?: true }
        .stateIn(viewModelScope, SharingStarted.Eagerly, true)

    // --- 自定义场景布局（反应式） ---
    // 订阅独立的 ea_layout DataStore；MainScreen 与 LayoutEditorScreen 各自的 ViewModel
    // 实例共享同一文件，编辑保存后所有订阅者立即收到新布局。未自定义时发默认布局。
    val layout: StateFlow<SceneLayout> = layoutStore.layoutFlow
        .stateIn(viewModelScope, SharingStarted.Eagerly, DefaultLayout.layout)

    // --- 人格形象性别（反应式，默认女生形象） ---
    // 与 parallaxEnabled / rainEnabled 同构：订阅 KEY_GENDER，未设置时回退女生形象。
    // ProfileScreen 直接 collect 该 StateFlow，设置界面切换「男 / 女 / 未设置」后立即生效。
    val personalityGender: StateFlow<String> = dataStore.data
        .map { it[KEY_GENDER] ?: "" }
        .stateIn(viewModelScope, SharingStarted.Eagerly, "")

    // --- 新手引导完成标记（反应式） ---
    // initial 用 true：首帧先视为已完成，DataStore 发出 false 后再弹引导，
    // 避免冷启动时 DataStore 未就绪导致「闪一下又弹出」的竞态。
    val onboardingDone: StateFlow<Boolean> = onboardingStore.doneFlow
        .stateIn(viewModelScope, SharingStarted.Eagerly, true)

    /** 引导看完/跳过 → 落盘，之后不再自动弹出。 */
    fun completeOnboarding() {
        viewModelScope.launch { onboardingStore.markDone() }
    }

    /** 设置页「重看引导」：清除标记，返回主界面即重新播放。 */
    fun replayOnboarding() {
        viewModelScope.launch { onboardingStore.reset() }
    }

    // --- 用户信息 ---
    // baseline（_userInfo，与 DataStore 一致）+ 内存暂存（_draftUserInfo）。
    // 四项改动先落 draft，离开设置页时统一弹窗确认：保存 → 写 DataStore + PUT 后端；
    // 不保存 → 丢弃 draft 回滚（与 LayoutEditor 的退出确认同模式）。
    private val _userInfo = MutableStateFlow(UserInfo())
    val userInfo: StateFlow<UserInfo> = _userInfo.asStateFlow()

    private val _draftUserInfo = MutableStateFlow<UserInfo?>(null)   // null = 无未保存改动

    /** 编辑中的用户信息（无 draft 时跟随 baseline），设置页 UI 绑定用 */
    val editingUserInfo: StateFlow<UserInfo> =
        combine(_userInfo, _draftUserInfo) { base, draft -> draft ?: base }
            .stateIn(viewModelScope, SharingStarted.Eagerly, UserInfo())

    val isUserInfoDirty: StateFlow<Boolean> =
        combine(_userInfo, _draftUserInfo) { base, draft -> draftIsDirty(draft, base) }
            .stateIn(viewModelScope, SharingStarted.Eagerly, false)

    // --- 重置状态 ---
    private val _resetResult = MutableStateFlow<String?>(null)
    val resetResult: StateFlow<String?> = _resetResult.asStateFlow()

    init {
        loadPreferences()
    }

    private fun loadPreferences() {
        viewModelScope.launch {
            try {
                val prefs = dataStore.data.first()
                _userInfo.value = UserInfo(
                    nickname = prefs[KEY_NICKNAME] ?: "",
                    gender = prefs[KEY_GENDER] ?: "",
                    age = prefs[KEY_AGE] ?: 0,
                    tonePreference = prefs[KEY_TONE] ?: ""
                )
            } catch (e: Exception) {
                Log.e(TAG, "Failed to load preferences", e)
            }
        }
    }

    fun setParallaxEnabled(enabled: Boolean) {
        viewModelScope.launch {
            try {
                dataStore.edit { prefs -> prefs[KEY_PARALLAX] = enabled }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to save parallax", e)
            }
        }
    }

    fun setRainEnabled(enabled: Boolean) {
        viewModelScope.launch {
            try {
                dataStore.edit { prefs -> prefs[KEY_RAIN] = enabled }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to save rain", e)
            }
        }
    }

    // 四项用户信息：只改内存暂存，等离开设置页时统一确认保存（见 saveUserInfoEdits）
    fun updateNickname(nickname: String) {
        _draftUserInfo.value = (_draftUserInfo.value ?: _userInfo.value).copy(nickname = nickname)
    }

    fun updateGender(gender: String) {
        _draftUserInfo.value = (_draftUserInfo.value ?: _userInfo.value).copy(gender = gender)
    }

    fun updateAge(age: Int) {
        _draftUserInfo.value = (_draftUserInfo.value ?: _userInfo.value).copy(age = age)
    }

    fun updateTonePreference(tone: String) {
        _draftUserInfo.value = (_draftUserInfo.value ?: _userInfo.value).copy(tonePreference = tone)
    }

    // --- 自定义布局 ---
    /** 覆盖单个组件布局并落盘（传入的 layout 通常是当前完整布局，改其中一项后整体存）。 */
    fun updateLayout(newLayout: SceneLayout) {
        viewModelScope.launch {
            try {
                layoutStore.save(DefaultLayout.effective(newLayout))
            } catch (e: Exception) {
                Log.e(TAG, "Failed to save layout", e)
            }
        }
    }

    /** 恢复默认布局。 */
    fun resetLayout() {
        viewModelScope.launch {
            try {
                layoutStore.reset()
            } catch (e: Exception) {
                Log.e(TAG, "Failed to reset layout", e)
            }
        }
    }

    // --- 用户信息保存（离开设置页的退出确认弹窗调用）---

    /**
     * 把暂存的四项改动落盘并同步后端。DataStore 写失败返回 false（不退出，
     * 由调用方提示）；后端 PUT 尽力而为（失败不阻塞退出，本地为源）。
     */
    suspend fun saveUserInfoEdits(): Boolean {
        val draft = _draftUserInfo.value ?: return true
        return try {
            dataStore.edit { prefs ->
                prefs[KEY_NICKNAME] = draft.nickname
                prefs[KEY_GENDER] = draft.gender
                prefs[KEY_AGE] = draft.age
                prefs[KEY_TONE] = draft.tonePreference
            }
            _userInfo.value = draft
            _draftUserInfo.value = null
            // 客户端只写不读：后端仅作 Agent 用途存储，失败静默记日志
            repository.updateUserInfo(userId, draft.toRequest())
                .onFailure { e -> Log.e(TAG, "PUT user-info failed (local saved)", e) }
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to save user info", e)
            false
        }
    }

    /** 不保存退出：丢弃暂存，回滚到 baseline */
    fun discardUserInfoEdits() {
        _draftUserInfo.value = null
    }

    // --- 重置功能 ---
    fun resetConversation() {
        viewModelScope.launch {
            try {
                historyStore.clear()
                repository.resetSession(userId)
                _resetResult.value = "对话已重置"
                Log.d(TAG, "resetSession done")
            } catch (e: Exception) {
                _resetResult.value = "重置失败：${e.message}"
                Log.e(TAG, "resetSession failed", e)
            }
        }
    }

    fun resetProfile() {
        viewModelScope.launch {
            try {
                historyStore.clear()
                // 重置后端会话（清空画像）
                repository.resetSession(userId)
                // 清空本地用户信息
                dataStore.edit { prefs ->
                    prefs.remove(KEY_NICKNAME)
                    prefs.remove(KEY_GENDER)
                    prefs.remove(KEY_AGE)
                    prefs.remove(KEY_TONE)
                }
                _userInfo.value = UserInfo()
                _draftUserInfo.value = null
                // 后端用户信息用全空请求体整份覆盖（空值 = 未设置，契约无需删除接口）
                runCatching { repository.updateUserInfo(userId, UserInfoRequest()) }
                    .onFailure { e -> Log.e(TAG, "PUT empty user-info failed", e) }
                _resetResult.value = "形象与对话已重置"
                Log.d(TAG, "resetProfile done")
            } catch (e: Exception) {
                _resetResult.value = "重置失败：${e.message}"
                Log.e(TAG, "resetProfile failed", e)
            }
        }
    }

    fun clearResetResult() {
        _resetResult.value = null
    }
}
