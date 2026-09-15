package com.njuse.ea.ui.screen

import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.boundsInRoot
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.njuse.ea.R
import com.njuse.ea.data.CAFE_H
import com.njuse.ea.data.CAFE_W
import com.njuse.ea.data.ChatMessage
import com.njuse.ea.data.DefaultLayout
import com.njuse.ea.data.SceneItemId
import com.njuse.ea.ui.device.AgentCat
import com.njuse.ea.ui.device.tapFeedback
import com.njuse.ea.ui.device.RainBox
import com.njuse.ea.ui.device.rememberAgentAnimationController
import com.njuse.ea.ui.device.rememberParallaxOffset
import com.njuse.ea.ui.screen.scene.SceneItems
import com.njuse.ea.ui.screen.scene.SceneScale
import com.njuse.ea.ui.theme.ChatGrey
import com.njuse.ea.ui.theme.MainBrown50
import com.njuse.ea.ui.theme.MainBrown80
import com.njuse.ea.ui.theme.TextLight
import com.njuse.ea.ui.viewmodel.ChatViewModel
import com.njuse.ea.ui.viewmodel.Pending
import com.njuse.ea.ui.viewmodel.ScrollTarget
import com.njuse.ea.ui.viewmodel.SettingsViewModel
import com.njuse.ea.ui.widget.OnboardingOverlay
import com.njuse.ea.ui.widget.ToastBus
import kotlinx.coroutines.delay

// 陀螺仪视差 bleed 比例：让 L1 整层 (cafe_room + 所有物品) 在 X/Y 方向各放大 (1+2rx/ry) 倍，
// 视差位移 clamp 在 ±bleed 内 → café 永远覆盖屏幕，不露背景。
// 取 0.05 → 缩放 1.10，单边 bleed = rx * screenW。
private const val PARALLAX_RX = 0.04f
private const val PARALLAX_RY = 0.015f
// L2（对话气泡）相对 L1 的位移倍数：制造前层深度感。raw 位移与 clamp 都需乘此系数，
// 否则 raw 超过 clamp 范围时气泡会被夹平（"撞墙"），而 L1 仍继续移动 → 视觉脱节。
private const val L2_PARALLAX_DEPTH = 2f

@Composable
fun MainScreen(
    onSettingClick: () -> Unit = {},
    onProfileClick: () -> Unit = {}
) {
    val viewModel: ChatViewModel = viewModel()
    val settingsViewModel: SettingsViewModel = viewModel()
    val isLoading by viewModel.isLoading.collectAsState()
    val messages by viewModel.messages.collectAsState()
    val syncing by viewModel.syncing.collectAsState()
    val pendingStatus by viewModel.pendingStatus.collectAsState()
    val latestAgentReply by viewModel.latestAgentReply.collectAsState()
    val parallaxEnabled by settingsViewModel.parallaxEnabled.collectAsState()
    val rainEnabled by settingsViewModel.rainEnabled.collectAsState()
    // 用户自定义布局（反应式）；未自定义时为默认布局。编辑器保存后此处即时生效。
    val layout by settingsViewModel.layout.collectAsState()
    val sceneLayout = DefaultLayout.effective(layout)
    // 新手引导：onboardingDone 初始为 true（防冷启动竞态），DataStore 发出 false 后弹出。
    // 步骤用 remember（非 rememberSaveable）：每次从设置页返回主界面都从第 0 步重新开始，
    // 否则「重看引导」会恢复上次走到的那一步（rememberSaveable 会跨导航恢复状态）。
    val onboardingDone by settingsViewModel.onboardingDone.collectAsState()
    val showOnboarding = !onboardingDone
    var onboardingStep by remember { mutableStateOf(0) }
    var inputBarRect by remember { mutableStateOf<Rect?>(null) }
    var petSwitcherRect by remember { mutableStateOf<Rect?>(null) }
    var boardRect by remember { mutableStateOf<Rect?>(null) }
    var inputText by rememberSaveable { mutableStateOf("") }
    // 聊天窗 Menu 态：false=对话气泡（Conversation），true=设置/足迹入口卡片（Menu，Figma 153:150）
    var menuOpen by rememberSaveable { mutableStateOf(false) }
    val listState = rememberLazyListState()
    var initialScrollDone by remember { mutableStateOf(false) }

    // Agent 猫咪动画状态机：发送消息->THINKING，点击->TOUCHED，否则 IDLE/IDLE_2。
    // isLoading 翻 true 排队 THINKING，翻 false 取消排队/让其自然播完（只播一次）。
    val animController = rememberAgentAnimationController()
    LaunchedEffect(isLoading) {
        if (isLoading) animController.requestThinking()
        else animController.cancelThinking()
    }

    LaunchedEffect(Unit) {
        viewModel.toast.collect { ToastBus.show(it) }
    }
    // 将每条消息展开为气泡行：Agent 按 \n 自主分段，每段独立 item 以各自参与淡入淡出计算。
    val rows = remember(messages) {
        val out = ArrayList<BubbleRow>(messages.size)
        messages.forEachIndexed { mi, msg ->
            if (msg.role == ChatMessage.Role.USER) {
                out.add(
                    BubbleRow(
                        key = "USER:${msg.timestamp}:0",
                        role = msg.role,
                        text = msg.text,
                        timestamp = msg.timestamp,
                        segIndex = 0,
                        originMsgIndex = mi,
                        showLabel = true
                    )
                )
            } else {
                val segs = msg.text.split("\n").map(String::trim).filter { it.isNotEmpty() }
                val display = if (segs.isEmpty()) listOf(msg.text) else segs
                display.forEachIndexed { si, s ->
                    out.add(
                        BubbleRow(
                            key = "AGENT:${msg.timestamp}:$si",
                            role = msg.role,
                            text = s,
                            timestamp = msg.timestamp,
                            segIndex = si,
                            originMsgIndex = mi,
                            showLabel = si == 0
                        )
                    )
                }
            }
        }
        out
    }
    val rowsState = rememberUpdatedState(rows)

    LaunchedEffect(messages) {
        if (!initialScrollDone && rows.isNotEmpty()) {
            initialScrollDone = true
            val pendingExtra = if (pendingStatus != null) 1 else 0
            listState.scrollToItem(rows.lastIndex + pendingExtra)
        }
    }
    LaunchedEffect(Unit) {
        viewModel.scrollEvent.collect { t ->
            when (t) {
                ScrollTarget.Bottom -> listState.animateScrollToItem(Int.MAX_VALUE)
                is ScrollTarget.ToIndex -> {
                    // ViewModel 给的是 messages 列表索引，这里映射到 rows 中该消息首段所在行。
                    val rowIdx = rowsState.value.indexOfFirst {
                        it.originMsgIndex == t.index && it.segIndex == 0
                    }
                    if (rowIdx >= 0) listState.animateScrollToItem(rowIdx)
                    else listState.animateScrollToItem(Int.MAX_VALUE)
                }
            }
        }
    }

    // 背景直接铺满（Crop），物品用「屏幕中心 + 设计坐标偏移」绝对定位，与 cafe_room 共用
    // 同一个 scale 和原点，因此物品相对 cafe_room 的位置在任何设备上恒定。键盘弹出时不动。
    BoxWithConstraints(
        modifier = Modifier.fillMaxSize().background(MainBrown50)
    ) {
        val screenW = maxWidth.value
        val screenH = maxHeight.value
        // 让 1188×2642 的 cafe_room 图 Crop 覆盖整个屏幕（取宽高较大者）。
        val scale = if (screenW > 0f && screenH > 0f) {
            maxOf(screenW / CAFE_W, screenH / CAFE_H)
        } else 1f
        val scene = SceneScale(scale, screenW / 2f, screenH / 2f)

        // 设计坐标 → 屏幕坐标的换算收敛在 SceneScale 上（dx/dy/dp）。
        val s: (Float) -> androidx.compose.ui.unit.Dp = { scene.dp(it) }

        // 陀螺仪视差：L1 整层（含 cafe_room 和所有 items）通过 graphicsLayer 在中心放大 zoomX/Y
// 倍 → 单边 bleed = (zoom-1)*screen/2；translation = parallax * bleed，clamp 在 ±bleed 内。
// 由于 cafe 与 items 都在 L1 Box 内被同样的 zoom + translation 作用，二者保持天然对齐
// （cat overlay 与 cafe 图里画的 cat-shape 一并缩放、一并平移），不会出现相对位置偏差。
val parallax = rememberParallaxOffset(parallaxEnabled)
val density = LocalDensity.current
val zoomX = 1f + 2f * PARALLAX_RX
val zoomY = 1f + 2f * PARALLAX_RY
val bleedMaxXpx = with(density) { (PARALLAX_RX * screenW).dp.toPx() }
val bleedMaxYpx = with(density) { (PARALLAX_RY * screenH).dp.toPx() }
        // 雨 Canvas 需要像素尺寸用于固定池初始化与回绕边界
        val screenWpx = with(density) { screenW.dp.toPx() }
        val screenHpx = with(density) { screenH.dp.toPx() }

        // ============================================================
        // Z-order：1.Back → 2.cafe_room → 3.Board → 4.Cat → 5.Plant →
        // 6.Notebook → 7.CoffeeCup → 8.Chat
        // 每件物品只写 Figma 设计左上角 (x,y) 和尺寸 (w,h)，换算由 DesignBox/DesignImage 处理。
        // 设置/画像入口统一收口在 8.Chat 顶部的按钮行（物品不再承载入口）。
        // 分层视差：L0=背景(不动)；L1=cafe_room+所有物品(整体平移)；
        //          L2=对话气泡窗口(平移 ×1.3)；底部输入栏不参与视差。
        // ============================================================

        // 1. background（L0：铺满，Crop，永不变形/露边；不参与视差，完全不动）
        Image(
            painter = painterResource(id = R.drawable.bg_landscape),
            contentDescription = null,
            modifier = Modifier.fillMaxSize(),
            contentScale = ContentScale.Crop
        )
        // 1.5 下雨效果：独立静态层，置于 L0 背景之上、L1 视差层之下。
        //    不参与陀螺仪视差（雨是户外远景层）；气泡 LazyColumn、键盘 imePadding 的重组
        //    不会触发本 Canvas 重画（父层 graphicsLayer 隔离）。enabled=false 时协程取消零 CPU。
        RainBox(
            enabled = rainEnabled,
            widthPx = screenWpx,
            heightPx = screenHpx,
            modifier = Modifier.fillMaxSize()
        )
        // 2~7. L1 视差层：cafe_room + 所有场景物品一起作整体 zoom + translation。
//     cafe 用原始的 fillMaxSize+Crop 铺法（保持与 items 的天然对齐），L1 Box 经
//     graphicsLayer 缩放出 bleed 一圈、再按 parallax 平移——cafe 与所有 items 同步
//     变换，相对位置不变。parallax=0 时 zoom*X/Y=1.1 但屏幕中心仍在原位、crop 等比例放大。
        Box(
            modifier = Modifier
                .fillMaxSize()
                .graphicsLayer {
                    val v = parallax.value
                    val txRaw = v.x * bleedMaxXpx
                    val tyRaw = v.y * bleedMaxYpx
                    translationX = if (txRaw.isFinite()) txRaw.coerceIn(-bleedMaxXpx, bleedMaxXpx) else 0f
                    translationY = if (tyRaw.isFinite()) tyRaw.coerceIn(-bleedMaxYpx, bleedMaxYpx) else 0f
                    scaleX = zoomX
                    scaleY = zoomY
                    transformOrigin = androidx.compose.ui.graphics.TransformOrigin.Center
                }
        ) {
            // 2. cafe_room（铺满，Crop；与背景共心所以两图对齐）
            Image(
                painter = painterResource(id = R.drawable.bg_cafe),
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop
            )
            // 3~7. 场景物品（黑板/猫/盆栽/笔记本/咖啡杯），位置来自用户可自定义布局。
            //      黑板恢复「点击进个人画像」入口；同时捕获黑板屏幕 rect 供新手引导挖孔。
            SceneItems(
                layout = sceneLayout,
                scene = scene,
                editable = false,
                onCatTouch = { animController.requestTouched() },
                onBoardClick = onProfileClick,
                catContent = {
                    AgentCat(controller = animController, modifier = Modifier.fillMaxSize())
                },
                itemExtraModifier = { id ->
                    if (id == SceneItemId.BOARD) {
                        Modifier.onGloballyPositioned { boardRect = it.boundsInRoot() }
                    } else Modifier
                }
            )
        }

        // 8. 对话气泡窗口（设计框左上(104,351), 760×1000, overflow-clip；
        //    Agent/Thinking 左对齐，User 右对齐；上下滑动时气泡淡入淡出）
        //    L2 视差：平移 ×2，前层深度感。translationX/Y 不影响 LazyColumn 布局/淡入淡出计算。
        //    窗内纵向结构：顶部爪印按钮（Figma Switcher 153:192）+ 可切换内容区：
        //    Conversation 态=气泡列表，Menu 态=设置/足迹入口卡片（Figma UI 153:150 两变体）。
        //    二者同处本 L2 Box → 视差下按钮与内容天然一起平移。
        val fadeEdgeDp = 150.dp
        val fadeEdgePx = with(density) { fadeEdgeDp.toPx() }
        val minBubbleHeightDp = 50.dp
        val safePaddingDp = (fadeEdgeDp - minBubbleHeightDp).coerceAtLeast(0.dp)
        val safePaddingPx = with(density) { safePaddingDp.toPx() }
        val chatItem = sceneLayout.items[SceneItemId.CHAT_WINDOW]
        val chW = chatItem?.w ?: 760f
        val chH = chatItem?.h ?: 1000f
        Box(
            modifier = Modifier
                .offset(
                    x = (scene.dx(chatItem?.cx ?: 484f) - chW * scale / 2f).dp,
                    y = (scene.dy(chatItem?.cy ?: 851f) - chH * scale / 2f).dp
                )
                .size(width = s(chW), height = s(chH))
                .graphicsLayer {
                    val v = parallax.value
                    val rx = v.x * bleedMaxXpx * L2_PARALLAX_DEPTH
                    val ry = v.y * bleedMaxYpx * L2_PARALLAX_DEPTH
                    translationX = if (rx.isFinite()) rx.coerceIn(
                        -L2_PARALLAX_DEPTH * bleedMaxXpx,
                        L2_PARALLAX_DEPTH * bleedMaxXpx
                    ) else 0f
                    translationY = if (ry.isFinite()) ry.coerceIn(
                        -L2_PARALLAX_DEPTH * bleedMaxYpx,
                        L2_PARALLAX_DEPTH * bleedMaxYpx
                    ) else 0f
                }
        ) {
            Column(modifier = Modifier.fillMaxSize()) {
                // 8.1 爪印按钮 + Conversation/Menu 态切换（气泡列表以插槽传入）
                ChatMenuContent(
                    menuOpen = menuOpen,
                    onToggleMenu = { menuOpen = !menuOpen },
                    onSettings = onSettingClick,
                    onProfile = onProfileClick,
                    scale = scale,
                    onSwitcherPositioned = { petSwitcherRect = it }
                ) {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(vertical = safePaddingDp),
                        verticalArrangement = Arrangement.spacedBy(s(20f))
                    ) {
                    if (messages.isEmpty() && pendingStatus == null) {
                        item(key = "greeting") {
                            SegmentRow(
                                row = BubbleRow(
                                    key = "greeting",
                                    role = ChatMessage.Role.AGENT,
                                    text = latestAgentReply,
                                    timestamp = System.currentTimeMillis(),
                                    segIndex = 0,
                                    originMsgIndex = -1,
                                    showLabel = true
                                ),
                                scale = scale,
                                listState = listState,
                                fadeEdgePx = fadeEdgePx,
                                contentPaddingPx = safePaddingPx,
                                itemKey = "greeting"
                            )
                        }
                    }
                    items(rows, key = { it.key }) { row ->
                        SegmentRow(
                            row = row,
                            scale = scale,
                            listState = listState,
                            fadeEdgePx = fadeEdgePx,
                            contentPaddingPx = safePaddingPx,
                            itemKey = row.key
                        )
                    }
                    pendingStatus?.let { pending ->
                        item(key = "pending") {
                            PendingRow(
                                pending = pending,
                                onRefresh = { viewModel.refresh() },
                                scale = scale,
                                listState = listState,
                                fadeEdgePx = fadeEdgePx,
                                contentPaddingPx = safePaddingPx
                            )
                        }
                    }
                    } // LazyColumn（ChatMenuContent 的气泡插槽）
                } // ChatMenuContent 尾随 lambda
            } // Column（聊天窗内容）
        } // 8. 对话气泡窗口 Box

        // 9. 底部输入栏（special-cased：不进入场景，距底 20、距左右 40；imePadding/nbrsPadding 跟随键盘上弹）
        // Figma Text_Bar(126:84)：外框圆角 80 / 宽 1000 / 高 ≥158 / px40 py35 / gap20；
        // 内套同色 Text_box 圆角 20 pl40 pr200 py10；右端 Send 75×68 @ opacity80 / 8px padding；
        // 文字 56px，文案内边距同设计。仅此元素在键盘弹出时上移。
        // onGloballyPositioned 捕获屏幕 rect 供新手引导挖孔。
        Row(
            modifier = Modifier
                .testTag("input-bar")
                .onGloballyPositioned { inputBarRect = it.boundsInRoot() }
                .align(Alignment.BottomCenter)
                .offset(y = s(-20f))
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = s(40f))
                .width(s(1000f))
                .heightIn(min = s(158f), max = s(400f))
                .clip(RoundedCornerShape(s(80f)))
                .background(MainBrown50)
                .padding(horizontal = s(40f), vertical = s(35f)),
            horizontalArrangement = Arrangement.spacedBy(s(20f)),
            verticalAlignment = Alignment.CenterVertically
        ) {
            BasicTextField(
                value = inputText,
                onValueChange = { inputText = it },
                textStyle = TextStyle(
                    color = TextLight,
                    fontSize = (56f * scale).sp,
                    // 行高与 placeholder 完全一致：否则空态（矮 placeholder 撑起）
                    // 与有文字态（默认行高更高）输入栏高度不一致
                    lineHeight = (56f * scale).sp,
                    lineHeightStyle = LineHeightStyle(
                        alignment = LineHeightStyle.Alignment.Center,
                        trim = LineHeightStyle.Trim.Both
                    )
                ),
                cursorBrush = SolidColor(TextLight),
                modifier = Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(s(20f)))
                    .background(MainBrown50),
                decorationBox = { innerTextField ->
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            // 高度地板：空态（隐形空文本行带字体内边距，较高）与有文字态
                            // （实际行盒较矮）行盒路径不同，不钳高会导致输入栏高度跳变；
                            // 地板须 ≥ 空态自然高(~99)，取 100；多行时仍可继续生长
                            .heightIn(min = s(100f))
                            .padding(horizontal = s(40f), vertical = s(10f)),
                        contentAlignment = Alignment.CenterStart
                    ) {
                        if (inputText.isEmpty()) {
                            Text(
                                text = "有什么想聊的吗？",
                                color = TextLight.copy(alpha = 0.6f),
                                style = TextStyle(
                                    fontSize = (56f * scale).sp,
                                    lineHeight = (56f * scale).sp,
                                    lineHeightStyle = LineHeightStyle(
                                        alignment = LineHeightStyle.Alignment.Center,
                                        trim = LineHeightStyle.Trim.Both
                                    )
                                )
                            )
                        }
                        innerTextField()
                    }
                }
            )

            val sendInteraction = remember { MutableInteractionSource() }
            Image(
                painter = painterResource(id = R.drawable.ic_send),
                contentDescription = "发送",
                modifier = Modifier
                    .testTag("send")
                    .size(width = s(75f), height = s(68f))
                    .alpha(if (isLoading || syncing) 0.3f else 0.8f)
                    .clickable(
                        interactionSource = sendInteraction,
                        indication = null,
                        enabled = !isLoading && !syncing
                    ) {
                        if (inputText.isNotBlank()) {
                            viewModel.sendMessage(inputText)
                            inputText = ""
                        }
                    }
                    .then(
                        if (!isLoading && !syncing)
                            Modifier.tapFeedback(sendInteraction, pressedScale = 0.92f)
                        else Modifier
                    ),
                contentScale = ContentScale.FillBounds
            )
        }

        // 10. 新手引导聚光灯浮层（仅首次/「重看引导」时出现，置于一切之上）
        if (showOnboarding) {
            OnboardingOverlay(
                step = onboardingStep,
                holes = mapOf(
                    1 to inputBarRect,
                    2 to petSwitcherRect,
                    3 to boardRect
                ),
                onNext = {
                    if (onboardingStep >= 3) {
                        settingsViewModel.completeOnboarding()
                    } else {
                        onboardingStep++
                    }
                },
                onSkip = { settingsViewModel.completeOnboarding() }
            )
        }
    }
}

// —— 顶层可组合：历史消息行、pending 占位行、动画省略号文字。
// 它们各自持有 scale，复刻 MainScreen 里的统一缩放（scale = 屏宽/1080）。

/** 计算单个 item 当前的淡入淡出 alpha：气泡边缘离视口顶/底越近越透明，离开视口则消失。
 *  超高 item（高于视口）始终有部分边缘在安全区内，不会消失。 */
@Composable
private fun produceAlpha(
    listState: androidx.compose.foundation.lazy.LazyListState,
    itemKey: Any?,
    fadeEdgePx: Float,
    contentPaddingPx: Float
): Float {
    val alphaState = remember(listState, itemKey, fadeEdgePx, contentPaddingPx) {
        derivedStateOf {
            val info = listState.layoutInfo
            val viewportPx = info.viewportSize.height
            if (viewportPx <= 0 || fadeEdgePx <= 0f) return@derivedStateOf 1f
            val item = info.visibleItemsInfo.firstOrNull { it.key == itemKey }
            if (item == null) return@derivedStateOf 1f
            val itemTop = item.offset.toFloat()
            val itemBottom = itemTop + item.size.toFloat()
            val screenTop = itemTop + contentPaddingPx
            val screenBottom = itemBottom + contentPaddingPx
            val topFade = (screenBottom / fadeEdgePx).coerceIn(0f, 1f)
            val bottomFade = ((viewportPx - screenTop) / fadeEdgePx).coerceIn(0f, 1f)
            topFade.coerceAtMost(bottomFade)
        }
    }
    return alphaState.value
}

/**
 * 气泡：上方「时间+头像」标签行 + 下方气泡容器（圆角 40、内边距 30、
 * 背景 MainBrown50，宽度最大 700 自适应拥抱文本，文本 56px）。
 * Agent/Thinking 对齐到 start，User 对齐到 end。
 */
private data class BubbleRow(
    val key: String,
    val role: ChatMessage.Role,
    val text: String,
    val timestamp: Long,
    val segIndex: Int,
    val originMsgIndex: Int,
    val showLabel: Boolean
)

@Composable
private fun SegmentRow(
    row: BubbleRow,
    scale: Float,
    listState: androidx.compose.foundation.lazy.LazyListState,
    fadeEdgePx: Float,
    contentPaddingPx: Float,
    itemKey: String
) {
    val isUser = row.role == ChatMessage.Role.USER
    val alpha = produceAlpha(listState, itemKey, fadeEdgePx, contentPaddingPx)
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .graphicsLayer { this.alpha = alpha },
        contentAlignment = if (isUser) Alignment.TopEnd else Alignment.TopStart
    ) {
        Column(
            modifier = Modifier.wrapContentWidth(),
            horizontalAlignment = if (isUser) Alignment.End else Alignment.Start
        ) {
            if (row.showLabel) {
                Text(
                    text = row.role.let {
                        val time = java.text.SimpleDateFormat("M.dd HH:mm", java.util.Locale.getDefault())
                            .format(java.util.Date(row.timestamp))
                        if (it == ChatMessage.Role.AGENT) "$time ( =•ω•= )" else "$time 人(`･ω･′)"
                    },
                    color = ChatGrey,
                    fontSize = (40f * scale).sp
                )
                SimpleBubble(
                    text = row.text,
                    scale = scale,
                    modifier = Modifier.padding(top = (5f * scale).dp)
                )
            } else {
                SimpleBubble(text = row.text, scale = scale)
            }
        }
    }
}

/** 单个气泡容器（圆角 40、内边距 30、背景 MainBrown50，宽度最大 700 自适应拥抱文本，文本 56）。 */
@Composable
private fun SimpleBubble(text: String, scale: Float, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .widthIn(max = (700f * scale).dp)
            .clip(RoundedCornerShape((40f * scale).dp))
            .background(MainBrown50)
            .padding(horizontal = (30f * scale).dp, vertical = (30f * scale).dp)
    ) {
        Text(
            text = text,
            color = TextLight,
            fontSize = (56f * scale).sp
        )
    }
}

/**
 * 聊天窗内容区：顶部爪印按钮（Figma Switcher 153:192）+ Conversation/Menu 两态切换
 * （Figma UI 153:150 的 Conversation/Menu 两变体）。位于 L2 气泡层内，
 * 与气泡同处一个 graphicsLayer → 视差下一起平移（结构保证，无需单独处理视差）。
 * Conversation 态显示气泡列表插槽，Menu 态显示「设置/足迹」两个入口卡片；
 * 切换用 Crossfade(tween 200)——有限动画，不触发应用级测试的 idle 死锁。
 * 果冻按压反馈与发送键同款。internal 供组件级测试直接组合（气泡以插槽传入）。
 */
@Composable
internal fun ChatMenuContent(
    menuOpen: Boolean,
    onToggleMenu: () -> Unit,
    onSettings: () -> Unit,
    onProfile: () -> Unit,
    scale: Float,
    modifier: Modifier = Modifier,
    onSwitcherPositioned: (Rect) -> Unit = {},
    bubbles: @Composable () -> Unit
) {
    Column(modifier = modifier.fillMaxSize()) {
        PetSwitcher(
            onClick = onToggleMenu,
            scale = scale,
            modifier = Modifier.padding(start = (6f * scale).dp, top = (20f * scale).dp),
            onPositioned = onSwitcherPositioned
        )
        // 设计稿 Switcher 与下方内容 gap 50
        Crossfade(
            targetState = menuOpen,
            animationSpec = tween(durationMillis = 200),
            modifier = Modifier.fillMaxSize().padding(top = (50f * scale).dp),
            label = "chatMenuSwitch"
        ) { open ->
            if (!open) {
                Box(modifier = Modifier.fillMaxSize()) { bubbles() }
            } else {
                Column(
                    modifier = Modifier.fillMaxSize().padding(start = (6f * scale).dp),
                    verticalArrangement = Arrangement.spacedBy((50f * scale).dp)
                ) {
                    MenuEntryCard(
                        title = "设置",
                        subtitle = "SETTING",
                        iconRes = R.drawable.ic_gear,
                        iconW = 85f,
                        iconH = 85f,
                        tag = "menu-entry-settings",
                        scale = scale,
                        onClick = onSettings
                    )
                    MenuEntryCard(
                        title = "足迹",
                        subtitle = "FOOT PRINT",
                        iconRes = R.drawable.ic_calendar,
                        iconW = 77f,
                        iconH = 85f,
                        tag = "menu-entry-profile",
                        scale = scale,
                        onClick = onProfile
                    )
                }
            }
        }
    }
}

/** 爪印圆形按钮（Figma Switcher 153:192）：120 设计px 圆形，MainBrown80 底，爪印图标 80×76 居中。 */
@Composable
private fun PetSwitcher(
    onClick: () -> Unit,
    scale: Float,
    modifier: Modifier = Modifier,
    onPositioned: (Rect) -> Unit = {}
) {
    val interaction = remember { MutableInteractionSource() }
    Box(
        modifier = modifier
            .testTag("pet-switcher")
            .onGloballyPositioned { onPositioned(it.boundsInRoot()) }
            .size((120f * scale).dp)
            .clip(CircleShape)
            .background(MainBrown80)
            .clickable(
                interactionSource = interaction,
                // 关掉默认 ripple：该设备上 ripple 被渲染成黑色矩形（同 DesignBox/发送键）
                indication = null
            ) { onClick() }
            .tapFeedback(interaction, pressedScale = 0.94f),
        contentAlignment = Alignment.Center
    ) {
        Image(
            painter = painterResource(R.drawable.ic_paw),
            contentDescription = null, // 装饰性图标，语义由菜单卡片文字承载
            modifier = Modifier.size(width = (80f * scale).dp, height = (76f * scale).dp),
            contentScale = ContentScale.FillBounds
        )
    }
}

/**
 * Menu 态单个入口卡片（Figma 153:127/153:118）：MainBrown80 底 + ChatGrey 3px 描边 + 圆角 15，
 * 内容：左竖线 → 双行文字（中文 56 / 英文 40 设计px，纯白）→ 图标。宽度按内容自适应（与设计一致）。
 */
@Composable
private fun MenuEntryCard(
    title: String,
    subtitle: String,
    iconRes: Int,
    iconW: Float,
    iconH: Float,
    tag: String,
    scale: Float,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    val interaction = remember { MutableInteractionSource() }
    Row(
        modifier = modifier
            .testTag(tag)
            .clip(RoundedCornerShape((15f * scale).dp))
            .border(BorderStroke((3f * scale).dp, ChatGrey), RoundedCornerShape((15f * scale).dp))
            .background(MainBrown80)
            .clickable(
                interactionSource = interaction,
                indication = null // 同上：该设备 ripple 渲染成黑色矩形
            ) { onClick() }
            // 入口卡片不加 tapFeedback 弹簧动画（产品要求）
            .padding(
                start = (15f * scale).dp,
                end = (25f * scale).dp,
                // Figma 的 3px 描边画在边界外侧，Compose border 画在内侧 → 垂直 padding 10+3 补偿，
                // 使卡片外轮廓总高与设计一致（131 竖线 + 20 padding + 6 描边 = 157 设计px）
                top = (13f * scale).dp,
                bottom = (13f * scale).dp
            ),
        horizontalArrangement = Arrangement.spacedBy((15f * scale).dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        // 左侧竖线：Figma Line 1（stroke #C5C5C5，旋转后 131 高 × 约 6 宽）
        Box(
            modifier = Modifier
                .size(width = (6f * scale).dp, height = (131f * scale).dp)
                .background(ChatGrey)
        )
        Column {
            Text(
                text = title,
                color = Color.White,
                fontSize = (56f * scale).sp,
                lineHeight = (56f * scale).sp
            )
            Text(
                text = subtitle,
                color = Color.White,
                fontSize = (40f * scale).sp,
                lineHeight = (40f * scale).sp
            )
        }
        Image(
            painter = painterResource(iconRes),
            contentDescription = null, // 装饰性图标，语义由卡片文字承载
            modifier = Modifier.size(width = (iconW * scale).dp, height = (iconH * scale).dp),
            contentScale = ContentScale.FillBounds
        )
    }
}

/** pending 占位行：Agent 思索中/同步中/错误，统一套用气泡外观，左对齐。 */
@Composable
private fun PendingRow(
    pending: Pending,
    onRefresh: () -> Unit,
    scale: Float,
    listState: androidx.compose.foundation.lazy.LazyListState,
    fadeEdgePx: Float,
    contentPaddingPx: Float
) {
    val dpF = { v: Float -> (v * scale).dp }
    val alpha = produceAlpha(listState, "pending", fadeEdgePx, contentPaddingPx)
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .graphicsLayer { this.alpha = alpha },
        horizontalAlignment = Alignment.Start
    ) {
        when (pending) {
            is Pending.Thinking -> {
                Text(text = "( =•ω•= )", color = ChatGrey, fontSize = (40f * scale).sp)
                PendingBubble(scale) {
                    DotsText(base = pending.base, scale = scale)
                }
            }
            is Pending.Syncing -> {
                Text(text = "( =•ω•= )", color = ChatGrey, fontSize = (40f * scale).sp)
                PendingBubble(scale) {
                    DotsText(base = pending.base, scale = scale)
                }
            }
            is Pending.Error -> {
                Text(text = "o_o ....", color = ChatGrey, fontSize = (40f * scale).sp)
                PendingBubble(scale) {
                    Text(
                        text = "喵...似乎有点困（温馨提示：错误码【${pending.code}】请联系管理员喵）",
                        color = TextLight,
                        fontSize = (56f * scale).sp
                    )
                    val refreshInteraction = remember { MutableInteractionSource() }
                    Image(
                        painter = painterResource(id = R.drawable.ic_refresh),
                        contentDescription = "刷新",
                        modifier = Modifier
                            .padding(top = dpF(10f))
                            .size(width = dpF(45f), height = dpF(37f))
                            .clickable(
                                interactionSource = refreshInteraction,
                                indication = null
                            ) { onRefresh() }
                            .tapFeedback(refreshInteraction, pressedScale = 0.92f),
                        contentScale = ContentScale.FillBounds
                    )
                }
            }
        }
    }
}

/** pending 内容气泡容器（与 Agent 气泡外观一致）。 */
@Composable
private fun PendingBubble(scale: Float, content: @Composable () -> Unit) {
    Column(
        modifier = Modifier
            .padding(top = (5f * scale).dp)
            .widthIn(max = (700f * scale).dp)
            .wrapContentWidth()
            .clip(RoundedCornerShape((40f * scale).dp))
            .background(MainBrown50)
            .padding(horizontal = (30f * scale).dp, vertical = (30f * scale).dp),
        horizontalAlignment = Alignment.Start
    ) {
        content()
    }
}

/** 「猫猫思索中.」→「..」→「...」循环。 */
@Composable
private fun DotsText(base: String, scale: Float, modifier: Modifier = Modifier) {
    var n by remember { mutableStateOf(1) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(500)
            n = (n % 3) + 1
        }
    }
    Text(
        text = base + ".".repeat(n),
        color = TextLight,
        fontSize = (56f * scale).sp,
        modifier = modifier
    )
}