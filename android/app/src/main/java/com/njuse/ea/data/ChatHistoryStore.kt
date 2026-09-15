package com.njuse.ea.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.first

private val Context.chatHistoryDataStore: DataStore<Preferences> by preferencesDataStore(name = "ea_chat_history")

class ChatHistoryStore(context: Context) {

    private val dataStore = context.chatHistoryDataStore

    companion object {
        private const val MAX_ITEMS = 200
        private val KEY_HISTORY = stringPreferencesKey("chat_history_json")

        private val _clearEvents = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
        val clearEvents: SharedFlow<Unit> = _clearEvents.asSharedFlow()
    }

    suspend fun load(): List<ChatMessage> = try {
        val json = dataStore.data.first()[KEY_HISTORY]
        parseChatHistory(json)
    } catch (e: Exception) {
        emptyList()
    }

    suspend fun append(message: ChatMessage) {
        dataStore.edit { prefs ->
            val current = parseChatHistory(prefs[KEY_HISTORY]).toMutableList()
            current.add(message)
            if (current.size > MAX_ITEMS) {
                prefs[KEY_HISTORY] = current.takeLast(MAX_ITEMS).toJson()
            } else {
                prefs[KEY_HISTORY] = current.toJson()
            }
        }
    }

    suspend fun clear() {
        dataStore.edit { prefs -> prefs.remove(KEY_HISTORY) }
        _clearEvents.emit(Unit)
    }

    suspend fun replaceAll(messages: List<ChatMessage>) {
        dataStore.edit { prefs ->
            prefs[KEY_HISTORY] = messages.takeLast(MAX_ITEMS).toJson()
        }
    }
}