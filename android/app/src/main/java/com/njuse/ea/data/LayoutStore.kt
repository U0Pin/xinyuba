package com.njuse.ea.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import android.util.Log
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.layoutDataStore: DataStore<Preferences> by preferencesDataStore(name = "ea_layout")

/**
 * 自定义场景布局的持久化（DataStore Preferences，单 key 存整份 JSON）。
 * 与 ChatHistoryStore 同一套路：读不到/解析失败一律回落默认布局。
 * 编解码走 [SceneLayoutCodec]（v1 左上角存储自动迁移为 v2 中心存储）。
 */
class LayoutStore(context: Context) {

    private val dataStore = context.layoutDataStore

    companion object {
        private const val TAG = "EA_Layout"
        private val KEY_LAYOUT = stringPreferencesKey("layout_json")
    }

    /** 反应式布局流：DataStore 每次写入都发出合并默认后的最新布局。 */
    val layoutFlow: Flow<SceneLayout> = dataStore.data.map { prefs ->
        try {
            val json = prefs[KEY_LAYOUT]
            val stored = json?.let { SceneLayoutCodec.parse(it) }
            DefaultLayout.effective(stored)
        } catch (e: Exception) {
            Log.e(TAG, "layoutFlow parse failed, fallback to default", e)
            DefaultLayout.layout
        }
    }

    /** 读取并合并默认布局（保证返回的 items 含全部已知 id）。 */
    suspend fun load(): SceneLayout = try {
        val json = dataStore.data.first()[KEY_LAYOUT]
        val stored = json?.let { SceneLayoutCodec.parse(it) }
        DefaultLayout.effective(stored)
    } catch (e: Exception) {
        Log.e(TAG, "load layout failed, fallback to default", e)
        DefaultLayout.layout
    }

    /** 落盘整份布局（通常存的是 effective 后的完整表）。 */
    suspend fun save(layout: SceneLayout) {
        try {
            dataStore.edit { prefs -> prefs[KEY_LAYOUT] = SceneLayoutCodec.toJson(layout) }
        } catch (e: Exception) {
            Log.e(TAG, "save layout failed", e)
        }
    }

    /** 恢复默认：清除覆盖数据。 */
    suspend fun reset() {
        try {
            dataStore.edit { prefs -> prefs.remove(KEY_LAYOUT) }
        } catch (e: Exception) {
            Log.e(TAG, "reset layout failed", e)
        }
    }
}
