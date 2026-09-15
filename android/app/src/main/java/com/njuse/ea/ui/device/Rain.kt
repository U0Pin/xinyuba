package com.njuse.ea.ui.device

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.withFrameNanos
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

/**
 * 轻量细雨粒子效果：单个 Canvas + withFrameNanos 驱动的固定池粒子。
 *
 * 设计目标（贴合现有纯 Compose 架构，零第三方依赖）：
 * 1. 单次 draw pass：所有雨滴在一个 Canvas 里用 drawLine 一次画完，绝不开每粒子一个 Composable
 *    （每粒子一个 Composable = 每帧 N 次重组，是 Compose 动画最常见的性能陷阱）。
 * 2. 固定池 + 取模回绕：用 FloatArray 存池（每滴 4 字段 x,y,len,speed），落出屏幕即 y=-len、x=random
 *    回绕——整动画循环零对象分配，消除 GC 抖动。颜色 + alpha 池化进常量，按索引取用。
 * 3. 驱动 withFrameNanos（非 rememberInfiniteTransition）：单 LaunchedEffect + 单 mutableStateOf(frame)
 *    触发整 Canvas 一次重画，避免 N 个 Animatable 的快照开销。
 * 4. enabled 是 StateFlow collect 出来的 Boolean：false 时 LaunchedEffect 协程自动取消，零 CPU；
 *    跟随 Composition 生命周期，息屏/切后台自动停，无需额外的 LifecycleEventObserver。
 *
 * 视觉：~60 滴细雨、strokeWidth 1dp、alpha 0.18~0.28、倾角 75°（向右下方斜落）。雨天街道氛围。
 */
@Composable
fun RainBox(
    enabled: Boolean,
    widthPx: Float,
    heightPx: Float,
    modifier: Modifier = Modifier
) {
    if (!enabled || widthPx <= 0f || heightPx <= 0f) return

    val density = LocalDensity.current
    val strokeWidthPx = with(density) { 1.dp.toPx() }

    // 倾角：75° 自垂直方向向右下倾斜。dx=cosθ, dy=sinθ。
    val (dirX, dirY) = remember { run { val a = 75f * Math.PI / 180f; cos(a).toFloat() to sin(a).toFloat() } }

    // 关键：雨滴向右下斜落，若 x 起点只在 [0,width]，轨迹永远到不了左下角三角形区域。
    // 把 x 起点向左延伸 maxDrift：从屏幕左外侧（负 x）开始下落，斜向右下即可覆盖左下角。
    // maxDrift ≈ 屏幕高 × tan(θ) = 雨滴从顶到底的水平右移量。
    val maxDrift = remember(widthPx, heightPx, dirX, dirY) {
        if (dirY > 0f) heightPx * (dirX / dirY) else 0f
    }

    // 固定粒子池：N 滴 × 4 字段 (x, y, len, speed)。只在 size 变化时重新初始化。
    val dropCount = 60
    val pool = remember(widthPx, heightPx, dropCount, maxDrift) {
        FloatArray(dropCount * 4).also { arr ->
            val rng = Random(System.currentTimeMillis())
            for (i in 0 until dropCount) {
                val base = i * 4
                arr[base] = -maxDrift + rng.nextFloat() * (widthPx + maxDrift)  // x ∈ [-maxDrift, width]
                arr[base + 1] = rng.nextFloat() * heightPx     // y
                arr[base + 2] = 28f + rng.nextFloat() * 32f     // len (px) 28~60
                arr[base + 3] = 420f + rng.nextFloat() * 380f   // speed (px/s) 420~800
            }
        }
    }

    // 帧驱动：每帧更新池（在数组上原地改写，零分配），再 bump 一个 Long 触发 Canvas 重画。
    var frame by remember { mutableLongStateOf(0L) }
    LaunchedEffect(enabled, widthPx, heightPx) {
        var lastNanos = 0L
        while (true) {
            withFrameNanos { nano ->
                val dt = if (lastNanos == 0L) 16_000_000L else (nano - lastNanos).coerceAtMost(64_000_000L)
                lastNanos = nano
                val dtSec = dt / 1_000_000_000f
                var i = 0
                while (i < pool.size) {
                    val moveX = dirX * pool[i + 3] * dtSec
                    val moveY = dirY * pool[i + 3] * dtSec
                    pool[i]     += moveX
                    pool[i + 1] += moveY
                    // 回绕：落出屏幕底部重置到顶部、随机 x（向左延伸 maxDrift 覆盖左下角）
                    if (pool[i + 1] - pool[i + 2] > heightPx || pool[i] > widthPx + pool[i + 2]) {
                        pool[i]     = -maxDrift + Random.nextFloat() * (widthPx + maxDrift)
                        pool[i + 1] = -pool[i + 2]
                        pool[i + 3] = 420f + Random.nextFloat() * 380f
                    }
                    i += 4
                }
                frame = nano
            }
        }
    }

    // 单次 draw pass：遍历池 drawLine 全画完。每条的 alpha 用池索引取模固定，省一次 add。
    // 关键：必须在这里读 frame（即便只是当前帧号），否则 pool 上的原地修改不会被
    // Compose snapshot 系统观测、draw 不会被 invalidate → 画一帧就静止。
    Canvas(modifier = modifier) {
        @Suppress("UNUSED_EXPRESSION") frame   // 触发 snapshot read → 帧更新时整 Canvas 重画
        val rainColor = Color.White
        var i = 0
        var dropIndex = 0
        while (i < pool.size) {
            val x = pool[i]
            val y = pool[i + 1]
            val len = pool[i + 2]
            val a = 0.18f + (dropIndex % 6) * 0.017f   // 0.18~0.28 离散档位
            val endX = x + dirX * len
            val endY = y + dirY * len
            drawLine(
                color = rainColor.copy(alpha = a),
                start = Offset(x, y),
                end = Offset(endX, endY),
                strokeWidth = strokeWidthPx
            )
            i += 4
            dropIndex++
        }
    }
}