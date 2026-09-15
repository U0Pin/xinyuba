package com.njuse.ea.ui.device

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.EaseIn
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.painter.BitmapPainter
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext

/**
 * 通用逐帧序列动画：按固定 fps 播放一组 drawable 帧资源，支持循环/单次+完成回调。
 *
 * 设计目标（与 Rain.kt 同构，纯 Compose 零第三方依赖）：
 * 1. 墙钟推导帧索引：以首帧纳秒为起点，framePeriod = 1e9 / fps，
 *    idx = ((now - start) / framePeriod) % size。丢帧/卡顿后从墙钟自纠偏，
 *    不靠逐帧计数（逐帧计数一旦掉一帧就永久漂移）。
 * 2. 单 LaunchedEffect + 单 mutableIntStateOf(idx)：每帧仅推进一个 Int，
 *    触发唯一一次 remember(idx) 重解码；当前帧 bitmap 缓存到下一帧切换，
 *    同时刻仅 1 张存活，全帧不会全部驻留内存。
 * 3. 坏帧容错：decodeFrame 对损坏/缺失帧返回 null（而非抛异常），该帧渲染为空、
 *    动画继续推进到下一帧，单帧损坏不会带崩整个 Composition。
 * 4. playing=false 时 LaunchedEffect 协程取消，停在当前帧；切后台/息屏随
 *    Composition 生命周期自动停，零 CPU。
 *
 * 片段控制（loop / onComplete / replayKey）：
 * - loop=true（默认）：idx 在 [0,size) 取模循环；onComplete 在每轮结束时触发一次
 *   （供上层计数循环次数，如 idle 每播 N 次插一次 idle_2）。
 * - loop=false：idx 走到 size-1 后停住；onComplete 在末帧触发一次。供 one-shot
 *   动画（touched / think）使用。
 * - replayKey：加入 LaunchedEffect 的 key。外部想「重播同一个片段」时递增
 *   replayKey（例如 TOUCHED->TOUCHED 连点），effect 重启、startNanos 归零、
 *   从第 0 帧重新开始。仅换 frames/loop 也会重启，但同状态同帧序列重播必须靠 replayKey。
 * - onComplete 经 rememberUpdatedState 持有，lambda 变化不会重启 effect、不打断动画。
 * - onFrame：每次换到新帧时回调 (frameIdx, frameCount)，frameIdx 在 [0,frameCount)
 *   （loop 片段每轮绕回也回调）。供上层读取播放进度（见 AgentAnimationController
 *   的「前/后 1/4 立即切、中间 1/2 延后切」策略）。同样经 rememberUpdatedState 持有。
 *
 * 片段间过渡（crossfadeMs，默认 150ms）：
 * - 帧解码在动画协程内完成，painter 是 mutableStateOf——effect 重启（换片段/重播）
 *   或 loop 绕回（最后一帧->第 0 帧）的那一刻，painter 里确定性地还是旧片段的当前帧，
 *   将其冻结截获为 outgoing，再开始播放新片段。
 * - 接力式叠化（线性）：渲染叠放两层，crossfade 值 p 从 0 涨到 1——前半段（p 0->0.5）
 *   新帧顶层 alpha 0->1 淡入、旧帧底层全程 100% 垫底；后半段（p 0.5->1）新帧已盖满、
 *   旧帧 alpha 1->0 淡出。任意时刻至少一层完全不透明，不会「变淡/透底」；叠加集中在
 *   切换瞬间，旧帧过渡结束即消失，不会拖到后续运动上形成鬼影。AI 素材首尾帧不一致
 *   造成的循环接缝跳变也由此抹平。
 * - crossfade 动画值按 transitionId 每次重建、初始即 0f：过渡第一帧组合读到的就是
 *   「新层透明/旧层全显」，不依赖 effect 归零，无 stale 一帧闪烁。
 * - 过渡中再次重启（快速连点）：直接以当时新片段的当前帧替换 outgoing 并重置计时。
 * - 峰值内存为 2 张 bitmap（过渡结束后旧帧释放）。crossfadeMs=0 不建 outgoing，硬切。
 *
 * 切换「果冻弹跳」（punchScale < 1 时启用，仅换片段/重播触发，循环绕回不触发）：
 * 游戏/宠物类常见的点击反馈手法——纯 alpha 叠帧过渡在姿态差异大时会出现双重鬼影、
 * 观感像「闪一下」；改用运动掩蔽：切换瞬间角色以脚底为锚点快速压扁（55ms EaseIn，
 * 模拟按下去的预备动作），在压到最低点、动得最快时新片段开播（跳变被压扁运动 +
 * crossfade 掩蔽），再以阻尼弹簧弹回（spring dampingRatio=0.55，带 ~4% 过冲，
 * Q 弹余动）。不使用 Modifier.blur：其在 API 31/32 对带透明区域的图层会把透明像素
 * 模糊成黑色（容器糊成黑矩形的已知问题），且实测无模糊观感更好。
 * punchScale=1 时不播放任何弹跳。
 *
 * 新增动画流程：把 baseName_NNNNN.{webp,png} 扔进 res/drawable/，再调
 * FrameAnimation(rememberFrameIds("baseName"), fps = N) 即可（帧数自动探测，
 * 改资源/改帧数无需动代码）。
 */
@Composable
fun FrameAnimation(
    frames: IntArray,
    modifier: Modifier = Modifier,
    fps: Float = 15f,
    contentDescription: String? = null,
    contentScale: ContentScale = ContentScale.FillBounds,
    playing: Boolean = true,
    loop: Boolean = true,
    replayKey: Int = 0,
    crossfadeMs: Long = 150L,
    punchScale: Float = 1f,
    onComplete: (() -> Unit)? = null,
    onFrame: ((frameIdx: Int, frameCount: Int) -> Unit)? = null
) {
    if (frames.isEmpty()) return
    val framePeriod = (1_000_000_000.0 / fps).toLong().coerceAtLeast(1L)
    val context = LocalContext.current
    // painter/outgoingPainter 均为 mutableStateOf：解码在动画协程内进行，
    // effect 重启（换片段/重播）或 loop 绕回时，painter 里确定性地还是旧片段当前帧，
    // 可可靠截获为 outgoing（不依赖 Composable 间 effect 的执行先后）。
    var painter by remember { mutableStateOf<Painter?>(null) }
    var outgoingPainter by remember { mutableStateOf<Painter?>(null) }
    var transitionId by remember { mutableIntStateOf(0) }
    // 每次过渡新建 Animatable，初始即 0f：组合读到过渡第一帧就是「新层透明/旧层全显」，
    // 不依赖 LaunchedEffect 里 snapTo 归零（effect 在组合提交后才跑，旧值 1f 会造成
    // 一帧 stale 闪烁：旧帧瞬间消失、新帧满帧，再跳回淡入）。
    val crossfade = remember(transitionId) { Animatable(if (transitionId == 0) 1f else 0f) }
    // punch：换片段/重播计数（循环绕回不递增）与压扁比例（1=正常、<1=被压扁）。
    var clipStartId by remember { mutableIntStateOf(0) }
    val punch = remember { Animatable(1f) }
    // 回调均经 rememberUpdatedState 持有最新 lambda：重组时不因 lambda 变化重启
    // LaunchedEffect（那会打断动画、重置 startNanos）。effect 只在帧序列/循环模式/
    // replayKey 变化时重启--这些正是「该从头播一个新片段」的合法触发。
    val currentOnComplete by rememberUpdatedState(onComplete)
    val currentOnFrame by rememberUpdatedState(onFrame)
    LaunchedEffect(playing, frames, framePeriod, loop, replayKey) {
        if (!playing) return@LaunchedEffect
        // 换片段/重播：截获旧片段当前帧为 outgoing 并触发一次 crossfade + punch。
        // 首次播放 painter 为 null，transitionId 保持 0 -> 不淡入；crossfadeMs<=0 硬切。
        val prev = painter
        if (prev != null && crossfadeMs > 0) {
            outgoingPainter = prev
            transitionId++
        }
        clipStartId++
        var startNanos = 0L
        var loopsFired = 0
        var lastIdx = -1
        while (true) {
            val now = withFrameNanos { it }
            if (startNanos == 0L) startNanos = now
            val elapsed = now - startNanos
            val totalDur = frames.size * framePeriod
            var idx: Int
            var skipDecode = false
            if (loop) {
                idx = ((elapsed / framePeriod) % frames.size).toInt()
                val loopsDone = (elapsed / totalDur).toInt()
                if (loopsDone > loopsFired) {
                    loopsFired = loopsDone
                    // 循环绕回（末帧->第 0 帧）：把尾帧截获为 outgoing 做 crossfade。
                    // 关键：此帧不再解码第 0 帧（painter 停在尾帧），否则若本次
                    // onComplete 触发切片段，新片段协程截获到的会是第 0 帧而非尾帧。
                    // 下一 vsync idx>=1 时补解码，此时顶层 alpha≈0、outgoing 兜底，不可见。
                    if (crossfadeMs > 0 && painter != null) {
                        outgoingPainter = painter
                        transitionId++
                        skipDecode = true
                    }
                    currentOnComplete?.invoke()
                }
            } else {
                idx = if (elapsed >= totalDur) {
                    if (loopsFired == 0) {
                        loopsFired = 1
                        currentOnComplete?.invoke()
                    }
                    frames.size - 1
                } else {
                    (elapsed / framePeriod).toInt()
                }
            }
            if (!skipDecode && idx != lastIdx) {
                lastIdx = idx
                // 手动解码而非 painterResource：后者对坏帧做非空强转会抛异常带崩整个
                // Composition；BitmapFactory.decodeStream 坏帧返回 null -> 该帧渲染为空、
                // 动画继续推进，单帧损坏不影响应用。
                painter = decodeFrame(context, frames[idx])?.let { BitmapPainter(it.asImageBitmap()) }
                currentOnFrame?.invoke(idx, frames.size)
            }
        }
    }
    // crossfade 驱动：transitionId 每次递增（换片段/重播/循环绕回）都重跑。
    // 动画值已在 remember(transitionId) 处从 0f 起步，无需 snapTo。结束后清空 outgoing
    // 释放旧 bitmap（峰值 2 张）。
    LaunchedEffect(transitionId) {
        if (transitionId == 0 || crossfadeMs <= 0) {
            outgoingPainter = null
            return@LaunchedEffect
        }
        crossfade.animateTo(1f, animationSpec = tween(durationMillis = crossfadeMs.toInt(), easing = LinearEasing))
        outgoingPainter = null
    }
    // punch 驱动：每次换片段/重播（clipStartId 递增）播放「快速压扁 -> 弹簧弹回」。
    // 压扁最低点（约 55ms，新片段此时正好开播）切换+模糊，动作跳变被运动掩蔽；
    // 阻尼弹簧弹回带轻微过冲，模拟果冻余动。过渡中再次触发（快速连点）协程被取消、
    // 从当前比例重新压扁，无突变。punchScale=1（待机/背景）只弹性回 1f，无可见形变。
    LaunchedEffect(clipStartId, punchScale) {
        if (punchScale >= 1f) {
            punch.animateTo(1f, spring(dampingRatio = 1f))
            return@LaunchedEffect
        }
        punch.animateTo(punchScale, tween(durationMillis = 55, easing = EaseIn))
        punch.animateTo(1f, spring(dampingRatio = 0.55f, stiffness = 420f))
    }
    val outgoing = outgoingPainter
    Box(
        modifier = modifier
            .graphicsLayer {
                val s = punch.value
                scaleX = 1f + (1f - s) * 0.35f
                scaleY = s
                // 以脚底中心为锚点：像踩在地上被按下去，而不是原地缩小。
                transformOrigin = TransformOrigin(0.5f, 1f)
            }
    ) {
        // 接力式叠化（p 从 0 线性涨到 1）：前半段新帧 0->1 淡入，旧帧全程 100% 垫底；
        // 后半段新帧已盖满，旧帧 1->0 淡出。任意时刻至少一层完全不透明（不变淡/不透底），
        // 叠加集中在切换瞬间，旧帧过渡结束即消失，不会拖到后续运动上成鬼影。
        val p = crossfade.value
        // 底层：旧冻结帧。前半段保持 100%，后半段线性淡出。
        if (outgoing != null) {
            Image(
                painter = outgoing,
                contentDescription = null,
                modifier = Modifier
                    .matchParentSize()
                    .alpha((1f - (p - 0.5f) * 2f).coerceIn(0f, 1f)),
                contentScale = contentScale
            )
        }
        // 顶层：新片段帧。前半段线性淡入（p>=0.5 起完全不透明）。
        val current = painter
        if (current != null) {
            Image(
                painter = current,
                contentDescription = contentDescription,
                modifier = Modifier
                    .matchParentSize()
                    .alpha(if (outgoing != null) (p * 2f).coerceIn(0f, 1f) else 1f),
                contentScale = contentScale
            )
        }
    }
}

/**
 * 按命名约定 baseName_NNNNN 解析 drawable resId。
 * 约定：帧文件名 = "${baseName}_${序号.toString().padStart(digits, '0')}"，
 * 序号从 0 连续递增、无空洞。
 *
 * 帧数解析两种模式（解耦资源与代码）：
 * - count >= 0（显式）：解析序号 0..count-1，兼容旧调用。
 * - count < 0（默认 -1，自动探测）：从 0 递增调用 getIdentifier，遇到第一个返回 0
 *   （找不到）即停。这样「只换资源/改帧数不改代码」--新增或更新动画只需把
 *   baseName_NNNNN.{webp,png} 扔进 res/drawable/，帧数由实际文件数决定。
 *
 * 只在首次组合跑一次并 remember 缓存；后续重组零开销。
 *
 * 注：getIdentifier 官方"不推荐"主要因慢 + 工具看不到引用；此处仅在首次组合跑
 * 一次（~ms 级），release 未开 minify 无 ProGuard 风险。换来"加动画零样板"
 * （无需手写 N 行 resId 数组、帧数变化无需改代码），对多帧动画场景最划算。
 */
@Suppress("DiscouragedApi", "LocalContextResourcesRead")  // getIdentifier 在此为有意为之：
// 仅首帧组合跑一次并 remember 缓存，resId 不随配置变化，读取无重组必要；换取"加帧动画
// 零样板"（无需手写 N 行 resId 数组、帧数变化无需改代码）。release 未开 minify，无 ProGuard 风险。
@Composable
fun rememberFrameIds(
    baseName: String,
    count: Int = -1,
    digits: Int = 5
): IntArray {
    val ctx = LocalContext.current
    return remember(baseName, count, digits) {
        val res = ctx.resources
        val pkg = ctx.packageName
        if (count >= 0) {
            IntArray(count) { i ->
                res.getIdentifier(
                    "${baseName}_${i.toString().padStart(digits, '0')}",
                    "drawable", pkg
                )
            }
        } else {
            // 自动探测：从 0 递增直到 getIdentifier 返回 0（找不到）。
            // 命名约定要求帧序号从 0 连续递增、无空洞，故首个缺口即末尾。
            val out = ArrayList<Int>(64)
            var i = 0
            while (true) {
                val id = res.getIdentifier(
                    "${baseName}_${i.toString().padStart(digits, '0')}",
                    "drawable", pkg
                )
                if (id == 0) break
                out.add(id)
                i++
            }
            IntArray(out.size) { out[it] }
        }
    }
}

/**
 * 安全解码一帧 drawable 为 Bitmap：坏帧/缺帧返回 null 而非抛异常。
 * - inScaled=false：按文件原始像素尺寸解码，不做密度缩放（最终拉伸交给调用方
 *   的 ContentScale.FillBounds），避免高密度设备被 ×3 放大浪费内存。
 * - openRawResource + decodeStream：绕开 painterResource 的非空强转，坏文件
 *   解码失败时返回 null，调用方据此降级为空帧。
 * - 仅支持位图资源（png/webp/jpg）；矢量 drawable 解码返回 null，安全降级。
 */
private fun decodeFrame(context: Context, resId: Int): Bitmap? {
    if (resId == 0) return null
    return try {
        val opts = BitmapFactory.Options().apply { inScaled = false }
        context.resources.openRawResource(resId).use { input ->
            BitmapFactory.decodeStream(input, null, opts)
        }
    } catch (e: Exception) {
        Log.w("FrameAnimation", "decode failed resId=$resId: ${e.message}")
        null
    }
}
