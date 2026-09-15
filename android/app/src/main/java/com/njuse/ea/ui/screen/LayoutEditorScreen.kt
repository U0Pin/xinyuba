package com.njuse.ea.ui.screen

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.layout
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInWindow
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.njuse.ea.R
import com.njuse.ea.data.CAFE_H
import com.njuse.ea.data.CAFE_W
import com.njuse.ea.data.DefaultLayout
import com.njuse.ea.data.ItemLayout
import com.njuse.ea.data.SceneItemId
import com.njuse.ea.data.SceneLayout
import com.njuse.ea.data.ScreenMath
import com.njuse.ea.data.SCREEN_H
import com.njuse.ea.data.SCREEN_W
import com.njuse.ea.data.anchorOf
import com.njuse.ea.data.fingerAngleDeg
import com.njuse.ea.data.resize
import com.njuse.ea.data.rotateTo
import com.njuse.ea.data.translate
import com.njuse.ea.ui.screen.scene.SceneItems
import com.njuse.ea.ui.screen.scene.SceneScale
import com.njuse.ea.ui.theme.CreamDim
import com.njuse.ea.ui.theme.CreamWhite
import com.njuse.ea.ui.theme.GlassBorder
import com.njuse.ea.ui.theme.GlassNav
import com.njuse.ea.ui.viewmodel.SettingsViewModel
import com.njuse.ea.ui.widget.ToastBus
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

// 选中框配色 / 尺寸（屏幕 dp，固定不随 scale 变，保证手感一致）。
private val HandleBlue = Color(0xFF2F88FF)
private val HandleWhite = Color(0xFFFFFFFF)
private val CornerSize = 14.dp        // 白色矩形角手柄边长
private val KnobRadius = 13.dp        // 旋转白色小圆半径
private val KnobLift = 20.dp          // 旋转小圆底边距边框上沿的间隙（与线完全脱离）
private const val MIN_SIZE = 140f     // 物品最小设计尺寸
private const val MAX_W = 1600f       // 物品最大设计宽（封顶，保证角始终在屏幕附近可抓回）
private const val MAX_H = 2200f       // 物品最大设计高
private const val BLEED = 120f        // 中心可移动出画布的容差（设计单位，仅用于拖动移动）

/**
 * 布局编辑器：静态场景，无任何业务点击/动画/对话滚动。
 * 点/按物品即选中出变换控件；拖本体=移动（拖动时隐藏控件，松开重现，PS 手感），
 * 拖角=不等比缩放，双指捏合=等比缩放，拖顶部白色小圆=旋转（绝对角度、旋钮跟手）；
 * 点空白取消选中；底部圆形按钮撤销上一步。对话气泡窗口不可编辑。
 *
 * 变换数学全部走 data/Transform.kt 的纯函数（中心制 + 手势开始冻结锚点 + 手指绝对位置反解），
 * 不做增量累加——旋转耦合 / 瞬移 / 漂移一族问题在数学层面消除。
 */
@Composable
fun LayoutEditorScreen(
    onBackClick: () -> Unit,
    viewModel: SettingsViewModel = viewModel()
) {
    val density = LocalDensity.current
    val savedLayout by viewModel.layout.collectAsState()

    var working by remember { mutableStateOf(DefaultLayout.layout) }
    var baseline by remember { mutableStateOf<SceneLayout>(DefaultLayout.layout) }
    // dirty=false 时跟随存储（含异步加载完成）；用户一旦本地编辑就停止跟随，避免被 flow 覆盖。
    var dirty by remember { mutableStateOf(false) }
    LaunchedEffect(savedLayout) {
        if (!dirty) {
            val eff = DefaultLayout.effective(savedLayout)
            working = eff
            baseline = eff
        }
    }

    var selectedId by remember { mutableStateOf<String?>(null) }
    var interacting by remember { mutableStateOf(false) } // 本体拖动中→隐藏控件
    var showExitDialog by remember { mutableStateOf(false) }
    val undoStack = remember { mutableListOf<Pair<String, ItemLayout>>() }
    var canUndo by remember { mutableStateOf(false) }
    var gestureSnapshot by remember { mutableStateOf<ItemLayout?>(null) }

    fun current(id: String): ItemLayout? = working.items[id]

    /**
     * 应用一次变化。[clampCenter]=true（拖动移动/旋转）时用中心边界防丢；
     * =false（角缩放/双指缩放）时**不钳中心**——缩放时中心由锚点几何决定，
     * 钳中心会破坏冻结锚点。缩放只靠 w/h 上下限（在 Transform.resize 内）。
     */
    fun mutate(id: String, clampCenter: Boolean = true, transform: (ItemLayout) -> ItemLayout) {
        val old = working.items[id] ?: return
        dirty = true
        val out = transform(old)
        val new = if (clampCenter) clampItem(out) else out
        working = working.copy(
            items = working.items.toMutableMap().apply { put(id, new) }
        )
    }

    fun beginGesture(id: String) {
        if (gestureSnapshot == null) gestureSnapshot = working.items[id]
    }
    fun endGesture(id: String) {
        val snap = gestureSnapshot
        gestureSnapshot = null
        interacting = false
        if (snap != null && working.items[id] != snap) {
            undoStack.add(id to snap)
            canUndo = undoStack.isNotEmpty()
            viewModel.updateLayout(working)
        }
    }

    fun undo() {
        if (undoStack.isEmpty()) return
        dirty = true
        val (id, prev) = undoStack.removeAt(undoStack.lastIndex)
        working = working.copy(
            items = working.items.toMutableMap().apply { put(id, prev) }
        )
        canUndo = undoStack.isNotEmpty()
        viewModel.updateLayout(working)
    }

    fun requestExit() {
        if (dirty) showExitDialog = true else onBackClick()
    }
    // 系统返回键同样弹出保存确认。
    BackHandler { requestExit() }

    BoxWithConstraints(
        modifier = Modifier.fillMaxSize().background(Color(0xFF2A1E17))
    ) {
        val screenW = maxWidth.value
        val screenH = maxHeight.value
        val scale = if (screenW > 0f && screenH > 0f) {
            maxOf(screenW / CAFE_W, screenH / CAFE_H)
        } else 1f
        val scene = remember(screenW, screenH) { SceneScale(scale, screenW / 2f, screenH / 2f) }
        val pxToDesign = with(density) { 1f / (1.dp.toPx() * scale) } // px → 设计单位
        // 屏幕 px ↔ 设计坐标换算（手势用绝对手指位置，配合 Transform 纯函数）。
        val screenMath = remember(screenW, screenH) {
            ScreenMath(
                scale = scale,
                density = density.density,
                screenWpx = with(density) { screenW.dp.toPx() },
                screenHpx = with(density) { screenH.dp.toPx() }
            )
        }

        // 双指捏合等比缩放（中心固定；单独一层 pointerInput，不拦截单击/单指拖动）。
        // awaitEachGesture + calculateZoom：手势结束（全部手指抬起）必须 endGesture——
        // 快照不清理会污染后续手势的撤销基线，且捏合本身不可撤销。
        Box(
            Modifier
                .fillMaxSize()
                .pointerInput(scale) {
                    awaitEachGesture {
                        awaitFirstDown(requireUnconsumed = false)
                        var pinchId: String? = null
                        do {
                            val e = awaitPointerEvent()
                            val zoom = e.calculateZoom()
                            if (zoom != 1f) {
                                val id = pinchId ?: selectedId
                                if (id != null) {
                                    if (pinchId == null) beginGesture(id)
                                    pinchId = id
                                    mutate(id, clampCenter = false) {
                                        // 中心制：等比缩放只改 w/h，中心天然不动。
                                        it.copy(
                                            w = (it.w * zoom).coerceIn(MIN_SIZE, MAX_W),
                                            h = (it.h * zoom).coerceIn(MIN_SIZE, MAX_H)
                                        )
                                    }
                                }
                            }
                        } while (e.changes.any { it.pressed })
                        if (pinchId != null) endGesture(pinchId)
                    }
                }
        ) {
            // 静态背景，无雨、无视差。
            Image(painterResource(R.drawable.bg_landscape), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
            Image(painterResource(R.drawable.bg_cafe), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
            // 空白点击层：物品/控件优先消费点击，落到底层即取消选中。
            Box(
                Modifier.fillMaxSize()
                    .pointerInput(Unit) { detectTapGestures(onTap = { selectedId = null; interacting = false }) }
            )

            // 5 个场景物品（编辑模式：静态猫、无业务点击），逐物品挂选择/拖动。
            SceneItems(
                layout = working,
                scene = scene,
                editable = true,
                itemExtraModifier = { id ->
                    Modifier
                        // 点击（含长按后松手）= 选中。拖动期间不在这里切换，避免被拖物在拖动中显示控件。
                        // （item-* testTag 由 SceneLayer.SceneItems 统一挂，主界面与编辑器共用。）
                        .pointerInput(id) {
                            detectTapGestures(onTap = { selectedId = id })
                        }
                        .pointerInput(id) {
                            // 自定义拖动：**按下点落在选中物（自己）的手柄/旋钮上时整体让权**
                            //（不消费任何事件）——手柄在最上层，会自然接管缩放/旋转。
                            // 否则本体检测器与手柄检测器进入 slop 竞速，本体获胜会把
                            // interacting 置真、在手势中途销毁手柄节点（历史缺陷）。
                            awaitEachGesture {
                                val down = awaitFirstDown(requireUnconsumed = false)
                                val skip = run {
                                    val cur = current(id) ?: return@run false
                                    if (selectedId != id) return@run false
                                    // lp 是 px → 盒子尺寸也必须是 px（design × scale × density）
                                    val swPx = with(density) { (cur.w * scale).dp.toPx() }
                                    val shPx = with(density) { (cur.h * scale).dp.toPx() }
                                    val hw = swPx / 2f; val hh = shPx / 2f
                                    val lp = down.position // 物品本地 px（未旋转坐标系）
                                    // 4 个角手柄：lp 是「盒子左上角」本地坐标，角点 = (0/swPx, 0/shPx)
                                    val cornerR = with(density) { CornerSize.toPx() } * 1.1f + 14f
                                    var onHandle = false
                                    for (sx in intArrayOf(-1, 1)) for (sy in intArrayOf(-1, 1)) {
                                        val cxLocal = if (sx == 1) swPx else 0f
                                        val cyLocal = if (sy == 1) shPx else 0f
                                        if (Math.abs(lp.x - cxLocal) <= cornerR && Math.abs(lp.y - cyLocal) <= cornerR) {
                                            onHandle = true
                                        }
                                    }
                                    // 旋转旋钮（顶边中点外法向）
                                    val rad = Math.toRadians(cur.rotation.toDouble())
                                    val s = sin(rad).toFloat(); val c = cos(rad).toFloat()
                                    val out = hh + with(density) { KnobLift.toPx() } + with(density) { KnobRadius.toPx() }
                                    val kx = hw + out * s; val ky = hh - out * c
                                    val kr = with(density) { KnobRadius.toPx() } + 14f
                                    val dx2 = lp.x - kx; val dy2 = lp.y - ky
                                    val onKnob = dx2 * dx2 + dy2 * dy2 <= kr * kr
                                    onHandle || onKnob
                                }
                                if (skip) {
                                    // 让权：不消费，直到手势结束
                                    while (true) {
                                        val e = awaitPointerEvent()
                                        if (e.changes.all { !it.pressed }) break
                                    }
                                    return@awaitEachGesture
                                }
                                // 正常本体拖动（屏幕绝对方向；中心制=中心平移）。
                                // 增量必须用 positionChange()：物品随拖动移动、节点坐标系每帧
                                // 都在变，若用「上一事件的本地位置」差分（ch.position − 旧值），
                                // 坐标系位移会混进增量形成交替振荡 = 移动方向抽搐。
                                // positionChange() 的语义即「忽略节点自身移动的屏幕位移」。
                                var dragOver = false
                                while (true) {
                                    val e = awaitPointerEvent()
                                    // 多指：立即中止拖动，让捏合层接管。否则捏合缩放导致节点
                                    // 坐标系变化、单指的本地坐标「假位移」会驱动物品平移（bug）。
                                    // 已发生的位移提交快照；未开始拖动则无需清理。
                                    if (e.changes.count { it.pressed } > 1) {
                                        if (dragOver) endGesture(id)
                                        break
                                    }
                                    val ch = e.changes.firstOrNull() ?: break
                                    if (!ch.pressed) break
                                    if (dragOver) {
                                        // 先读位移再消费：positionChange() 返回的是
                                        // 「位移 − 已消费部分」，consume 之后再读恒为 0
                                        val d = ch.positionChange()
                                        ch.consume()
                                        if (d != Offset.Zero) {
                                            mutate(id) {
                                                it.translate(d.x * pxToDesign, d.y * pxToDesign)
                                            }
                                        }
                                    } else {
                                        // 触发 slop：超过即开始拖动并消费；快照必须先于首次 mutate
                                        val d = ch.position - down.position
                                        if (d.getDistance() > viewConfiguration.touchSlop) {
                                            dragOver = true
                                            selectedId = id
                                            interacting = true
                                            beginGesture(id)
                                            ch.consume()
                                        }
                                    }
                                    if (!ch.pressed) break
                                }
                                if (dragOver) {
                                    endGesture(id) // 松手：压栈撤销 + 落盘 + interacting=false
                                }
                            }
                        }
                }
            )
        }

        // 变换控件覆盖层（选中项且非本体拖动中）。key(sel) 保证切换选中后整体重绑。
        val sel = selectedId
        val selItem = sel?.let { current(it) }
        if (sel != null && selItem != null) {
            key(sel) {
                TransformOverlay(
                    scene = scene,
                    item = selItem,
                    itemProvider = { current(sel) },
                    visible = !interacting,
                    math = screenMath,
                    // 角缩放：不钳中心（中心由锚点几何决定），只靠 w/h 上下限。
                    onResize = { newItem -> mutate(sel, clampCenter = false) { newItem } },
                    // 旋转：中心不动，走中心钳制（无害）。
                    onRotate = { newItem -> mutate(sel) { newItem } },
                    onHandleGestureStart = { beginGesture(sel) },
                    onHandleGestureEnd = { endGesture(sel) },
                    onGestureCancel = { gestureSnapshot = null; interacting = false }
                )
            }
        }

        // 顶部返回按钮（与撤销键同色）。
        Box(
            Modifier.align(Alignment.TopStart).padding(start = 20.dp, top = 45.dp)
                .size(48.dp).clip(RoundedCornerShape(16.dp)).background(GlassNav)
                .border(1.dp, GlassBorder, RoundedCornerShape(16.dp))
                .testTag("editor-back")
                .clickable { requestExit() },
            contentAlignment = Alignment.Center
        ) { Text("←", color = CreamWhite, fontSize = 20.sp) }

        // 底部中心撤销按钮（与左上角退出同色 GlassNav）。
        AnimatedVisibility(visible = canUndo, enter = fadeIn(), exit = fadeOut(),
            modifier = Modifier.align(Alignment.BottomCenter)) {
            Box(
                Modifier.padding(bottom = 40.dp).size(58.dp).clip(CircleShape)
                    .background(GlassNav).border(1.dp, GlassBorder, CircleShape)
                    .testTag("undo")
                    .clickable { undo() },
                contentAlignment = Alignment.Center
            ) {
                Image(
                    painter = painterResource(R.drawable.ic_undo),
                    contentDescription = "撤销",
                    modifier = Modifier.size(28.dp),
                    colorFilter = ColorFilter.tint(CreamWhite)
                )
            }
        }
    }

    // 退出确认：保存并退出 / 不保存并退出（回滚到进入时布局）。
    if (showExitDialog) {
        AlertDialog(
            onDismissRequest = { showExitDialog = false },
            containerColor = Color(0xFF6B4A38),
            titleContentColor = CreamWhite,
            textContentColor = CreamDim,
            title = { Text("退出布局编辑", fontSize = 18.sp) },
            text = { Text("是否保存对布局的更改？", fontSize = 14.sp) },
            confirmButton = {
                TextButton(onClick = {
                    showExitDialog = false
                    viewModel.updateLayout(working)
                    ToastBus.show("布局已保存")
                    onBackClick()
                }) { Text("保存并退出", color = CreamWhite, fontSize = 16.sp) }
            },
            dismissButton = {
                TextButton(onClick = {
                    showExitDialog = false
                    viewModel.updateLayout(baseline) // 回滚本次编辑，不保存退出
                    onBackClick()
                }) { Text("不保存", color = CreamDim, fontSize = 16.sp) }
            }
        )
    }
}

/** 限制物品中心在画布（含 bleed 容差）内，防止拖丢（仅用于纯移动）。 */
private fun clampItem(it: ItemLayout): ItemLayout =
    it.copy(
        cx = it.cx.coerceIn(-BLEED, SCREEN_W + BLEED),
        cy = it.cy.coerceIn(-BLEED, SCREEN_H + BLEED)
    )

/**
 * 选中物的变换控件：蓝色边框 + 4 个白色矩形角手柄 + 顶边白色旋转小圆（内 rotate 图标）。
 *
 * 关键点：
 * - 根节点不旋转、不拦截触摸；蓝框/角块用 Canvas rotate 纯视觉旋转；
 *   角手柄/旋钮命中区用旋转矩阵放到屏幕绝对位置，其余区域穿透。
 * - **角缩放（绝对求解）**：手势开始冻结对角锚点（Transform.anchorOf），之后每帧用
 *   手指**绝对位置**（positionInRoot → ScreenMath → 设计坐标）反解尺寸与中心，
 *   不做增量累加；被拖角 1:1 跟手、对角锚点钉死、与旋转解耦。
 * - **旋转（绝对角度）**：手势开始记录「初始角 − 手指方位角」差值，之后每帧
 *   新角度 = 手指方位角 + 差值——旋钮始终指向手指（PS/Figma 手感），无累积误差。
 */
@Composable
private fun TransformOverlay(
    scene: SceneScale,
    item: ItemLayout,
    itemProvider: () -> ItemLayout?,
    visible: Boolean,
    math: ScreenMath,
    onResize: (ItemLayout) -> Unit,
    onRotate: (ItemLayout) -> Unit,
    onHandleGestureStart: () -> Unit,
    onHandleGestureEnd: () -> Unit,
    onGestureCancel: () -> Unit
) {
    val density = LocalDensity.current
    val scale = scene.scale

    val centerX = scene.dx(item.cx)
    val centerY = scene.dy(item.cy)
    val swPx = with(density) { (item.w * scale).dp.toPx() }
    val shPx = with(density) { (item.h * scale).dp.toPx() }

    Box(
        Modifier
            .offset {
                val offX = (with(density) { centerX.dp.toPx() } - swPx / 2f).roundToInt()
                val offY = (with(density) { centerY.dp.toPx() } - shPx / 2f).roundToInt()
                IntOffset(offX, offY)
            }
            // 显式 layout：控件内容按「物品尺寸×scale」的精确 px 测量并 place(0,0) 顶格——
            // 溢出屏幕也不被钳扁/居中（size/requiredSize 的溢出语义是历史缺陷根源），
            // 命中测试区域=真实完整盒子。
            .layout { measurable, constraints ->
                val w = swPx.roundToInt()
                val h = shPx.roundToInt()
                val p = measurable.measure(
                    Constraints(minWidth = w, maxWidth = w, minHeight = h, maxHeight = h)
                )
                layout(constraints.maxWidth, constraints.maxHeight) { p.place(0, 0) }
            }
    ) {
        if (visible) {
            // 绘制层：旋转到物品姿态。边框 + 角方块（纯视觉）。
            Canvas(Modifier.fillMaxSize()) {
                rotate(degrees = item.rotation, pivot = Offset(size.width / 2f, size.height / 2f)) {
                    drawRect(color = HandleBlue, style = Stroke(width = with(density) { 2.dp.toPx() }))
                    val hc = with(density) { CornerSize.toPx() } / 2f
                    val w = size.width; val h = size.height
                    val pts = listOf(
                        Offset(-hc, -hc), Offset(w - hc, -hc),
                        Offset(-hc, h - hc), Offset(w - hc, h - hc)
                    )
                    for (p in pts) {
                        drawRect(color = HandleWhite, topLeft = p, size = Size(hc * 2f, hc * 2f))
                    }
                }
            }

            // 角手柄命中区：放在旋转层外的屏幕位置（4 个角旋转后的实际位置）。
            data class Corner(val sx: Int, val sy: Int)
            val corners = listOf(Corner(-1, -1), Corner(1, -1), Corner(-1, 1), Corner(1, 1))
            val rad = Math.toRadians(item.rotation.toDouble())
            val cos = cos(rad).toFloat()
            val sin = sin(rad).toFloat()
            for (c in corners) {
                // 本地（未旋转）角相对中心的向量，旋转到屏幕坐标。
                val lx = c.sx * swPx / 2f
                val ly = c.sy * shPx / 2f
                val rx = lx * cos - ly * sin
                val ry = lx * sin + ly * cos
                val targetPx = with(density) { CornerSize.toPx() } * 2.2f // 命中区比视觉大方
                // 命中区节点在窗口中的位置：与 change.position 同源同帧，相加即手指绝对坐标，
                // 不依赖会移动的节点闭包、无一帧滞后反馈。
                var nodePos by remember { mutableStateOf(Offset.Zero) }
                val handleName = when {
                    c.sx < 0 && c.sy < 0 -> "handle-tl"
                    c.sx > 0 && c.sy < 0 -> "handle-tr"
                    c.sx < 0 && c.sy > 0 -> "handle-bl"
                    else -> "handle-br"
                }
                Box(
                    Modifier
                        .offset {
                            IntOffset(
                                (swPx / 2f + rx - targetPx / 2f).roundToInt(),
                                (shPx / 2f + ry - targetPx / 2f).roundToInt()
                            )
                        }
                        // testTag 必须在 offset 之后：放在前面时语义 bounds 不含平移，
                        // 测试读取的位置会错（视觉/命中不受影响）。
                        .testTag(handleName)
                        .size(with(density) { targetPx.toDp() })
                        .onGloballyPositioned { nodePos = it.positionInWindow() }
                        .pointerInput(c.sx, c.sy, math) {
                            // 冻结对角锚点（设计坐标），整次拖拽内绝对钉死。
                            var frozen: FloatArray? = null // [ax, ay]
                            detectDragGestures(
                                onDragStart = {
                                    val cur = itemProvider() ?: return@detectDragGestures
                                    val (ax, ay) = cur.anchorOf(c.sx, c.sy)
                                    frozen = floatArrayOf(ax, ay)
                                    onHandleGestureStart()
                                },
                                onDragEnd = { frozen = null; onHandleGestureEnd() },
                                onDragCancel = { frozen = null; onGestureCancel() }
                            ) { change, _ ->
                                change.consume()
                                val cur = itemProvider() ?: return@detectDragGestures
                                val f = frozen ?: return@detectDragGestures
                                // 手指绝对位置（窗口 px）→ 设计坐标 → 绝对反解尺寸/中心。
                                val p = nodePos + change.position
                                val (fx, fy) = math.screenToDesignPx(p.x, p.y)
                                onResize(
                                    cur.resize(
                                        sx = c.sx, sy = c.sy,
                                        anchorX = f[0], anchorY = f[1],
                                        fingerX = fx, fingerY = fy,
                                        minW = MIN_SIZE, maxW = MAX_W,
                                        minH = MIN_SIZE, maxH = MAX_H
                                    )
                                )
                            }
                        }
                )
            }

            // 旋转小圆：沿“旋转后的顶边中点外法向”摆放，与控件共轴。
            run {
                val knobR = with(density) { KnobRadius.toPx() }
                val lift = with(density) { KnobLift.toPx() }
                // 顶边中点相对中心的屏幕向量 = R(θ)·(0, -sh/2) = (sh/2·sin, -sh/2·cos)。
                val out = shPx / 2f + lift + knobR
                val cnx = swPx / 2f + out * sin
                val cny = shPx / 2f - out * cos
                // 旋钮节点在窗口中的位置（与 change.position 同源同帧 → 手指绝对坐标无滞后）。
                var knobPos by remember { mutableStateOf(Offset.Zero) }
                Box(
                    Modifier
                        .offset {
                            IntOffset(
                                (cnx - knobR).roundToInt(),
                                (cny - knobR).roundToInt()
                            )
                        }
                        .testTag("knob")
                        .size(KnobRadius * 2)
                        .clip(CircleShape)
                        .background(HandleWhite)
                        .onGloballyPositioned { knobPos = it.positionInWindow() }
                        .pointerInput(math) {
                            var grabOffset = 0f
                            detectDragGestures(
                                onDragStart = { local ->
                                    val cur = itemProvider() ?: return@detectDragGestures
                                    // 手指绝对位置 = 旋钮节点窗口坐标 + 节点内局部位置。
                                    val fx = knobPos.x + local.x
                                    val fy = knobPos.y + local.y
                                    val (cpx, cpy) = math.designToScreenPx(cur.cx, cur.cy)
                                    grabOffset = cur.rotation - fingerAngleDeg(fx, fy, cpx, cpy)
                                    onHandleGestureStart()
                                },
                                onDragEnd = { onHandleGestureEnd() },
                                onDragCancel = { onGestureCancel() }
                            ) { change, _ ->
                                change.consume()
                                val cur = itemProvider() ?: return@detectDragGestures
                                // 中心在旋转中不动 → 直接用当前中心；手指方位角 + 冻结差值 = 新角度。
                                val (cpx, cpy) = math.designToScreenPx(cur.cx, cur.cy)
                                val p = knobPos + change.position
                                onRotate(cur.rotateTo(fingerAngleDeg(p.x, p.y, cpx, cpy) + grabOffset))
                            }
                        },
                    contentAlignment = Alignment.Center
                ) {
                    Image(
                        painter = painterResource(R.drawable.ic_rotate),
                        contentDescription = "旋转",
                        modifier = Modifier.size(KnobRadius * 1.2f)
                    )
                }
            }
        }
    }
}
