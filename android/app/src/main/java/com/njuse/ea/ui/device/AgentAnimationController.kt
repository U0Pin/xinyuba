package com.njuse.ea.ui.device

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.random.Random

/**
 * Agent 猫咪动画状态机。
 *
 * 控制与播放分离：本类只管「现在该播哪个片段 / 下一个该播什么 / 事件如何排队」，
 * 不持有任何 Compose/Android 资源；帧序列解析与渲染交给 [AgentCat] Composable。
 * 这样状态逻辑可单测、可复用，UI 层只做 state -> (frames, loop) 映射。
 *
 * 四个片段（均 15fps，帧数由 [rememberFrameIds] 自动探测、改资源无需改代码）：
 * - [IDLE]      cat_idle    循环；每播 2..4 次插一次 IDLE_2
 * - [IDLE_2]    cat_idle_2  单次；播完回 IDLE
 * - [TOUCHED]   cat_touched 单次；播完回 IDLE
 * - [THINKING]  cat_think   单次；播完回 IDLE（只播一次，不随 isLoading 延长）
 *
 * 切换规则（用户已确认）：
 * - 不再「必播完才切」。任意片段间切换（含 TOUCHED 连点重播、IDLE 循环接缝）都由
 *   FrameAnimation 的 150ms crossfade 平滑过渡（旧帧冻结在底层、新帧顶层淡入，无跳变、
 *   不透底），切换本身即时无视觉断裂。
 * - 何时切：当前配置（SWITCH_WINDOW_END=0.5）下任意进度都立即切，无延后——
 *   姿态跳变全部交给 FrameAnimation 的果冻压扁 + 运动模糊 + crossfade 掩蔽，
 *   点击/发送零延迟。pending 机制保留（onFrame 消费），把常量调回 0.25 即可恢复
 *   「前/后 1/4 立即切、中间 1/2 延后到后 1/4」的旧策略。
 * - 事件优先级：THINKING（发送）到来时立即抢占正在播放的 TOUCHED/IDLE；
 *   THINKING 播放中点击则让 TOUCHED 打断它（点击总是有反馈）。
 * - cancelThinking：若 THINKING 已在播或已被 TOUCHED 打断 -> 无需处理；
 *   若 THINKING 仅 pending 未开播 -> 清掉。
 */
enum class AgentAnimState { IDLE, IDLE_2, TOUCHED, THINKING }

class AgentAnimationController {

    private val _state = MutableStateFlow(AgentAnimState.IDLE)
    val state: StateFlow<AgentAnimState> = _state.asStateFlow()

    /**
     * 每次发起一个新片段时递增。作为 FrameAnimation 的 replayKey，确保同状态
     * 重播（如 TOUCHED->TOUCHED 连点）也能从第 0 帧重新开始。
     */
    private val _playToken = MutableStateFlow(0)
    val playToken: StateFlow<Int> = _playToken.asStateFlow()

    // 当前片段播放进度 0..1（由 onFrame 每帧更新；IDLE 循环按每轮绕回周期计）。
    @Volatile private var progress: Float = 0f

    // 落在中间 1/2 窗口而延后的切换请求；进度进入后 1/4 时由 onFrame 自动消费。null=无。
    private var pending: AgentAnimState? = null

    // isLoading=true 期间为 true。仅用于决定 THINKING 请求是否有效：
    // 响应在 THINKING 开播前到达 -> 取消 pending；响应在 THINKING 播放中到达 -> 自然播完。
    private var thinkingRequested = false

    // IDLE 循环计数：每完成一轮 IDLE +1，达到阈值 -> 切 IDLE_2 并重置。
    private var idlePlaysSinceIdle2 = 0
    private var nextIdle2Threshold = Random.nextInt(2, 5) // 2..4 inclusive

    /** 用户点击 agent。任意状态（含 THINKING 播放中）都立即切 TOUCHED。 */
    fun requestTouched() {
        synchronized(lock) {
            // 思考中点击也响应：TOUCHED 打断 THINKING 播放；响应到达由
            // cancelThinking 处理，不依赖 THINKING 必须播完，无副作用。
            requestSwitch(AgentAnimState.TOUCHED)
        }
    }

    /**
     * 用户发送消息（isLoading 翻 true）。抢占已 pending 的 TOUCHED；若当前非 THINKING
     * 且无 THINKING pending，则按进度窗口立即切或延后切 THINKING。
     */
    fun requestThinking() {
        synchronized(lock) {
            thinkingRequested = true
            if (_state.value == AgentAnimState.THINKING) return // 已在思考
            if (pending == AgentAnimState.THINKING) return       // 已待切思考
            requestSwitch(AgentAnimState.THINKING)
        }
    }

    /**
     * 统一的切换决策：落在前/后 1/4 进度窗口立即切（crossfade 平滑过渡）；
     * 落在中间 1/2（主动作区）记为 pending，等 [onFrame] 推进到后 1/4 再切。
     * THINKING 优先级高于 TOUCHED，可直接抢占 pending。
     */
    private fun requestSwitch(target: AgentAnimState) {
        val current = _state.value
        if (current == target && target == AgentAnimState.THINKING) return
        if (progress < SWITCH_WINDOW_END || progress >= 1f - SWITCH_WINDOW_END) {
            pending = null
            startClip(target)
        } else {
            pending = target
        }
    }

    /**
     * FrameAnimation 每次换帧回调（约 15/s）。更新当前片段进度；若有 pending 切换
     * 且进度已进入后 1/4 窗口，立即消费切换。
     */
    fun onFrame(frameIdx: Int, frameCount: Int) {
        synchronized(lock) {
            progress = if (frameCount > 0) (frameIdx + 1f) / frameCount else 1f
            val target = pending
            if (target != null && progress >= 1f - SWITCH_WINDOW_END) {
                pending = null
                startClip(target)
            }
        }
    }

    /**
     * 响应到达（isLoading 翻 false）。
     * - 仅 pending 未开播：清 pending（响应比 think 先到，回 IDLE 流程）。
     * - 正在播 THINKING：让其自然播完（[onClipComplete] 会切回 IDLE）。
     */
    fun cancelThinking() {
        synchronized(lock) {
            thinkingRequested = false
            if (pending == AgentAnimState.THINKING) pending = null
        }
    }

    /**
     * FrameAnimation 每轮/末帧回调。状态转换的唯一出口：在此消费排队、
     * 推进 IDLE->IDLE_2 的周期、或回到 IDLE。
     */
    fun onClipComplete() {
        synchronized(lock) {
            val current = _state.value
            when (current) {
                AgentAnimState.IDLE -> {
                    // 循环片段：先看有无延后切换（onFrame 通常已在后 1/4 消费掉，
                    // 此处为兜底，处理跨 loop 边界尚未消费的情况）
                    val q = pending
                    if (q != null) {
                        pending = null
                        startClip(q)
                    } else {
                        idlePlaysSinceIdle2++
                        if (idlePlaysSinceIdle2 >= nextIdle2Threshold) {
                            idlePlaysSinceIdle2 = 0
                            nextIdle2Threshold = Random.nextInt(2, 5)
                            startClip(AgentAnimState.IDLE_2)
                        }
                        // 否则留 IDLE 继续循环：不 bump token；绕回接缝由
                        // FrameAnimation 的 crossfade 抹平
                    }
                }
                AgentAnimState.IDLE_2,
                AgentAnimState.TOUCHED -> {
                    // 单次片段播完：消费延后切换或回 IDLE
                    val q = pending
                    if (q != null) {
                        pending = null
                        startClip(q)
                    } else {
                        resetIdleAndStart()
                    }
                }
                AgentAnimState.THINKING -> {
                    // 单次思考播完：thinkingRequested 不延长 THINKING（只播一次）
                    val q = pending
                    if (q != null) {
                        pending = null
                        startClip(q)
                    } else {
                        resetIdleAndStart()
                    }
                }
            }
        }
    }

    private fun startClip(next: AgentAnimState) {
        progress = 0f
        _state.value = next
        _playToken.value++
    }

    private fun resetIdleAndStart() {
        idlePlaysSinceIdle2 = 0
        nextIdle2Threshold = Random.nextInt(2, 5)
        _state.value = AgentAnimState.IDLE
        _playToken.value++
    }

    private val lock = Any()

    companion object {
        // 进度窗口边界：progress < W 或 >= 1-W 时立即切，中间段延后。
        // 设为 0.5 -> 任意进度都立即切（延后区为 0，切换全靠 punch+crossfade 掩蔽）。
        private const val SWITCH_WINDOW_END = 0.5f
    }
}

/**
 * 记住一个 [AgentAnimationController] 实例，跨重组复用。
 */
@Composable
fun rememberAgentAnimationController(): AgentAnimationController {
    return remember { AgentAnimationController() }
}

/**
 * Agent 猫咪渲染：观察 [AgentAnimationController] 的 state + playToken，
 * 映射到对应帧序列与循环模式，交给 [FrameAnimation] 播放，并把每轮/末帧完成
 * 回调转给 controller 决定下一个片段。
 *
 * 帧数由 [rememberFrameIds] 自动探测（从 baseName_00000 起递增到找不到为止），
 * 这里只声明 baseName 与是否循环 -- 改资源/改帧数无需动本文件。
 * 命名约定（与 res/drawable 下 cat_*_NNNNN.webp 对应）：
 * - cat_idle    循环   - cat_idle_2  单次
 * - cat_touched 单次   - cat_think   单次
 */
@Composable
fun AgentCat(
    controller: AgentAnimationController,
    modifier: Modifier = Modifier,
    contentDescription: String? = "Agent猫咪",
    contentScale: ContentScale = ContentScale.FillBounds
) {
    val state by controller.state.collectAsState()
    val token by controller.playToken.collectAsState()
    // baseName / loop / punch 参数按状态映射：
    // - TOUCHED（点击）：明显果冻弹跳（压到 0.90），反馈感强；
    // - THINKING（发送）：轻柔呼吸（压到 0.96），安静克制；
    // - IDLE / IDLE_2（背景）：不压，循环接缝只靠 crossfade 抹平。
    val baseName: String
    val loop: Boolean
    val punchScale: Float
    when (state) {
        AgentAnimState.IDLE -> {
            baseName = "cat_idle"; loop = true; punchScale = 1f
        }
        AgentAnimState.IDLE_2 -> {
            baseName = "cat_idle_2"; loop = false; punchScale = 1f
        }
        AgentAnimState.TOUCHED -> {
            baseName = "cat_touched"; loop = false; punchScale = 0.90f
        }
        AgentAnimState.THINKING -> {
            baseName = "cat_think"; loop = false; punchScale = 0.96f
        }
    }
    val frames = rememberFrameIds(baseName)
    FrameAnimation(
        frames = frames,
        modifier = modifier,
        fps = 15f,
        contentDescription = contentDescription,
        contentScale = contentScale,
        loop = loop,
        replayKey = token,
        crossfadeMs = 150L,
        punchScale = punchScale,
        onComplete = { controller.onClipComplete() },
        onFrame = { idx, count -> controller.onFrame(idx, count) }
    )
}
