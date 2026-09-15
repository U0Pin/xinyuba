package com.njuse.ea.ui

import android.os.SystemClock
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.click
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.pinch
import com.njuse.ea.data.DefaultLayout
import com.njuse.ea.data.ItemLayout
import com.njuse.ea.data.LayoutStore
import com.njuse.ea.data.SceneItemId
import com.njuse.ea.data.SceneLayout
import com.njuse.ea.ui.screen.LayoutEditorScreen
import com.njuse.ea.ui.viewmodel.SettingsViewModel
import kotlinx.coroutines.runBlocking
import kotlin.math.atan2

import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 布局编辑器全面交互测试（真实组合 LayoutEditorScreen + 真实 DataStore）。
 *
 * 坐标系注记：模拟器 1080×2400 @420dpi，设计→屏幕换算 unit = 1080/1188 ≈ 0.90909。
 * 断言一律「物品节点 boundsInRoot 的实际中心」为基准（对窗口内嵌/insets 自洽），
 * 数值状态以 DataStore 落盘轮询为准（viewModel 异步写）。
 */
@RunWith(AndroidJUnit4::class)
class LayoutEditorEditTest {

    @get:Rule
    val compose = createComposeRule()

    private val app = ApplicationProvider.getApplicationContext<android.app.Application>()
    private val store = LayoutStore(app)
    private val vm get() = SettingsViewModel(app)

    private val u = 1080f / 1188f // px per design unit

    private fun node(tag: String) = compose.onNodeWithTag(tag, useUnmergedTree = true)
    private fun centerOf(tag: String): Offset {
        val b = node(tag).fetchSemanticsNode().boundsInRoot
        return Offset(b.center.x, b.center.y)
    }

    private fun seedCat(cat: ItemLayout) = runBlocking {
        val items = DefaultLayout.layout.items.toMutableMap()
        items[SceneItemId.CAT] = cat
        store.save(SceneLayout(version = 2, items = items))
        Unit
    }

    private fun launchEditor() {
        compose.setContent {
            LayoutEditorScreen(onBackClick = {}, viewModel = vm)
        }
        compose.waitUntil(5000) {
            compose.onAllNodesWithTag("item-cat", useUnmergedTree = true)
                .fetchSemanticsNodes().isNotEmpty()
        }
        compose.waitForIdle()
    }

    private fun selectCat() {
        node("item-cat").performTouchInput { click(center) }
        compose.waitForIdle()
        compose.waitUntil(3000) {
            compose.onAllNodesWithTag("handle-tl", useUnmergedTree = true)
                .fetchSemanticsNodes().isNotEmpty()
        }
        compose.waitForIdle()
    }

    private fun catNow(): ItemLayout = runBlocking { store.load() }.items.getValue(SceneItemId.CAT)

    private fun awaitCat(timeoutMs: Long = 3000, pred: (ItemLayout) -> Boolean): ItemLayout {
        val deadline = SystemClock.elapsedRealtime() + timeoutMs
        var last = catNow()
        while (SystemClock.elapsedRealtime() < deadline) {
            if (pred(last)) return last
            SystemClock.sleep(50)
            last = catNow()
        }
        return last
    }

    private fun expectNear(actual: Float, expected: Float, tol: Float, what: String) {
        assertTrue("$what: got $actual expect $expected (±$tol)", Math.abs(actual - expected) <= tol)
    }

    // ---------------------------------------------------------------- pinch

    @Test
    fun pinchIn_growsProportionally_centerFixed_capsAtMax() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        val c = centerOf("item-cat")
        // 手指距离 200px → 500px = zoom 2.5：w 2620 → 封顶 1600，h 684×2.5 = 1710
        compose.onRoot().performTouchInput {
            pinch(c + Offset(-100f, 0f), c + Offset(-250f, 0f), c + Offset(100f, 0f), c + Offset(250f, 0f))
        }
        compose.waitForIdle()
        val l = awaitCat { it.w > 1590f }
        expectNear(l.w, 1600f, 2f, "w cap")
        expectNear(l.h, 1710f, 10f, "h proportional")
        expectNear(l.cx, 849f, 0.5f, "cx must not drift")
        expectNear(l.cy, 1599f, 0.5f, "cy must not drift")
        assertTrue("rotation must stay", l.rotation == 0f)
    }

    @Test
    fun pinchOut_clampsAtMin_centerFixed() {
        seedCat(ItemLayout(cx = 540f, cy = 1200f, w = 300f, h = 250f, rotation = 0f, visible = true))
        launchEditor()
        selectCat()
        val c = centerOf("item-cat")
        // 500px → 220px ≈ zoom 0.44：w/h 双双钳到 MIN 140
        compose.onRoot().performTouchInput {
            pinch(c + Offset(-250f, 0f), c + Offset(-110f, 0f), c + Offset(250f, 0f), c + Offset(110f, 0f))
        }
        compose.waitForIdle()
        val l = awaitCat { it.w < 145f }
        expectNear(l.w, 140f, 2f, "w min clamp")
        expectNear(l.h, 140f, 2f, "h min clamp")
        expectNear(l.cx, 540f, 0.5f, "cx")
        expectNear(l.cy, 1200f, 0.5f, "cy")
    }

    @Test
    fun pinchAtCap_staysCap() {
        seedCat(ItemLayout(cx = 540f, cy = 1200f, w = 1500f, h = 700f, rotation = 0f, visible = true))
        launchEditor()
        selectCat()
        val c = centerOf("item-cat")
        compose.onRoot().performTouchInput {
            pinch(c + Offset(-100f, 0f), c + Offset(-250f, 0f), c + Offset(100f, 0f), c + Offset(250f, 0f))
        }
        compose.waitForIdle()
        var l = awaitCat { it.h > 1700f }
        expectNear(l.w, 1600f, 2f, "w cap 1st")
        expectNear(l.h, 1750f, 10f, "h 1st")
        // 第二次继续外扩：w 保持 1600，h 封到 2200
        compose.onRoot().performTouchInput {
            pinch(c + Offset(-100f, 0f), c + Offset(-250f, 0f), c + Offset(100f, 0f), c + Offset(250f, 0f))
        }
        compose.waitForIdle()
        l = awaitCat { it.h > 2150f }
        expectNear(l.w, 1600f, 2f, "w cap 2nd")
        expectNear(l.h, 2200f, 2f, "h cap 2nd")
        expectNear(l.cx, 540f, 0.5f, "cx")
    }

    @Test
    fun pinchThenUndo_revertsSize() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        val c = centerOf("item-cat")
        compose.onRoot().performTouchInput {
            pinch(c + Offset(-100f, 0f), c + Offset(-125f, 0f), c + Offset(100f, 0f), c + Offset(125f, 0f))
        }
        compose.waitForIdle()
        val l = awaitCat { it.w > 1290f }
        expectNear(l.w, 1310f, 8f, "w after pinch")
        node("undo").performClick()
        compose.waitForIdle()
        val l2 = awaitCat { Math.abs(it.w - 1048f) < 2f }
        expectNear(l2.w, 1048f, 2f, "w after undo")
        expectNear(l2.h, 684f, 2f, "h after undo")
    }

    // ------------------------------------------------------- drag + pinch chain

    /** 边界：混合手势的撤销按逆序逐层回退（先退捏合尺寸、再退拖动位置），互不污染 */
    @Test
    fun dragThenPinch_undoChainIntact() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        val c0 = centerOf("item-cat")
        // 单指拖 +270px（首个 30px 为 slop 触发段，不生效）
        node("item-cat").performTouchInput {
            down(center)
            moveBy(Offset(30f, 0f))
            moveBy(Offset(270f, 0f))
            up()
        }
        compose.waitForIdle()
        val cxAfterDrag = awaitCat { it.cx > 1100f }.cx
        expectNear(cxAfterDrag, 849f + 270f / u, 8f, "cx after drag")
        // 捏合 zoom 1.25
        compose.onRoot().performTouchInput {
            pinch(c0 + Offset(-100f, 0f), c0 + Offset(-125f, 0f), c0 + Offset(100f, 0f), c0 + Offset(125f, 0f))
        }
        compose.waitForIdle()
        val l = awaitCat { it.w > 1290f }
        expectNear(l.w, 1310f, 8f, "w after pinch")
        expectNear(l.cx, cxAfterDrag, 0.5f, "pinch must not move item")
        // 撤销：先退捏合，再退拖动
        node("undo").performClick()
        compose.waitForIdle()
        val l2 = awaitCat { Math.abs(it.w - 1048f) < 2f }
        expectNear(l2.cx, cxAfterDrag, 0.5f, "undo pinch keeps drag")
        node("undo").performClick()
        compose.waitForIdle()
        val l3 = awaitCat { Math.abs(it.cx - 849f) < 2f }
        expectNear(l3.cx, 849f, 2f, "undo drag")
    }

    @Test
    fun secondFingerDuringDrag_abortsDrag_pinchTakesOver() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        val c = centerOf("item-cat")
        node("item-cat").performTouchInput {
            down(0, center)
            moveBy(0, Offset(30f, 0f))        // 单指拖动已触发（slop 段，无位移）
            down(1, center + Offset(60f, 0f)) // 第二指落下 → 拖动必须中止
            moveTo(0, c + Offset(-100f, 0f))  // 外扩捏合（指针0 远移，指针1 不动）
            up(0)
            up(1)
        }
        compose.waitForIdle()
        val l = awaitCat { it.w > 1590f }
        expectNear(l.w, 1600f, 2f, "w pinch after abort")
        // 关键：被中止的拖动不得造成任何平移
        expectNear(l.cx, 849f, 0.5f, "cx no drift from aborted drag")
        expectNear(l.cy, 1599f, 0.5f, "cy no drift")
    }

    // ---------------------------------------------------------------- drag

    @Test
    fun singleFingerDrag_movesOneToOne() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        node("item-cat").performTouchInput {
            down(center)
            moveBy(Offset(30f, 0f))
            moveBy(Offset(300f, 0f))
            up()
        }
        compose.waitForIdle()
        val l = awaitCat { it.cx > 1100f }
        expectNear(l.cx, 849f + 300f / u, 8f, "cx 1:1")
        expectNear(l.cy, 1599f, 1f, "cy unchanged")
    }

    // ------------------------------------------------------- corner resize

    @Test
    fun cornerResizeTl_cornerFollows_anchorFixed() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        val brBefore = centerOf("handle-br")
        val tlBefore = centerOf("handle-tl")
        node("handle-tl").performTouchInput {
            down(center)
            moveBy(Offset(-110f, -55f))
            up()
        }
        compose.waitForIdle()
        val l = awaitCat { it.w > 1100f }
        // 角位移 −110px,−55px → 设计 −121,−60.5；TL 拖动只放大对应边
        expectNear(l.w, 1048f + 121f, 8f, "w")
        expectNear(l.h, 684f + 60.5f, 8f, "h")
        expectNear(l.cx, 849f - 121f / 2f, 6f, "cx half-shift")
        expectNear(l.cy, 1599f - 60.5f / 2f, 6f, "cy half-shift")
        val brAfter = centerOf("handle-br")
        expectNear(brAfter.x, brBefore.x, 3f, "anchor BR x fixed")
        expectNear(brAfter.y, brBefore.y, 3f, "anchor BR y fixed")
        val tlAfter = centerOf("handle-tl")
        expectNear(tlAfter.x, tlBefore.x - 110f, 8f, "TL corner follows finger x")
        expectNear(tlAfter.y, tlBefore.y - 55f, 8f, "TL corner follows finger y")
    }

    // ------------------------------------------------------- rotate

    @Test
    fun rotateKnob_absoluteFollow_centerFixed() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        val ic = centerOf("item-cat")
        val kc = centerOf("knob")
        // 分段位移（模拟真实手指多事件）：首个事件跨 slop 时 onDragStart 拿到的是该事件
        // 位置，grabOffset 基于它；最终角度 = a(终点) − a(首段)。单事件大位移会恒转 0。
        node("knob").performTouchInput {
            down(center)
            moveBy(Offset(-50f, -10f))
            moveBy(Offset(-50f, -10f))
            moveBy(Offset(-50f, -10f))
            moveBy(Offset(-50f, -10f))
            moveBy(Offset(-100f, -20f))
            up()
        }
        compose.waitForIdle()
        val pStart = kc + Offset(-50f, -10f)
        val pEnd = kc + Offset(-300f, -60f)
        val a0 = Math.toDegrees(atan2(pStart.y - ic.y, pStart.x - ic.x).toDouble())
        val a1 = Math.toDegrees(atan2(pEnd.y - ic.y, pEnd.x - ic.x).toDouble())
        val expected = (a1 - a0).toFloat()
        val l = awaitCat { Math.abs(it.rotation) > 10f }
        expectNear(l.rotation, expected.toFloat(), 6f, "rotation absolute follow")
        expectNear(l.cx, 849f, 0.5f, "cx")
        expectNear(l.cy, 1599f, 0.5f, "cy")
    }

    // ------------------------------------------------------- oversized geometry

    @Test
    fun oversizedItem_handlesNotClampedOrCentered() {
        seedCat(ItemLayout(cx = 1200f, cy = 1500f, w = 1600f, h = 609f, rotation = 0f, visible = true))
        launchEditor()
        selectCat()
        val ic = centerOf("item-cat")
        val tl = centerOf("handle-tl")
        expectNear(tl.x, ic.x - 1600f * u / 2f, 8f, "TL x unclamped")
        expectNear(tl.y, ic.y - 609f * u / 2f, 8f, "TL y")
        val br = centerOf("handle-br")
        // 物品大于屏幕：BR 手柄必须真的溢出屏幕右侧（不钳到屏幕边、不向中心收拢）
        assertTrue("BR must overflow screen: ${br.x}", br.x > 1080f)
        expectNear(br.x, ic.x + 1600f * u / 2f, 8f, "BR x")
        expectNear(br.y, ic.y + 609f * u / 2f, 8f, "BR y")
    }

    @Test
    fun baseline_select_showsGizmoMatchingItem() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        val ic = centerOf("item-cat")
        val tl = centerOf("handle-tl")
        val br = centerOf("handle-br")
        val knob = centerOf("knob")
        expectNear(tl.x, ic.x - 1048f * u / 2f, 8f, "tl x")
        expectNear(tl.y, ic.y - 684f * u / 2f, 8f, "tl y")
        expectNear(br.x, ic.x + 1048f * u / 2f, 8f, "br x")
        expectNear(br.y, ic.y + 684f * u / 2f, 8f, "br y")
        // rot=0：旋钮中心在顶边中点外法向 lift+r 处（半径 r 只算一次）
        expectNear(knob.x, ic.x, 8f, "knob x")
        expectNear(knob.y, ic.y - 684f * u / 2f - 52.5f - 34.1f, 12f, "knob y")
        expectNear(catNow().rotation, 0f, 0.5f, "rot")
    }

    @Test
    fun blankTap_deselects() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        node("handle-tl").assertExists()
        compose.onRoot().performTouchInput { click(Offset(500f, 300f)) }
        compose.waitForIdle()
        node("handle-tl").assertDoesNotExist()
    }

    // ------------------------------------------------------- save / discard

    @Test
    fun backSave_persistsToDataStore() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        node("item-cat").performTouchInput {
            down(center)
            moveBy(Offset(30f, 0f))
            moveBy(Offset(270f, 0f))
            up()
        }
        compose.waitForIdle()
        val expectedCx = awaitCat { it.cx > 1100f }.cx
        node("editor-back").performClick()
        compose.waitForIdle()
        compose.onNodeWithText("保存并退出").performClick()
        compose.waitForIdle()
        val l = awaitCat { Math.abs(it.cx - expectedCx) < 3f }
        expectNear(l.cx, expectedCx, 3f, "saved cx")
    }

    @Test
    fun backDiscard_restoresBaseline() {
        seedCat(DefaultLayout.layout.items.getValue(SceneItemId.CAT))
        launchEditor()
        selectCat()
        node("item-cat").performTouchInput {
            down(center)
            moveBy(Offset(30f, 0f))
            moveBy(Offset(270f, 0f))
            up()
        }
        compose.waitForIdle()
        awaitCat { it.cx > 1100f }
        node("editor-back").performClick()
        compose.waitForIdle()
        compose.onNodeWithText("不保存").performClick()
        compose.waitForIdle()
        val l = awaitCat { it.cx < 900f }
        expectNear(l.cx, 849f, 3f, "baseline cx")
        expectNear(l.w, 1048f, 3f, "baseline w")
    }
}
