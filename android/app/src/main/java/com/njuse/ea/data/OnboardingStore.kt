package com.njuse.ea.data

import android.content.Context
import android.util.Log
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.onboardingDataStore: DataStore<Preferences> by preferencesDataStore(name = "ea_onboarding")

/**
 * 新手引导完成标记的持久化（DataStore Preferences，单 key）。
 * 与 [LayoutStore] 同套路：独立 DataStore 文件，读不到一律视为「未完成」。
 * 独立成文件是为了让测试/设置页重放入口能直接操作，不必经由 ea_settings。
 */
class OnboardingStore(context: Context) {

    private val dataStore = context.onboardingDataStore

    companion object {
        private const val TAG = "EA_Onboarding"
        private val KEY_DONE = booleanPreferencesKey("onboarding_done")
    }

    /** 完成标记流：未读过/解析异常 → false（引导待播放）。 */
    val doneFlow: Flow<Boolean> = dataStore.data.map { prefs ->
        try {
            prefs[KEY_DONE] ?: false
        } catch (e: Exception) {
            Log.e(TAG, "read onboarding_done failed", e)
            false
        }
    }

    /** 引导看完/跳过 → 落盘，之后不再自动弹出。 */
    suspend fun markDone() {
        try {
            dataStore.edit { prefs -> prefs[KEY_DONE] = true }
        } catch (e: Exception) {
            Log.e(TAG, "markDone failed", e)
        }
    }

    /** 清除标记 → 引导将在下次回到主界面时重新播放（设置页「重看引导」/测试用）。 */
    suspend fun reset() {
        try {
            dataStore.edit { prefs -> prefs.remove(KEY_DONE) }
        } catch (e: Exception) {
            Log.e(TAG, "reset failed", e)
        }
    }
}
