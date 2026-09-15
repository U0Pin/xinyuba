package com.njuse.ea.ui.widget

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.njuse.ea.ui.theme.AccentTeal
import com.njuse.ea.ui.theme.CreamDim
import com.njuse.ea.ui.theme.CreamWhite
import com.njuse.ea.ui.theme.GlassBorder
import com.njuse.ea.ui.theme.GlassCard
import kotlin.math.max

/**
 * 首次启动的聚光灯分步引导：暖棕半透明遮罩挖孔高亮目标 + 玻璃风说明卡。
 * 与主题统一：遮罩取主场景深棕色调（而非纯黑），挖孔描 AccentTeal 光环并带呼吸脉冲，
 * 说明卡用画像/设置页同款 GlassCard + GlassBorder + CreamWhite/CreamDim 文字，
 * CTA 胶囊与「跳过」按钮同设置页玻璃胶囊观感。
 *
 * 步骤 0=欢迎（无挖孔），1..3 依次对应 holes 里的目标 rect（root 像素坐标）。
 * 点遮罩任意处 / 卡片按钮 = 下一步；「跳过」与系统返回键 = onSkip（立即结束）。
 *
 * 挖孔在 Offscreen 合成层里用 BlendMode.DstOut 抠出圆角矩形，孔洞全透明；
 * rect 外扩 16dp 吸收陀螺仪视差等造成的坐标漂移（最大漂移 ≈ 4% 屏宽）。
 * 挖孔带入场动画：步骤切换时从大孔收缩到目标孔（聚光灯「聚」的瞬间）。
 */
private const val LAST_STEP = 3

/** 每步文案：标题 / 正文（推进方式 = 点击屏幕任意处，无按钮）。 */
private data class StepCopy(val title: String, val body: String)

private fun stepCopy(step: Int): StepCopy = when (step) {
    0 -> StepCopy("欢迎来到心语吧 🐾", "这是一只愿意听你说话的猫猫。\n点击屏幕任意处，开始认识这里吧。")
    1 -> StepCopy("和猫猫聊天", "在下方输入框说说你的心情，\n猫猫会一直在这里回应你。")
    2 -> StepCopy("功能菜单", "点聊天窗左上角的爪印，\n可以打开设置和足迹入口。")
    else -> StepCopy("你的个人画像", "右侧黑板记着你的成长足迹与人格画像，\n随时回来看看。")
}

@Composable
fun OnboardingOverlay(
    step: Int,
    holes: Map<Int, Rect?>,
    onNext: () -> Unit,
    onSkip: () -> Unit
) {
    val density = LocalDensity.current
    val expandPx = with(density) { 16.dp.toPx() }
    val cornerPx = with(density) { 20.dp.toPx() }

    // 挖孔入场动画：目标 rect 变化时从挖满全屏收缩到目标孔。rect 未就绪（null）时只显示遮罩。
    val hole = holes[step]
    val holeProgress by animateFloatAsState(
        targetValue = if (hole != null) 1f else 0f,
        animationSpec = tween(durationMillis = 420, easing = FastOutSlowInEasing),
        label = "onboardingHole"
    )

    // 呼吸脉冲 0..1（挖孔外圈光环）
    val pulseTransition = rememberInfiniteTransition(label = "onboardingPulse")
    val pulse by pulseTransition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 1200, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulse"
    )

    BackHandler { onSkip() }

    Box(
        Modifier
            .fillMaxSize()
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null
            ) { onNext() }
    ) {
        // 遮罩层：offscreen 合成后用 DstOut 抠孔，保证孔洞全透明。
        // 遮罩色用主场景深棕（#241610 系），与咖啡馆暖色调一致，不用纯黑。
        Box(
            Modifier
                .fillMaxSize()
                .graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }
                .drawBehind {
                    drawRect(Color(0xCC241610))
                    val t = hole
                    if (t != null && holeProgress > 0.01f) {
                        // 从「挖得非常大」插值收缩到目标孔：中心不变，尺寸 (1 + 6*(1-p)) 倍
                        val baseW = max(t.width + expandPx * 2, 1f)
                        val baseH = max(t.height + expandPx * 2, 1f)
                        val grow = 1f + 6f * (1f - holeProgress)
                        val curW = baseW * grow
                        val curH = baseH * grow
                        drawRoundRect(
                            color = Color.Transparent,
                            topLeft = Offset(t.center.x - curW / 2f, t.center.y - curH / 2f),
                            size = Size(curW, curH),
                            cornerRadius = CornerRadius(cornerPx + 24.dp.toPx() * (1f - holeProgress)),
                            blendMode = BlendMode.DstOut
                        )
                    }
                }
        )

        // 挖孔光环：teal 描边 + 呼吸外圈（画在遮罩之上、卡片之下；rect 未就绪则不画）
        hole?.let { h ->
            val ringLeft = h.left - expandPx
            val ringTop = h.top - expandPx
            val ringW = max(h.width + expandPx * 2, 1f)
            val ringH = max(h.height + expandPx * 2, 1f)
            val ringWdp = with(density) { (ringW / density.density).dp }
            val ringHdp = with(density) { (ringH / density.density).dp }
            Box(
                Modifier
                    .graphicsLayer {
                        translationX = ringLeft
                        translationY = ringTop
                        alpha = holeProgress
                    }
                    .size(ringWdp, ringHdp)
                    .drawBehind {
                        drawRoundRect(
                            color = AccentTeal.copy(alpha = 0.9f),
                            cornerRadius = CornerRadius(cornerPx),
                            style = Stroke(width = 2.dp.toPx())
                        )
                        drawRoundRect(
                            color = AccentTeal.copy(alpha = 0.35f * pulse),
                            cornerRadius = CornerRadius(cornerPx),
                            style = Stroke(width = (2.dp.toPx() + 4.dp.toPx() * pulse))
                        )
                    }
            )
        }

        // 步骤说明卡（玻璃风：GlassCard 底 + GlassBorder 描边，同设置/画像页）
        // 注：已按产品要求移除「跳过」按钮与「下一步」CTA——推进方式统一为点击屏幕任意处，
        // 系统返回键仍可结束引导（BackHandler → onSkip）。
        val copy = stepCopy(step)
        val cardModifier = when (step) {
            0 -> Modifier.align(Alignment.Center).padding(horizontal = 32.dp)
            1 -> Modifier.align(Alignment.TopCenter).padding(top = 90.dp, start = 32.dp, end = 32.dp)
            2 -> Modifier.align(Alignment.TopCenter).padding(top = 110.dp, start = 48.dp, end = 24.dp)
            else -> Modifier.align(Alignment.CenterStart).padding(start = 20.dp, end = 120.dp)
        }

        Column(
            cardModifier
                .clip(RoundedCornerShape(20.dp))
                .background(GlassCard)
                .border(1.dp, GlassBorder, RoundedCornerShape(20.dp))
                .padding(horizontal = 18.dp, vertical = 14.dp)
        ) {
            Text(copy.title, color = CreamWhite, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            Spacer(modifier = Modifier.height(6.dp))
            Text(copy.body, color = CreamDim, fontSize = 12.sp, lineHeight = 18.sp)
            Spacer(modifier = Modifier.height(12.dp))
            // 进度点
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                repeat(LAST_STEP + 1) { i ->
                    Box(
                        Modifier
                            .size(if (i == step) 7.dp else 5.dp)
                            .clip(CircleShape)
                            .background(if (i == step) AccentTeal else CreamDim.copy(alpha = 0.35f))
                    )
                }
            }
        }
    }
}
