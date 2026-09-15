package com.njuse.ea.ui.viewmodel

import android.app.Application
import android.provider.Settings
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.njuse.ea.data.ChatHistoryStore
import com.njuse.ea.data.ChatMessage
import com.njuse.ea.data.ChatRepository
import com.njuse.ea.data.toChatMessage
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.io.IOException
import java.net.SocketTimeoutException

/** 气泡尾部/折叠态的「待处理」占位状态。null 表示空闲（显示最近回复）。 */
sealed class Pending {
    data class Thinking(val base: String) : Pending()   // 猫猫思索中
    data class Syncing(val base: String) : Pending()    // 猫猫同步中
    data class Error(val code: String) : Pending()     // 错误占位
}

/** 展开态滚动指令。 */
sealed class ScrollTarget {
    object Bottom : ScrollTarget()
    data class ToIndex(val index: Int) : ScrollTarget()
}

class ChatViewModel(application: Application) : AndroidViewModel(application) {

    private val repository = ChatRepository()
    private val historyStore = ChatHistoryStore(application)

    private val userId: String by lazy {
        Settings.Secure.getString(
            application.contentResolver,
            Settings.Secure.ANDROID_ID
        )
    }

    /** 折叠态气泡：最近一条 agent 回复（或问候语）。 */
    private val _latestAgentReply = MutableStateFlow("喵~今天心情如何？")
    val latestAgentReply: StateFlow<String> = _latestAgentReply.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val messages: StateFlow<List<ChatMessage>> = _messages.asStateFlow()

    private val _syncing = MutableStateFlow(false)
    val syncing: StateFlow<Boolean> = _syncing.asStateFlow()

    private val _pendingStatus = MutableStateFlow<Pending?>(null)
    val pendingStatus: StateFlow<Pending?> = _pendingStatus.asStateFlow()

    /** 本次超时未得到回答的提问，供 refresh 后判定是否需要重发。 */
    private var pendingQuestion: String? = null

    /**
     * 轮次序号：_isLoading 的归属标记。主回复完成即放开按钮后，上一轮的连接可能还在
     * 后台排空，只有「哪一轮开启的」才有权把它复位（见 sendMessage 的 finally）。
     */
    @Volatile private var activeTurnSeq = 0

    private val _scrollEvent = MutableSharedFlow<ScrollTarget>(extraBufferCapacity = 8)
    val scrollEvent: SharedFlow<ScrollTarget> = _scrollEvent.asSharedFlow()

    private val _lastTimedOut = MutableStateFlow(false)

    private val _toast = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val toast: SharedFlow<String> = _toast.asSharedFlow()

    init {
        viewModelScope.launch {
            _messages.value = historyStore.load()
            syncHistory(showToast = true)
        }
        viewModelScope.launch {
            ChatHistoryStore.clearEvents.collect {
                _messages.value = emptyList()
                _latestAgentReply.value = "历史对话已经清除了喵，聊聊新话题？"
                _pendingStatus.value = null
                pendingQuestion = null
            }
        }
    }

    companion object {
        private const val TAG = "EA_Chat"
    }

    private fun updateLatestReply() {
        _messages.value.lastOrNull { it.role == ChatMessage.Role.AGENT }?.let {
            _latestAgentReply.value = it.text
        }
    }

    /**
     * 从服务端拉取历史并整体替换本地。
     * @return 是否同步成功
     */
    private suspend fun syncHistory(showToast: Boolean): Boolean {
        _syncing.value = true
        return try {
            repository.fetchHistory(userId, page = 1, pageSize = 100).fold(
                onSuccess = { resp ->
                    // items 为扁平消息、新→旧；反转为时间顺序
                    val ordered = resp.items.asReversed().map { it.toChatMessage() }
                    _messages.value = ordered
                    historyStore.replaceAll(ordered)
                    _lastTimedOut.value = false
                    updateLatestReply()
                    Log.d(TAG, "syncHistory success: ${ordered.size} messages")
                    true
                },
                onFailure = { e ->
                    Log.e(TAG, "syncHistory failed: ${e.javaClass.simpleName} — ${e.message}", e)
                    if (showToast) {
                        _toast.tryEmit("喵...同步历史失败了，请检查网络后再试(っ╥﹏╥っ)")
                    }
                    false
                }
            )
        } finally {
            // 同步失败/抛异常同样要放开——它与 _isLoading 一起门控发送键，卡住同样是死锁。
            _syncing.value = false
        }
    }

    fun sendMessage(text: String) {
        if (_isLoading.value) return
        _isLoading.value = true
        // 本轮序号：UI 的「发送中」与连接的「还在读」从本轮起是两件事——主回复说完就放开
        // 按钮，而连接可能仍在后台排空（后端还在跑决策线/沉淀线）。只有本轮自己才有权
        // 复位 _isLoading，否则上一轮的迟到收尾会把新一轮的按钮状态误清掉。
        val myTurn = ++activeTurnSeq

        val userMsg = ChatMessage(ChatMessage.Role.USER, text, System.currentTimeMillis())

        viewModelScope.launch {
            try {
                _pendingStatus.value = Pending.Thinking("猫猫思索中")
                _messages.value = _messages.value + userMsg
                historyStore.append(userMsg)
                _scrollEvent.tryEmit(ScrollTarget.Bottom)

                // 真·流式：后端每生成一个 token 就推送，onToken 在 IO 线程回调，
                // 这里立即把增量贴进气泡（切到 Main 更新 Compose state），不做任何人为延迟。
                val replyBuilder = StringBuilder()
                // 本轮 agent 气泡的身份（timestamp）。放开按钮后用户可能已经发出下一轮，
                // 列表末尾不再是本轮消息，所以定位一律按身份，不能用 lastIndex。
                var agentTs: Long? = null

                fun indexOfAgent(): Int = agentTs?.let { ts ->
                    _messages.value.indexOfLast {
                        it.role == ChatMessage.Role.AGENT && it.timestamp == ts
                    }
                } ?: -1

                fun upsertAgentMessage(): Int {
                    val list = _messages.value.toMutableList()
                    val known = indexOfAgent()
                    return if (known >= 0) {
                        list[known] = list[known].copy(text = replyBuilder.toString())
                        _messages.value = list
                        known
                    } else {
                        val ts = System.currentTimeMillis()
                        list.add(ChatMessage(ChatMessage.Role.AGENT, replyBuilder.toString(), ts))
                        _messages.value = list
                        agentTs = ts
                        if (activeTurnSeq == myTurn) _pendingStatus.value = null
                        list.lastIndex
                    }
                }

                val result = repository.streamChat(
                    userId = userId,
                    message = text,
                    onToken = { delta ->
                        replyBuilder.append(delta)
                        val idx = upsertAgentMessage()
                        // 本轮自己的气泡无条件更新（上面），但折叠态气泡与自动滚动是**共享**状态：
                        // 只有在仍是本轮时才写，避免迟到的 token 把新一轮的界面拽回去。
                        if (activeTurnSeq == myTurn) {
                            _latestAgentReply.value = replyBuilder.toString()
                            _scrollEvent.tryEmit(ScrollTarget.ToIndex(idx))
                        }
                    },
                    // 主回复说完即解除「发送中」——此刻连接仍开着，后端还在跑决策线与
                    // 沉淀线（画像/摘要，最慢数十秒），但那不该阻塞用户继续说话。
                    onDialogueDone = {
                        if (activeTurnSeq == myTurn) _isLoading.value = false
                    }
                )

                result.fold(
                    onSuccess = { r ->
                        val finalText = r.text.ifBlank { replyBuilder.toString() }
                        val idx: Int
                        val known = indexOfAgent()
                        if (known >= 0) {
                            val list = _messages.value.toMutableList()
                            list[known] = list[known].copy(text = finalText)
                            _messages.value = list
                            idx = known
                        } else {
                            val msg = ChatMessage(ChatMessage.Role.AGENT, finalText, System.currentTimeMillis())
                            _messages.value = _messages.value + msg
                            idx = _messages.value.lastIndex
                        }
                        _messages.value.getOrNull(idx)?.let { historyStore.append(it) }
                        // 共享 UI 状态只在「本轮仍是最新一轮」时写。上一轮的连接可能在新一轮
                        // 开始后才结束（后端沉淀线期间同 session 的新消息要排队，实测空窗可达十余秒），
                        // 那时若照写，就会清掉新一轮的「猫猫思索中」、把折叠气泡改回旧文案、
                        // 并把列表滚回旧消息——这正是「思索中消失 + 跳回上条回复」的成因。
                        // 落库（上一行）是**本轮自己的内容**，与轮次无关，照写。
                        if (activeTurnSeq == myTurn) {
                            _latestAgentReply.value = finalText
                            _lastTimedOut.value = false
                            _pendingStatus.value = null
                            pendingQuestion = null
                            _scrollEvent.tryEmit(ScrollTarget.ToIndex(idx))
                        }
                    },
                    onFailure = { e ->
                        Log.e(TAG, "sendMessage error: ${e.javaClass.simpleName} — ${e.message}", e)
                        // 已流出部分回复则保留并落库，不再覆盖为错误占位
                        val known = indexOfAgent()
                        if (known >= 0) {
                            _messages.value.getOrNull(known)?.let { historyStore.append(it) }
                        }
                        // 同上：占位/重发线索都是共享状态，只有本轮仍是最新一轮时才动。
                        if (activeTurnSeq == myTurn) {
                            if (e is SocketTimeoutException) {
                                _lastTimedOut.value = true
                            }
                            if (known < 0) {
                                val code = when (e) {
                                    is HttpException -> e.code().toString()
                                    is IOException -> "NETWORK"
                                    else -> "UNKNOWN"
                                }
                                pendingQuestion = text
                                _pendingStatus.value = Pending.Error(code)
                            } else {
                                pendingQuestion = null
                                _pendingStatus.value = null
                            }
                        }
                    }
                )
            } finally {
                // 正常结束 / 异常 / 协程取消，任何路径都必须放开，杜绝按钮永久灰掉。
                if (activeTurnSeq == myTurn) _isLoading.value = false
            }
        }
    }

    /**
     * 超时后的 refresh：先同步历史，再按后端是否已生成本题答案决定
     * 「滚到答案开头」还是「重发本题」。
     */
    fun refresh() {
        if (_isLoading.value || _syncing.value) return
        val question = pendingQuestion ?: return
        viewModelScope.launch {
            _pendingStatus.value = Pending.Syncing("猫猫同步中")
            val ok = syncHistory(showToast = true)
            if (!ok) {
                _pendingStatus.value = Pending.Error("SYNC")
                return@launch
            }
            // 同步后的列表中，最后一条用户消息是否等于本次提问？
            val lastUserText = _messages.value
                .lastOrNull { it.role == ChatMessage.Role.USER }?.text
            if (lastUserText == question) {
                // 后端已生成本题答案 → 滚到 agent 回答开头
                _latestAgentReply.value =
                    _messages.value.lastOrNull { it.role == ChatMessage.Role.AGENT }?.text
                        ?: _latestAgentReply.value
                _pendingStatus.value = null
                pendingQuestion = null
                _messages.value.indexOfLast { it.role == ChatMessage.Role.AGENT }
                    .takeIf { it >= 0 }
                    ?.let { _scrollEvent.tryEmit(ScrollTarget.ToIndex(it)) }
            } else {
                // 后端没处理本题 → 重新发请求，走思索中流程
                sendMessage(question)
            }
        }
    }
}