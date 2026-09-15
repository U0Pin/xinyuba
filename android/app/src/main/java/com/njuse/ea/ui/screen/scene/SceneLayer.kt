package com.njuse.ea.ui.screen.scene

import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.ui.platform.testTag
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.layout
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import com.njuse.ea.R
import com.njuse.ea.data.ItemLayout
import com.njuse.ea.data.SceneItemId
import com.njuse.ea.data.SceneLayout
import com.njuse.ea.data.SCREEN_CX
import com.njuse.ea.data.SCREEN_CY
import com.njuse.ea.ui.device.AgentCat
import com.njuse.ea.ui.device.rememberFrameIds
import com.njuse.ea.ui.device.tapFeedback
import kotlin.math.roundToInt

/**
 * 把「设计坐标 → 屏幕坐标」的换算状态打包，避免每个物品重复传参。
 * scale = 让 cafe_room(1188×2642) Crop 铺满屏幕的倍率；halfW/halfH 为屏幕中心(dp)。
 * 画布常量 (SCREEN_W/CAFE_W 等) 定义在 data/SceneLayout.kt（与布局数据同源）。
 */
class SceneScale(val scale: Float, val halfW: Float, val halfH: Float) {
    /** 设计稿 X → 屏幕 dp（屏幕中心 + 相对设计中心的偏移 × scale）。 */
    fun dx(designX: Float): Float = halfW + (designX - SCREEN_CX) * scale
    /** 设计稿 Y → 屏幕 dp。 */
    fun dy(designY: Float): Float = halfH + (designY - SCREEN_CY) * scale
    /** 设计稿尺寸 → 屏幕 dp。 */
    fun dp(v: Float) = (v * scale).dp
}

/**
 * 按 [ItemLayout]（中心坐标+尺寸+旋转）放置一个场景物品。
 * 内部用「屏幕中心 + 设计中心偏移」换算并按 scale 缩放尺寸；调用处只给布局数据。
 * [extraModifier] 追加到根 Box（编辑器用它挂选择/拖动手势）。
 */
@Composable
fun DesignBox(
    scene: SceneScale,
    item: ItemLayout,
    onClick: (() -> Unit)? = null,
    tapFeedback: Boolean = true,
    extraModifier: Modifier = Modifier,
    content: @Composable () -> Unit
) {
    val ox = scene.dx(item.cx)
    val oy = scene.dy(item.cy)
    val sw = item.w * scene.scale
    val sh = item.h * scene.scale
    // 关掉默认 ripple：该设备上 ripple 被渲染成黑色矩形。反馈改用 tapFeedback 果冻压扁。
    // extraModifier（编辑器选择/拖动手势）放在 graphicsLayer(rotation) 之前：手势节点在
    // 旋转层之外，拖移位移是屏幕绝对方向，不随物品旋转而偏转；点击命中区仍含旋转。
    // 显式 layout：按「设计尺寸×scale」的精确 px 约束测量内容并 place(0,0) 顶格放置——
    // 物品可大于屏幕（约束来自父容器会被钳扁/居中，size/requiredSize/wrapContentSize
    // 的溢出语义都不适合此场景），命中测试区域=真实完整盒子。
    val interactionSource = remember { MutableInteractionSource() }
    var mod: Modifier = Modifier
        .offset(x = (ox - sw / 2f).dp, y = (oy - sh / 2f).dp)
        .layout { measurable, constraints ->
            val w = (sw.dp.toPx()).roundToInt()
            val h = (sh.dp.toPx()).roundToInt()
            val p = measurable.measure(
                Constraints(minWidth = w, maxWidth = w, minHeight = h, maxHeight = h)
            )
            layout(constraints.maxWidth, constraints.maxHeight) { p.place(0, 0) }
        }
        .then(extraModifier)
        .graphicsLayer { rotationZ = item.rotation }
    if (onClick != null) {
        mod = mod.clickable(
            interactionSource = interactionSource,
            indication = null
        ) { onClick() }
        if (tapFeedback) mod = mod.tapFeedback(interactionSource)
    }
    Box(modifier = mod) {
        content()
    }
}

/** 便利封装：单张图（最常见），自动 fillMaxSize + FillBounds。 */
@Composable
fun DesignImage(
    resId: Int,
    scene: SceneScale,
    item: ItemLayout,
    onClick: (() -> Unit)? = null,
    tapFeedback: Boolean = true,
    extraModifier: Modifier = Modifier,
    contentDescription: String? = null
) {
    DesignBox(scene, item, onClick, tapFeedback, extraModifier) {
        Image(
            painter = painterResource(resId),
            contentDescription = contentDescription,
            modifier = Modifier.fillMaxSize(),
            contentScale = ContentScale.FillBounds
        )
    }
}

/**
 * 渲染场景里的全部可摆放物品（5 个物品 + 对话窗口不在此内，对话窗口由调用方自行布局）。
 * MainScreen 与布局编辑器共用，保证「所见即所得」。
 *
 * - [editable]=false（主界面）：业务点击生效、猫播放动画、装饰物品有果冻反馈。
 * - [editable]=true（编辑器）：无任何业务点击、猫显示静态首帧、不播动画；
 *   选择/拖动手势由 [itemExtraModifier] 注入到每个物品根节点。
 */
@Composable
fun SceneItems(
    layout: SceneLayout,
    scene: SceneScale,
    editable: Boolean = false,
    onCatTouch: () -> Unit = {},
    onBoardClick: (() -> Unit)? = null,
    catContent: @Composable () -> Unit = {},
    itemExtraModifier: (String) -> Modifier = { Modifier }
) {
    val items = layout.items
    // 物品语义 tag：主界面与编辑器共用（测试用 UiAutomator/Compose finder 定位物品）。
    // 必须挂在 DesignBox 的 offset/layout 之后（extraModifier 追加位置即如此），
    // 语义 bounds 才包含平移。
    fun ext(id: String): Modifier = itemExtraModifier(id).testTag("item-$id")

    // Z 顺序（L1 视差层内）：装饰/猫在下，黑板、笔记本在最上（仅低于对话气泡与输入栏）。
    // 二者现为纯装饰（入口统一收口到聊天窗顶部按钮行），仅保留果冻按压反馈。

    // 盆栽（装饰）
    items[SceneItemId.PLANT]?.let {
        DesignImage(
            R.drawable.plant, scene, it,
            onClick = if (editable) null else ({ }),
            extraModifier = ext(SceneItemId.PLANT)
        )
    }

    // 咖啡杯（装饰）
    items[SceneItemId.COFFEE_CUP]?.let {
        DesignImage(
            R.drawable.coffee_cup, scene, it,
            onClick = if (editable) null else ({ }),
            extraModifier = ext(SceneItemId.COFFEE_CUP)
        )
    }

    // 猫（Agent）。主界面播逐帧动画；编辑器用 cat_idle 首帧静态图。
    items[SceneItemId.CAT]?.let {
        DesignBox(
            scene, it,
            onClick = if (editable) null else onCatTouch,
            tapFeedback = false, // AgentCat 内部已有 punch，避免双重压扁
            extraModifier = ext(SceneItemId.CAT)
        ) {
            if (editable) {
                val frames = rememberFrameIds("cat_idle")
                val firstFrame = frames.firstOrNull() ?: 0
                Image(
                    painter = painterResource(firstFrame),
                    contentDescription = null,
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.FillBounds
                )
            } else {
                catContent()
            }
        }
    }

    // 黑板——主界面点击进个人画像（onBoardClick 由 MainScreen 传入；
    // 未传时保持纯装饰点击但仍有果冻反馈）。编辑器无业务点击。
    items[SceneItemId.BOARD]?.let {
        DesignImage(
            R.drawable.ic_board, scene, it,
            onClick = if (editable) null else (onBoardClick ?: ({ })),
            extraModifier = ext(SceneItemId.BOARD)
        )
    }

    // 笔记本（纯装饰）——最上层装饰物之一
    items[SceneItemId.NOTEBOOK]?.let {
        DesignImage(
            R.drawable.notebook, scene, it,
            onClick = if (editable) null else ({ }),
            extraModifier = ext(SceneItemId.NOTEBOOK)
        )
    }
}

