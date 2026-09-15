package com.njuse.ea.ui.device

import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/**
 * ParallaxBiasTracker 纯逻辑测试（通道 1，确定性时钟）。
 *
 * 固化两条判据：
 *  - 角速度连续低于阈值满 1s（＝安静下来了）触发回正并重置零位；归位在 600ms 内完成、缓入缓出。
 *    角速度只用于判安静，**不拦输入**：超阈值的运动本身不会移动零位，也不会触发回正。
 *  - 「不闪现」：bias 全程连续——触发那一帧与上一帧逐位相同（p=0 ⇒ eased=0），
 *    单事件位移有上界，首末事件位移趋近 0；非有限帧被丢弃且不消耗触发。
 *
 * 约定：事件间隔 20ms（50Hz），与真机 SENSOR_DELAY_GAME 一致。
 * motion 参数：null = 本机无陀螺仪；0f = 有陀螺仪且静止；≥ MOTION_QUIET_RAD_PER_SEC = 在动。
 */
class ParallaxBiasTrackerTest {

    private val step = 20_000_000L                  // 20ms = 50Hz
    private val quiet = RECALIBRATE_QUIET_NANOS     // 1s
    private val glide = RECALIBRATE_GLIDE_NANOS     // 600ms
    private val moving = MOTION_QUIET_RAD_PER_SEC   // 阈值本身就是「在动」

    private fun assertNear(expected: Float, actual: Float, msg: String, eps: Float = 1e-4f) =
        assertTrue("$msg expected=$expected actual=$actual", abs(expected - actual) < eps)

    /** 逐位相同：用于断言「这一帧与上一帧完全一致」（不闪现的硬判据）。 */
    private fun assertExactly(expected: Float, actual: Float, msg: String) =
        assertTrue("$msg expected=$expected actual=$actual", expected == actual)

    private fun feed(t: ParallaxBiasTracker, now: Long, rawX: Float, motion: Float? = null): Float {
        t.update(rawX, 0f, now, motion)
        return t.biasX
    }

    // ---- 首次对齐 ----

    @Test
    fun `first pose becomes the zero point instantly and does not glide`() {
        val t = ParallaxBiasTracker()
        t.update(0.4f, -0.2f, 0L, 0f)
        assertExactly(0.4f, t.biasX, "首帧 biasX")
        assertExactly(-0.2f, t.biasY, "首帧 biasY")
        assertTrue("首帧后 hasPose", t.hasPose)

        // 20ms 后姿态大变，但既没安静满 1s、也不存在滑动
        t.update(0.9f, 0.5f, step, 0f)
        assertExactly(0.4f, t.biasX, "首帧不滑动")
        assertExactly(-0.2f, t.biasY, "首帧不滑动")
    }

    // ---- 角速度只判安静，不移动零位 ----

    @Test
    fun `supra-threshold motion never moves the bias and never triggers`() {
        val t = ParallaxBiasTracker()
        t.update(0f, 0f, 0L, moving)
        var now = step
        while (now <= 5_000_000_000L) {                 // 持续在动 5s
            feed(t, now, 1.0f, moving)
            assertExactly(0f, t.biasX, "运动中不得触发回正 @${now / 1_000_000}ms")
            now += step
        }
    }

    @Test
    fun `motion at the threshold counts as moving, just below counts as quiet`() {
        // 阈值即边界：>= 阈值算「在动」
        val atThreshold = ParallaxBiasTracker()
        atThreshold.update(0f, 0f, 0L, MOTION_QUIET_RAD_PER_SEC)
        var now = step
        while (now <= 3_000_000_000L) {
            feed(atThreshold, now, 1f, MOTION_QUIET_RAD_PER_SEC)
            now += step
        }
        assertExactly(0f, atThreshold.biasX, "等于阈值应算在动，不得触发")

        // 略低于阈值算「安静」→ 满 1s 触发
        val belowThreshold = ParallaxBiasTracker()
        belowThreshold.update(0f, 0f, 0L, MOTION_QUIET_RAD_PER_SEC - 0.01f)
        now = step
        while (now <= quiet) {
            feed(belowThreshold, now, 1f, MOTION_QUIET_RAD_PER_SEC - 0.01f)
            now += step
        }
        assertExactly(0f, belowThreshold.biasX, "触发帧不得跳变")
        feed(belowThreshold, now + step, 1f, MOTION_QUIET_RAD_PER_SEC - 0.01f)
        assertTrue("略低于阈值应算安静并触发", belowThreshold.biasX > 0f)
    }

    @Test
    fun `no gyroscope means no quiet recalibration`() {
        // motion = null（本机无陀螺仪）：无从判断是否在动 → 永不按安静回正
        val t = ParallaxBiasTracker()
        t.update(0f, 0f, 0L)
        var now = step
        while (now <= 5_000_000_000L) {
            feed(t, now, 1.0f)
            assertExactly(0f, t.biasX, "无陀螺仪不得按安静触发 @${now / 1_000_000}ms")
            now += step
        }
    }

    // ---- 归位曲线：600ms 完成、无跳变 ----

    @Test
    fun `quiet recalibration glides home in 600ms with an eased profile and no snap`() {
        // 注入 100ms 安静窗口以便少喂事件：触发点 t=100ms，归位 600ms（用生产常量）
        val t = ParallaxBiasTracker(quietNanos = 100_000_000L)
        val biases = ArrayList<Float>()
        for (i in 0..50) {                                  // t = 0 .. 1000ms
            val now = i * step
            t.update(if (now == 0L) 0f else 1f, 0f, now, 0f)
            biases.add(t.biasX)
        }
        fun at(ms: Long) = biases[(ms / 20).toInt()]

        // 首次姿态是零位，随后安静：触发帧（t=100ms）与上一帧逐位相同 —— 不闪现的硬判据
        assertExactly(0f, at(80), "触发前一帧")
        assertExactly(0f, at(100), "触发帧不得跳变")
        assertTrue("触发后开始移动", at(120) > 0f)

        // 归位在 600ms 内完成：t=100+600=700ms 精确到位
        assertNear(1f, at(700), "归位完成值")
        assertExactly(1f, at(1000), "完成后不得抖动（安静触发反复到来也不重启归位）")

        // 缓入缓出：两端速度≈0，峰值单事件位移远小于总量
        var maxDelta = 0f
        var firstDelta = 0f
        var lastDelta = 0f
        for (i in 6..35) {                                   // t=120ms .. 700ms
            val d = biases[i] - biases[i - 1]
            maxDelta = maxOf(maxDelta, abs(d))
            if (i == 6) firstDelta = d
            if (i == 35) lastDelta = d
        }
        assertTrue("单事件位移 $maxDelta 应远小于总量", maxDelta <= 0.12f)
        assertTrue("首个事件位移 $firstDelta 必须≈0（不闪现）", firstDelta <= 0.01f)
        assertTrue("末个事件位移 $lastDelta 必须≈0（不顿挫）", lastDelta <= 0.01f)

        // 单调：全程只向目标前进，不来回摆
        for (i in 6..35) assertTrue("bias 应单调不减 @i=$i", biases[i] >= biases[i - 1])

        // 前快后慢（FastOutSlowIn）中点 ≈0.78。安静窗口（100ms）远小于归位时长，
        // 若归位中被安静触发重启，此值会明显偏离（≈0.59）——这条同时钉住「不重启」。
        assertTrue("中点 ${at(400)} 应落在缓动曲线中段", at(400) > 0.70f && at(400) < 0.85f)
    }

    // ---- 安静 1s ----

    @Test
    fun `one second of quiet recalibrates, and the trigger frame is bit-identical`() {
        val t = ParallaxBiasTracker()
        val biases = ArrayList<Float>()
        for (i in 0..80) {                                   // t = 0 .. 1.600s
            val now = i * step
            t.update(if (now == 0L) 0f else 1f, 0f, now, 0f)
            biases.add(t.biasX)
        }
        fun at(i: Int) = biases[i]

        assertExactly(0f, at(49), "1s 前不得触发")            // t=0.980s
        assertExactly(0f, at(50), "触发帧不得跳变")           // t=1.000s
        assertTrue("触发后开始移动", at(51) > 0f)
        assertTrue("1.58s 应接近但未到 1", at(79) < 1f && at(79) > 0.99f)
        assertExactly(1f, at(80), "1.60s 精确到位")           // 600ms 归位

        var maxDelta = 0f
        for (i in 51..80) maxDelta = maxOf(maxDelta, abs(at(i) - at(i - 1)))
        assertTrue("单事件位移 $maxDelta 应有界", maxDelta <= 0.12f)
        assertTrue("首个事件位移应≈0", abs(at(51) - at(50)) <= 0.01f)
    }

    @Test
    fun `quiet timer restarts on every supra-threshold sample`() {
        val t = ParallaxBiasTracker()
        var now = 0L
        t.update(0f, 0f, 0L, moving)                         // 有效首帧（零位=0）
        // 持续在动 5s：不触发
        while (now < 5_000_000_000L) {
            now += step
            assertExactly(0f, feed(t, now, 1f, moving), "运动中不得触发 @${now / 1_000_000}ms")
        }
        // 安静下来：触发点应是「最后一次在动」+ 1s，不早不晚
        val lastMove = 5_000_000_000L
        while (now < lastMove + quiet) {                     // 直到 5.980s
            assertExactly(0f, feed(t, now, 1f, 0f), "安静不足 1s 不得触发 @${now / 1_000_000}ms")
            now += step
        }
        assertExactly(0f, feed(t, now, 1f, 0f), "整点触发帧不得跳变")   // t=6.000s
        var bias = feed(t, now + step, 1f, 0f)
        assertTrue("触发后应下行到 1.0", bias > 0f)

        while (now < lastMove + quiet + glide) {             // 直到 6.600s
            now += step
            bias = feed(t, now, 1f, 0f)
        }
        assertNear(1f, bias, "从触发点起 600ms 到位")
    }

    // ---- 显式重校准（注册监听 / 回到前台） ----

    @Test
    fun `explicit recalibration glides from the current bias without a first-frame jump`() {
        val t = ParallaxBiasTracker()
        var now = 0L
        while (now <= 5_000_000_000L) {                     // 5s：无陀螺仪 → 不按安静触发
            t.update(if (now == 0L) 0f else 1f, 0f, now, null)
            now += step
        }
        assertExactly(0f, t.biasX, "5s 内不得触发")

        // 模拟 ON_STOP → 姿态改变 → ON_START 重新注册
        t.requestRecalibration()
        val resumeAt = 30_000_000_000L
        t.update(0.3f, 0f, resumeAt, null)
        assertExactly(0f, t.biasX, "恢复首帧必须与暂停前逐位相同（否则可见偏移会跳）")

        now = resumeAt
        var bias = t.biasX
        while (now < resumeAt + glide) {
            now += step
            t.update(0.3f, 0f, now, null)
            assertTrue("归位应单调滑向新零位", t.biasX >= bias)
            bias = t.biasX
        }
        assertNear(0.3f, bias, "600ms 后滑到新姿态")
    }

    @Test
    fun `explicit recalibration during a glide continues from the in-flight bias`() {
        val t = ParallaxBiasTracker()
        var now = 0L
        // 0 → 1.180s：安静满 1s 于 1.000s 起滑，向 1.0
        while (now <= 1_180_000_000L) {
            t.update(if (now == 0L) 0f else 1f, 0f, now, 0f)
            now += step
        }
        val before = t.biasX
        assertTrue("归位应已在进行中", before > 0.3f && before < 0.6f)

        // 归位中途显式请求，且姿态变为 -0.5
        val switchAt = 1_200_000_000L
        t.requestRecalibration()
        t.update(-0.5f, 0f, switchAt, 0f)
        val atSwitch = t.biasX
        assertTrue("切换帧不得跳回起点（应延续原归位）", atSwitch >= before)
        assertTrue("切换帧也只是原归位的一小步", atSwitch - before <= 0.12f)

        // 从切换点起算 600ms 滑到 -0.5
        var later = switchAt
        while (later < switchAt + glide) {
            later += step
            t.update(-0.5f, 0f, later, 0f)
        }
        assertNear(-0.5f, t.biasX, "从重新请求起 600ms 到位")
    }

    // ---- 防御 ----

    @Test
    fun `non-finite frames are dropped and do not consume the trigger`() {
        val t = ParallaxBiasTracker()
        var now = 0L
        while (now <= 980_000_000L) {
            t.update(if (now == 0L) 0f else 1f, 0f, now, 0f)
            now += step
        }
        assertExactly(0f, t.biasX, "1s 前不得触发")

        now = 1_000_000_000L           // 已安静满 1s，但这一帧姿态是脏数据
        t.update(Float.NaN, 0f, now, 0f)
        assertExactly(0f, t.biasX, "NaN 帧不得改动 bias")
        assertTrue("NaN 帧后 bias 仍有限", t.biasX.isFinite())

        now += step
        t.update(0f, Float.POSITIVE_INFINITY, now, 0f)
        assertExactly(0f, t.biasX, "Inf 帧不得改动 bias")

        now += step                     // 1.040s：首个有效帧，触发应在此刻才发生
        t.update(1f, 0f, now, 0f)
        assertExactly(0f, t.biasX, "触发帧不得跳变")

        while (now < 1_040_000_000L + glide) {
            now += step
            t.update(1f, 0f, now, 0f)
        }
        assertNear(1f, t.biasX, "从首个有效帧起 600ms 到位")
    }

    @Test
    fun `a dirty pose frame still refreshes the quiet timer when the gyro says moving`() {
        // 姿态帧退化为 NaN 时运动信号仍然有效：不得因此误判为「安静」而触发回正
        val t = ParallaxBiasTracker()
        t.update(0f, 0f, 0L, moving)                     // 有效首帧（零位=0）
        var now = step
        while (now <= 3_000_000_000L) {                  // 3s 内姿态一直是脏数据，但一直在动
            t.update(Float.NaN, Float.NaN, now, moving)
            now += step
        }
        assertExactly(0f, t.biasX, "脏帧期间不得改动 bias")

        // 姿态恢复正常且已静止：安静计时从「最后一次在动」（3.000s）起算，需再等满 1s。
        // 若实现把角速度刷新放在脏帧拦截之后，这里会在恢复帧就触发、4s 时已归位到 1.0。
        t.update(1f, 0f, now, 0f)
        assertExactly(0f, t.biasX, "恢复帧不得跳变")
        val triggerAt = 3_000_000_000L + quiet
        while (now < triggerAt) {
            now += step
            t.update(1f, 0f, now, 0f)
        }
        assertExactly(0f, t.biasX, "整点触发帧不得跳变")
    }

    // ---- 组合管线：死区 + 曲线 + 低通 ----

    @Test
    fun `deadzone never adds a visible step to the glide home`() {
        // 同一条 bias 轨迹分别喂「有死区」「无死区」两条滤波管线，逐帧比较：
        // 死区对单帧位移的全部影响必须 ≤0.005（≈0.15px）——即它永远不会读成闪现。
        val t = ParallaxBiasTracker(quietNanos = 100_000_000L)
        var withDead = 1f              // 触发前已稳定在满偏移
        var withoutDead = 1f
        var prevWith = withDead
        var prevWithout = withoutDead
        var maxDelta = 0f
        var maxDeltaDiff = 0f
        var now = 0L
        while (now <= 1_000_000_000L) {
            t.update(if (now == 0L) 0f else 1f, 0f, now, 0f)
            if (now >= 100_000_000L) {                     // 触发之后才有归位
                val error = 1f - t.biasX
                prevWith = withDead
                prevWithout = withoutDead
                withDead = ParallaxFilter.step(withDead, error)
                // 基线只含曲线、不含死区，否则就是 ParallaxFilter.step 的逐行手抄（同义反复）
                withoutDead = withoutDead * ParallaxFilter.SMOOTHING +
                    ParallaxCurve.shape(error) * (1 - ParallaxFilter.SMOOTHING)
                maxDelta = maxOf(maxDelta, abs(withDead - prevWith))
                maxDeltaDiff = maxOf(
                    maxDeltaDiff,
                    abs((withDead - prevWith) - (withoutDead - prevWithout))
                )
                assertTrue("输出应单调趋向 0", withDead <= prevWith + 1e-6f)
            }
            now += step
        }
        // 像素换算（随 MainScreen 的 PARALLAX_RX/RY 变动，按当前 0.04/0.015、1080×420dpi 估）：
        // 归一化 1.0 ≈ 43px（L1 bleed = PARALLAX_RX × 屏宽）/ 86px（L2 ×2）。
        // 于是 0.1 ≈ 4.3px/帧 是平移；真正的闪现是 0.5~1.0（22~43px）的单帧瞬移。
        assertTrue("单帧位移 $maxDelta 不得出现闪现量级跳变", maxDelta <= 0.1f)
        // 死区最多把误差抹掉 0.03，经低通放大系数 0.15 ⇒ 单帧差 ≤0.0045（推导而来）
        assertTrue("死区造成的单帧位移差 $maxDeltaDiff 必须亚像素", maxDeltaDiff <= 0.005f)
        assertTrue("归位结束输出应接近 0", withDead < 0.2f)
    }

    @Test
    fun `composed pipeline stays below flash level through a quiet recalibration`() {
        // 运动中：输出收敛到曲线给出的幅度（1.0 → shape(1.0) = AMPLITUDE_SCALE）
        // 安静后：bias 归位把误差拉回 0，输出随之平滑归零——全程不得有闪现级单帧跳变
        val t = ParallaxBiasTracker(quietNanos = 100_000_000L)
        var out = 0f
        var prevOut = 0f
        var maxDelta = 0f
        var now = 0L
        while (now <= 2_000_000_000L) {
            val motion = if (now < 500_000_000L) moving else 0f
            t.update(if (now == 0L) 0f else 1f, 0f, now, motion)
            if (now > 0L) {
                prevOut = out
                out = ParallaxFilter.step(out, 1f - t.biasX)
                maxDelta = maxOf(maxDelta, abs(out - prevOut))
            }
            now += step
        }
        assertTrue("单帧位移 $maxDelta 不得出现闪现量级跳变", maxDelta <= 0.1f)
        assertTrue("运动段应达到曲线满幅 ${ParallaxCurve.AMPLITUDE_SCALE}", maxDelta > 0f)
        assertTrue("归位后输出应接近 0（实际 $out）", out < 0.05f)
    }
}
