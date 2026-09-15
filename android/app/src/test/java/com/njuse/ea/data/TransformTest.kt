package com.njuse.ea.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Transform.kt 纯几何回归测试。
 * 固化布局编辑器开发中踩过的坑：瞬移、2 倍速、锚点漂移、旋转耦合、旧数据迁移。
 */
class TransformTest {

    private val eps = 0.001f

    private fun assertNear(expected: Float, actual: Float, msg: String = "") =
        assertTrue("$msg expected=$expected actual=$actual", Math.abs(expected - actual) < eps)

    private fun assertCornerNear(item: ItemLayout, sx: Int, sy: Int, ex: Float, ey: Float, msg: String = "") {
        val (x, y) = item.corner(sx, sy)
        assertNear(ex, x, "$msg cornerX")
        assertNear(ey, y, "$msg cornerY")
    }

    // ---- corner / anchorOf ----

    @Test
    fun `corner at zero rotation is axis aligned`() {
        val it = ItemLayout(cx = 500f, cy = 600f, w = 200f, h = 100f)
        assertCornerNear(it, -1, -1, 400f, 550f, "TL")
        assertCornerNear(it, 1, 1, 600f, 650f, "BR")
    }

    @Test
    fun `corner follows rotation`() {
        // θ=90°：R(90)·(w/2, h/2) = (−h/2, w/2)
        val it = ItemLayout(cx = 500f, cy = 600f, w = 200f, h = 100f, rotation = 90f)
        assertCornerNear(it, 1, 1, 500f - 50f, 600f + 100f, "BR@90")
        assertCornerNear(it, -1, 1, 500f - 50f, 600f - 100f, "BL@90")
    }

    // ---- resize：绝对求解的核心不变量 ----

    @Test
    fun `resize unrotated dragged corner equals finger and anchor fixed`() {
        val it = ItemLayout(cx = 500f, cy = 600f, w = 200f, h = 100f)
        val (ax, ay) = it.anchorOf(-1, -1) // 拖左上角 → 右下角固定
        assertNear(600f, ax); assertNear(650f, ay)
        // 注意手指距离锚点要 ≥ 最小尺寸的一半，否则会触发钳制（钳制行为由专门用例覆盖）。
        val resized = it.resize(-1, -1, ax, ay, fingerX = 350f, fingerY = 500f,
            minW = 140f, maxW = 1600f, minH = 140f, maxH = 2200f)
        // 被拖角严格等于手指（1:1 跟手，不是 2 倍）。
        assertCornerNear(resized, -1, -1, 350f, 500f, "dragged==finger")
        // 对角锚点严格不动。
        assertCornerNear(resized, 1, 1, ax, ay, "anchor fixed")
        assertNear(250f, resized.w); assertNear(150f, resized.h)
    }

    @Test
    fun `resize rotated dragged corner follows finger in screen space`() {
        // θ=90°：捏「屏幕右下方向的角」（局部 (1,-1)），手指向屏幕右移 → 角严格跟手。
        val it = ItemLayout(cx = 500f, cy = 600f, w = 200f, h = 100f, rotation = 90f)
        val (ax, ay) = it.anchorOf(1, -1)
        val resized = it.resize(1, -1, ax, ay, fingerX = 650f, fingerY = 700f,
            minW = 140f, maxW = 1600f, minH = 140f, maxH = 2200f)
        assertCornerNear(resized, 1, -1, 650f, 700f, "dragged==finger@90")
        assertCornerNear(resized, -1, 1, ax, ay, "anchor fixed@90")
        // 旋转角在缩放中保持不变（解耦）。
        assertNear(90f, resized.rotation)
    }

    @Test
    fun `resize clamps to max but anchor stays and reverse drag recovers`() {
        val it = ItemLayout(cx = 500f, cy = 600f, w = 200f, h = 100f)
        val (ax, ay) = it.anchorOf(1, 1) // 拖右下角 → 左上角固定
        // 手指远超上限 → w 钳到 max，锚点仍不动。
        val maxed = it.resize(1, 1, ax, ay, fingerX = 5000f, fingerY = 5000f,
            minW = 140f, maxW = 1600f, minH = 140f, maxH = 2200f)
        assertNear(1600f, maxed.w)
        assertCornerNear(maxed, -1, -1, ax, ay, "anchor fixed at max")
        // 反向拖回（手指往回收）→ 立即可缩小，不卡死。
        val back = maxed.resize(1, 1, ax, ay, fingerX = 700f, fingerY = 700f,
            minW = 140f, maxW = 1600f, minH = 140f, maxH = 2200f)
        assertCornerNear(back, 1, 1, 700f, 700f, "dragged==finger after reverse")
        assertCornerNear(back, -1, -1, ax, ay, "anchor fixed after reverse")
    }

    @Test
    fun `resize clamps to min without drifting`() {
        val it = ItemLayout(cx = 500f, cy = 600f, w = 200f, h = 100f)
        val (ax, ay) = it.anchorOf(-1, -1)
        // 手指穿过锚点向对侧压 → 尺寸钳到 min，不产生整体平移（锚点不动）。
        val mined = it.resize(-1, -1, ax, ay, fingerX = 1000f, fingerY = 1000f,
            minW = 140f, maxW = 1600f, minH = 140f, maxH = 2200f)
        assertNear(140f, mined.w)
        assertNear(140f, mined.h)
        assertCornerNear(mined, 1, 1, ax, ay, "anchor fixed at min")
    }

    // ---- translate / rotateTo ----

    @Test
    fun `translate is screen absolute regardless of rotation`() {
        // 修复「旋转 90° 后向右拖变向上移」的回归：中心制平移只加 dx/dy。
        val rotated = ItemLayout(cx = 500f, cy = 600f, w = 200f, h = 100f, rotation = 90f)
        val moved = rotated.translate(10f, 0f)
        assertNear(510f, moved.cx); assertNear(600f, moved.cy)
    }

    @Test
    fun `rotateTo keeps center`() {
        val it = ItemLayout(cx = 500f, cy = 600f, w = 200f, h = 100f)
        val rotated = it.rotateTo(137.5f)
        assertNear(500f, rotated.cx); assertNear(600f, rotated.cy)
        assertNear(137.5f, rotated.rotation)
    }

    // ---- SceneLayoutCodec：旧数据迁移 ----

    @Test
    fun `v1 top-left json migrates to center`() {
        val v1 = """{"version":1,"items":{"cat":{"x":325.0,"y":1257.0,"w":1048.0,"h":684.0,"rotation":0.0}}}"""
        val parsed = SceneLayoutCodec.parse(v1)
        val cat = parsed.items["cat"]!!
        assertNear(325f + 524f, cat.cx, "migrated cx")
        assertNear(1257f + 342f, cat.cy, "migrated cy")
        assertNear(1048f, cat.w)
    }

    @Test
    fun `v2 json round trips`() {
        val layout = DefaultLayout.effective(null)
        val parsed = SceneLayoutCodec.parse(SceneLayoutCodec.toJson(layout))
        assertEquals(layout.items.size, parsed.items.size)
        for ((id, item) in layout.items) {
            val p = parsed.items[id]!!
            assertNear(item.cx, p.cx, "$id cx"); assertNear(item.cy, p.cy, "$id cy")
            assertNear(item.w, p.w, "$id w"); assertNear(item.h, p.h, "$id h")
            assertNear(item.rotation, p.rotation, "$id rot")
        }
    }

    @Test
    fun `effective fills missing ids from default`() {
        val partial = SceneLayout(items = mapOf(SceneItemId.CAT to ItemLayout(1f, 2f, 100f, 100f)))
        val eff = DefaultLayout.effective(partial)
        assertEquals(DefaultLayout.items.size, eff.items.size)
        assertNear(1f, eff.items[SceneItemId.CAT]!!.cx)
        assertEquals(DefaultLayout.items[SceneItemId.BOARD], eff.items[SceneItemId.BOARD])
    }
}
