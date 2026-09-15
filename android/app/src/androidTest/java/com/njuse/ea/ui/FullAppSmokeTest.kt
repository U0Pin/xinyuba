package com.njuse.ea.ui

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.click
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.Configurator
import androidx.test.uiautomator.UiDevice
import com.njuse.ea.MainActivity
import com.njuse.ea.data.ItemLayout
import com.njuse.ea.data.LayoutStore
import com.njuse.ea.data.OnboardingStore
import com.njuse.ea.data.SceneItemId
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 应用级 E2E：真实启动 MainActivity（完整 NavHost/主题/动画栈），验证
 * 「装包后能启动、真实跨页导航、编辑跨页持久化」的黑盒链路。
 *
 * 与组件级测试（LayoutEditorEditTest）的分工：这里管启动与跨页，细粒度手势断言在那边。
 *
 * 重要约束：
 * - 主界面有无限帧动画（雨/猫 idle），**任何会等 compose idle 的操作**（performClick/
 *   performTouchInput/waitForIdle）在主界面都会死等超时——主界面交互一律用
 *   UiAutomator 系统注入（device.click，坐标取自语义 bounds；MainActivity
 *   edge-to-edge 全屏，boundsInRoot 即屏幕坐标）。
 * - 编辑器/设置页是静态界面，可安全使用 compose finder + performTouchInput。
 * - 区分页面：主界面与编辑器**共用** item-* tag，编辑器特有 editor-back tag；
 *   设置页以文本「编辑」为特征。判断「在哪页」必须用页面特有特征。
 * - 不用 pm clear（会杀 instrumentation 所在进程）；@Before 直接重置 DataStore，
 *   反应式 flow 会同步到之后进入的页面。
 */
@RunWith(AndroidJUnit4::class)
class FullAppSmokeTest {

    @get:Rule
    val compose = createAndroidComposeRule<MainActivity>()

    private val app = ApplicationProvider.getApplicationContext<android.app.Application>()
    private val store = LayoutStore(app)
    private val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())

    private val u = 1080f / 1188f // px per design unit（1080×2400@420dpi）

    private fun node(tag: String) = compose.onNodeWithTag(tag, useUnmergedTree = true)
    private fun centerOf(tag: String): Offset {
        val b = node(tag).fetchSemanticsNode().boundsInRoot
        return Offset(b.center.x, b.center.y)
    }

    // 帧时钟手动模式下手动推帧；auto 模式下 no-op（advanceTimeBy 会抛异常）
    private fun tick(ms: Long = 120) {
        if (!compose.mainClock.autoAdvance) compose.mainClock.advanceTimeBy(ms)
    }

    /**
     * autoAdvance=false 冻结了帧时钟 → 重组（导航/数据到达）必须手动推帧才会渲染。
     * advanceTimeBy 有界推进（无限动画不会死循环）；轮询直到 tag 出现。
     */
    private fun awaitTag(tag: String, timeout: Long = 8000) {
        val deadline = System.currentTimeMillis() + timeout
        while (System.currentTimeMillis() < deadline) {
            tick()
            if (compose.onAllNodesWithTag(tag, useUnmergedTree = true)
                    .fetchSemanticsNodes().isNotEmpty()
            ) return
            Thread.sleep(60)
        }
        throw AssertionError("等待 tag 超时: $tag")
    }
    private fun awaitEditor() = awaitTag("editor-back")
    private fun awaitSettings() {
        val deadline = System.currentTimeMillis() + 8000
        while (System.currentTimeMillis() < deadline) {
            tick()
            if (compose.onAllNodesWithText("编辑", useUnmergedTree = true)
                    .fetchSemanticsNodes().isNotEmpty()
            ) return
            Thread.sleep(60)
        }
        throw AssertionError("等待设置页超时")
    }
    // 画像页特征文本（仅画像页有「个人画像」标题；设置页弹窗含「画像」但不含全词）
    private fun awaitProfile() {
        val deadline = System.currentTimeMillis() + 8000
        while (System.currentTimeMillis() < deadline) {
            tick()
            if (compose.onAllNodesWithText("个人画像", useUnmergedTree = true)
                    .fetchSemanticsNodes().isNotEmpty()
            ) return
            Thread.sleep(60)
        }
        throw AssertionError("等待画像页超时")
    }

    private fun catNow(): ItemLayout = runBlocking { store.load() }.items.getValue(SceneItemId.CAT)
    private fun awaitCat(timeoutMs: Long = 3000, pred: (ItemLayout) -> Boolean): ItemLayout {
        val deadline = System.currentTimeMillis() + timeoutMs
        var last = catNow()
        while (System.currentTimeMillis() < deadline) {
            if (pred(last)) return last
            Thread.sleep(60)
            last = catNow()
        }
        return last
    }

    @Before
    fun resetLayout() {
        // 主界面有无限帧动画（雨/猫），compose idle 机制永不空闲 → 关闭帧时钟自动
        // 推进让其立即 idle（动画视觉冻结不影响断言；数据加载走真实协程，事件
        // 注入不依赖帧时钟）。必须在任何 finder/waitUntil 之前设置。
        compose.mainClock.autoAdvance = false
        // UiAutomator 默认等 UI idle 才注入——主界面无限动画下永远等不到，
        // 会抛 "Failed to inject touch input"。把 idle 等待设为 0（立即注入）。
        val cfg = Configurator.getInstance()
        cfg.waitForIdleTimeout = 0
        cfg.actionAcknowledgmentTimeout = 0
        runBlocking {
            store.reset()
            // 标记新手引导已完成：否则首次启动的聚光灯浮层会盖住主界面，
            // 拦截现有 E2E 的点击/断言
            OnboardingStore(app).markDone()
        }
    }

    // 主界面 → 点聊天窗顶部「设置」按钮 → 设置页（物品入口已移除，此为唯一设置链路）。
    // 主界面有动画：不用 compose 注入（会死等 idle）；
    // 也不用 UiAutomator 的注入器（与 compose test 框架冲突，Failed to inject），
    // 走 shell input 系统注入（同 adb input tap，与 idle 无关）。
    private fun tap(x: Float, y: Float) {
        device.executeShellCommand("input tap ${x.toInt()} ${y.toInt()}")
    }

    private fun navigateToSettings() {
        openChatMenu()
        val c = centerOf("menu-entry-settings")
        tap(c.x, c.y)
        awaitSettings()
    }

    // 主界面 → 点爪印按钮切入 Menu 态（显示设置/足迹入口卡片）。
    // Crossfade(200ms) 过渡期两态语义并存，先推完过渡帧再取坐标，避免取到半透明 outgoing 节点。
    private fun openChatMenu() {
        awaitTag("pet-switcher")
        val c = centerOf("pet-switcher")
        tap(c.x, c.y)
        tick(300)
        awaitTag("menu-entry-settings")
        tick(300)
    }

    // 设置页 → 点「编辑」→ 编辑器。编辑器是静态页，进入后恢复帧时钟自动推进，
    // 否则 compose 的触摸注入（手势协程等帧）会失败（Failed to inject touch input）。
    private fun navigateToEditor() {
        val edit = device.findObject(By.text("编辑"))
        assertTrue("设置页应有「编辑」入口", edit != null)
        val b = edit.visibleBounds
        tap(b.exactCenterX(), b.exactCenterY())
        awaitEditor()
        compose.mainClock.autoAdvance = true
    }

    @Test
    fun appLaunches_mainSceneRenders() {
        // 真实启动链路：MainActivity → EaApp() → NavHost main → 场景数据驱动渲染
        awaitTag("item-cat")
        awaitTag("item-notebook")
        awaitTag("item-board")
        assertTrue(device.displayWidth >= 1080)
    }

    // 主界面 → 爪印按钮 → Menu 态「设置」卡片 → 设置页（navigateToSettings 同链路，独立断言兜底）
    @Test
    fun chatMenu_navigateToSettings() {
        openChatMenu()
        val c = centerOf("menu-entry-settings")
        tap(c.x, c.y)
        awaitSettings()
    }

    // 主界面 → 爪印按钮 → Menu 态「足迹」卡片 → 画像页（画像入口现由足迹卡片承接）
    @Test
    fun chatMenu_navigateToProfile() {
        openChatMenu()
        val c = centerOf("menu-entry-profile")
        tap(c.x, c.y)
        awaitProfile()
    }

    // 双击返回键（第二次落在导航过渡期内）不应把栈底 main 弹掉导致黑屏。
    // 判据：双击后主界面爪印按钮仍渲染。曾在 popBackStack 无守卫时稳定复现黑屏。
    @Test
    fun doubleTapBack_returnsToMain_notBlackScreen() {
        navigateToSettings()
        val back = node("settings-back").fetchSemanticsNode().boundsInRoot.center
        // 两条 tap 塞进同一 shell 命令，尽量缩小两次点击的间隔
        tap(back.x, back.y)
        tap(back.x, back.y)
        awaitTag("pet-switcher")
    }

    @Test
    fun navigate_toEditor_andSelectShowsGizmo() {
        navigateToSettings()
        navigateToEditor()
        // 选中物品 → 变换控件出现
        node("item-cat").performTouchInput { click(center) }
        awaitTag("handle-tl")
    }

    @Test
    fun crossPage_persistence_editSurvivesNavigation() {
        navigateToSettings()
        navigateToEditor()
        // 选中并拖动（编辑器是静态界面，compose 注入安全）
        node("item-cat").performTouchInput { click(center) }
        awaitTag("handle-tl")
        node("item-cat").performTouchInput {
            down(center)
            moveBy(Offset(30f, 0f))
            moveBy(Offset(270f, 0f))
            up()
        }
        val moved = awaitCat { it.cx > 1100f }
        val expectedCenterX = 540f + (moved.cx - 540f) * u
        // 返回 → 保存并退出（真实弹窗交互）→ 真实 popBackStack 回设置页
        node("editor-back").performClick()
        compose.onNodeWithText("保存并退出").performClick()
        awaitSettings()
        // 重新进入编辑器，位置应保持
        navigateToEditor()
        val after = centerOf("item-cat")
        assertTrue(
            "重进后位置应保持: got ${after.x} expect $expectedCenterX",
            Math.abs(after.x - expectedCenterX) <= 12f
        )
    }
}
