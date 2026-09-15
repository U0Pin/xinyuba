package com.njuse.ea.ui.device

import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/**
 * ParallaxCurve 纯逻辑测试（通道 1）。
 *
 * 判据集：中心灵敏度与曲线引入前一致、边缘趋硬、幅度减半、处处压缩、奇对称单调、
 * 越界精确 clamp、符号不翻转，以及 shape(0f) 逐位为 0（死区分支行为不变的依据）。
 */
class ParallaxCurveTest {

    private val scale = ParallaxCurve.AMPLITUDE_SCALE

    private fun assertExactly(expected: Float, actual: Float, msg: String) =
        assertTrue("$msg expected=$expected actual=$actual", expected == actual)

    /** 有限差分增益：中心应为 1.0（= 2 × AMPLITUDE_SCALE），边缘趋 0。 */
    private fun gain(v: Float, h: Float = 1e-3f) =
        (ParallaxCurve.shape(v + h) - ParallaxCurve.shape(v)) / h

    @Test
    fun `zero and endpoints are exact`() {
        // 0 逐位为 0：死区分支（error=0）经曲线后必须仍是 0，否则死区行为会变
        assertExactly(0f, ParallaxCurve.shape(0f), "shape(0)")
        assertExactly(scale, ParallaxCurve.shape(1f), "shape(1)")
        assertExactly(-scale, ParallaxCurve.shape(-1f), "shape(-1)")
        // AMPLITUDE_SCALE=0.5 时中心增益 2×0.5=1.0，即小幅倾斜灵敏度与引入曲线前一致
        assertExactly(1f, 2f * scale, "中心增益 = 2 × AMPLITUDE_SCALE")
    }

    @Test
    fun `input beyond full deflection clamps exactly`() {
        assertExactly(scale, ParallaxCurve.shape(2f), "shape(2)")
        assertExactly(-scale, ParallaxCurve.shape(-2f), "shape(-2)")
        assertExactly(scale, ParallaxCurve.shape(1e6f), "shape(1e6)")
        assertExactly(-scale, ParallaxCurve.shape(-1e6f), "shape(-1e6)")
    }

    @Test
    fun `curve is odd and monotone over the whole domain`() {
        var v = -2f
        var prev = ParallaxCurve.shape(v)
        while (v <= 2f) {
            val g = ParallaxCurve.shape(v)
            assertTrue("单调非减 @v=$v ($prev -> $g)", g >= prev)
            assertExactly(-g, ParallaxCurve.shape(-v), "奇对称 @v=$v")
            prev = g
            v += 0.0005f
        }
    }

    @Test
    fun `curve compresses and never exceeds the linear map`() {
        // k=2 未缩放时是放大（g = 2v - v²  ≥ v）；是 0.5 这个缩放把它压到线下方。
        var v = 0f
        while (v <= 1f) {
            val g = ParallaxCurve.shape(v)
            assertTrue("压缩性 @v=$v ($g > $v)", g <= v)
            if (v > 0f) assertTrue("仅 v=0 取等 @v=$v", g < v)
            v += 0.001f
        }
    }

    @Test
    fun `centre gain is one and strictly decreasing toward zero at the edge`() {
        assertTrue("中心增益 ${gain(0f)} 应≈1.0", abs(gain(0f) - 1f) < 0.01f)

        var v = 0f
        var prevGain = gain(v)
        while (v < 0.9f) {
            v += 0.1f
            val g = gain(v)
            assertTrue("增益应严格递减 @v=$v ($prevGain -> $g)", g < prevGain)
            prevGain = g
        }
        assertTrue("边缘增益 ${gain(0.999f)} 应趋 0", gain(0.999f) < 0.01f)
    }

    @Test
    fun `curve never flips the sign of the error`() {
        var v = -1.5f
        while (v <= 1.5f) {
            if (abs(v) >= 1e-3f) {
                val g = ParallaxCurve.shape(v)
                assertTrue("符号不得翻转 @v=$v (g=$g)", (g > 0f) == (v > 0f))
            }
            v += 0.01f
        }
    }
}
