package com.njuse.ea.ui.device

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.EaseIn
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.foundation.interaction.PressInteraction
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import kotlinx.coroutines.flow.collectLatest

/**
 * 通用「果冻按压」反馈：监听 [interactionSource] 的按下/松开，按下时以脚底为锚点
 * 快速压扁（60ms EaseIn，横向微扩张 0.35），松开/取消时阻尼弹簧弹回
 * （spring dampingRatio=0.55，带轻微过冲）。参数风格与 FrameAnimation 的 punch 一致，
 * 供静态可点物品（黑板/笔记本/发送按钮等）使用——不要与组件内部已有的 punch 叠加，
 * 否则会双重压扁。
 *
 * 用法（与 clickable 共用同一个 interactionSource）：
 * ```
 * val src = remember { MutableInteractionSource() }
 * Box(Modifier
 *     .tapFeedback(src)
 *     .clickable(interactionSource = src, indication = null) { ... })
 * ```
 *
 * @param pressedScale 压到最低点时的纵向比例（<1；越小越夸张）
 */
fun Modifier.tapFeedback(
    interactionSource: InteractionSource,
    pressedScale: Float = 0.94f
): Modifier = composed {
    val press = remember { Animatable(1f) }
    LaunchedEffect(interactionSource) {
        interactionSource.interactions.collectLatest { interaction ->
            when (interaction) {
                is PressInteraction.Press -> {
                    press.animateTo(pressedScale, tween(durationMillis = 60, easing = EaseIn))
                }
                is PressInteraction.Release,
                is PressInteraction.Cancel -> {
                    press.animateTo(1f, spring(dampingRatio = 0.55f, stiffness = 420f))
                }
            }
        }
    }
    graphicsLayer {
        val s = press.value
        scaleX = 1f + (1f - s) * 0.35f
        scaleY = s
        // 以脚底中心为锚点：像踩在地上被按下去。
        transformOrigin = TransformOrigin(0.5f, 1f)
    }
}
