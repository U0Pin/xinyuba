package com.njuse.ea.ui.device

import androidx.compose.animation.core.FastOutSlowInEasing
import kotlin.math.abs

/** 安静判定阈值：角速度模长低于此值（rad/s，0.20 ≈ 11.5°/s）视为「没有在动」。 */
internal const val MOTION_QUIET_RAD_PER_SEC = 0.20f

/** 安静多久才回正：ω 连续低于阈值满这段时间，触发回正并重置零位。 */
internal const val RECALIBRATE_QUIET_NANOS = 1_000_000_000L

/** 归位时长：bias 在 600ms 内平滑滑到新零位（缓入缓出，两端速度≈0 ⇒ 不闪现）。 */
internal const val RECALIBRATE_GLIDE_NANOS = 600_000_000L

/**
 * 视差零位校准状态机（纯逻辑、无 Android 依赖，JVM 单测覆盖）。
 *
 * 维护「当前零位 bias」。两个时机启动一次 600ms 平滑归位：
 *  1) **安静**：角速度连续低于 [MOTION_QUIET_RAD_PER_SEC] 满 [quietNanos]，把当前姿态
 *     重新定为零位（画面平滑滑回中心）；
 *  2) **显式**：[requestRecalibration]（注册监听 / 回到前台），不受安静门控——那时零位
 *     可能已过期，必须无条件滑回。
 *
 * 角速度只用来判安静，**不拦输入**：低于阈值时倾斜依然被响应。若在此清零输入，
 * 松手瞬间画面就会按低通归零，「安静满 1s 才回正」永远来不及显现——两条会互相抵消。
 * 停手时画面能停住也不需要冻结机制：不动时姿态本来就不变。
 *
 * 关键不变量（＝「不要闪现」）：
 *  - bias 全程连续；唯一跳变发生在首次姿态，而那一刻可见偏移必然是 0，跳变不可见。
 *  - 归位起点取「本帧之前的 bias」（p=0 ⇒ eased=0），因此触发那一帧与上一帧逐位相同。
 *  - 只有触发能移动 bias；用户自己转动手机只改 raw，不动 bias。
 *  - 非有限值整帧丢弃：不推进状态、也不消耗触发。
 *
 * 归位进行中只接受显式请求重入（安静触发会让位），避免「安静窗口 < 归位时长」时反复重启。
 *
 * 线程：只在 SensorEventListener 回调线程（本 App 为主线程）使用，无需同步。
 *
 * @param quietNanos 安静多久触发回正
 * @param glideNanos 归位时长
 */
internal class ParallaxBiasTracker(
    private val quietNanos: Long = RECALIBRATE_QUIET_NANOS,
    private val glideNanos: Long = RECALIBRATE_GLIDE_NANOS
) {
    /** 当前零位（原始姿态坐标系下的归一化值）。 */
    var biasX: Float = 0f
        private set
    var biasY: Float = 0f
        private set

    /** 是否已捕获过至少一个有效姿态；false 时 [update] 走「首次对齐」分支。 */
    var hasPose: Boolean = false
        private set

    /** 最后一次「在动」的时刻；安静时长从它起算。 */
    private var lastMoveNanos = 0L
    private var pendingRecalibration = false

    private var gliding = false
    private var glideFromX = 0f
    private var glideFromY = 0f
    private var glideToX = 0f
    private var glideToY = 0f
    private var glideStartNanos = 0L

    /**
     * 请求一次平滑归位（不是瞬间清零）。注册监听 / 回到前台时调用：下一帧有效姿态会
     * 从「当前 bias」平滑滑到「那一刻的姿态」——二者之差正是当前可见偏移，因此画面
     * 从离开前的数值连续地回到 0，而不是瞬间回正。
     */
    fun requestRecalibration() {
        pendingRecalibration = true
    }

    /**
     * 喂入一帧原始姿态（已归一化），零位由 [biasX]/[biasY] 读出。
     *
     * [nowNanos] 由调用方用 SystemClock.elapsedRealtimeNanos() 提供——单一单调时钟，
     * ACCEL/MAG 两条事件流共用；不用 SensorEvent.timestamp（时基不保证、可能为 0）。
     *
     * [motionRadPerSec] 为陀螺仪角速度模长：`null` 表示**本机没有陀螺仪**（无从判断
     * 是否在动，于是永不按安静回正）；有陀螺仪但静止时传 `0f`。
     */
    fun update(txRaw: Float, tyRaw: Float, nowNanos: Long, motionRadPerSec: Float? = null) {
        // 安静计时：本帧角速度达到阈值就刷新「最后一次在动」。NaN/Inf（退化陀螺仪）
        // 按「在动」处理——宁可不要回正，也不能让零位追着倾斜跑。
        // 刻意放在姿态有效性检查**之前**：姿态帧退化为 NaN 时，运动信号仍然有效，
        // 不该因此被冻住、进而误判成「安静」而触发一次本不该发生的回正。
        if (motionRadPerSec != null &&
            (!motionRadPerSec.isFinite() || motionRadPerSec >= MOTION_QUIET_RAD_PER_SEC)
        ) {
            lastMoveNanos = nowNanos
        }

        // 仿真器/退化姿态会注入 NaN/Infinity：整帧丢弃，绝不污染 bias（否则后续全 NaN）。
        if (!txRaw.isFinite() || !tyRaw.isFinite()) return

        // 1) 首次姿态：直接对齐。此刻可见偏移 = raw - bias = 0，跳变不可见。
        if (!hasPose) {
            hasPose = true
            biasX = txRaw
            biasY = tyRaw
            lastMoveNanos = nowNanos
            pendingRecalibration = false
            gliding = false
            return
        }

        // 2) 先推进进行中的归位，得到「本帧的起点」。
        advanceGlide(nowNanos)

        // 3) 到点则启动新归位。起点取当前 bias ⇒ 本帧 bias 不变 ⇒ 不闪现。
        val due = pendingRecalibration ||
            (!gliding && motionRadPerSec != null && nowNanos - lastMoveNanos >= quietNanos)
        if (due) {
            glideFromX = biasX
            glideFromY = biasY
            glideToX = txRaw
            glideToY = tyRaw
            glideStartNanos = nowNanos
            gliding = true
            pendingRecalibration = false
        }
    }

    private fun advanceGlide(nowNanos: Long) {
        if (!gliding) return
        val elapsed = nowNanos - glideStartNanos
        // 时钟异常（回拨/乱序）本帧不动：绝不能把进度往回拨，那会让画面倒退。
        if (elapsed < 0L) return
        // 饱和收尾：长时间无事件后一次性完成（此时屏幕通常已不可见）。
        val p = if (elapsed >= glideNanos) 1f else elapsed.toFloat() / glideNanos
        val e = FastOutSlowInEasing.transform(p)
        biasX = glideFromX + (glideToX - glideFromX) * e
        biasY = glideFromY + (glideToY - glideFromY) * e
        if (p >= 1f) gliding = false
    }
}

/**
 * 边缘发硬曲线：越接近最大偏移越难继续移动（像弹簧被挤压）。
 *
 * 软膝归一化 `g(v) = 振幅 · sign(v) · (1 − (1−|v|)^k)`，取 k = 2 并展开成
 * `g(v) = 振幅 · sign(v) · (2|v| − |v|²)`（不用 pow，保持逐位可预期）。
 * 输入先 clamp 到 ±1：`|v| ≥ 1` 时输出精确等于 ±振幅。
 *
 * 以 [AMPLITUDE_SCALE] = 0.5 计：中心增益恰好 1.0（小幅倾斜灵敏度与曲线引入前一致），
 * 边缘增益 → 0（推不动），最大幅度 ±0.5（满档减半），且处处 `g(v) ≤ v`（仅 v=0 取等）。
 *
 * 若要改 k，需相应改 [shape] 里的展开式（k=3 即 3|v| − 3|v|² + |v|³）。
 */
internal object ParallaxCurve {
    /** 整体振幅缩放：同时决定「中心增益 = 2 × 本值」与「最大可达偏移 = 本值」。 */
    const val AMPLITUDE_SCALE = 0.5f

    fun shape(v: Float): Float {
        val a = abs(v)
        if (a >= 1f) return if (v > 0f) AMPLITUDE_SCALE else -AMPLITUDE_SCALE
        val t = 1f - a
        val s = 1f - t * t
        return if (v < 0f) -s * AMPLITUDE_SCALE else s * AMPLITUDE_SCALE
    }
}

/**
 * 视差输出滤波：死区 → 边缘发硬曲线 → 逐事件低通。纯函数、无状态。
 *
 * 顺序是刻意的：死区在**曲线之前**（阈值 0.03 保持「原始误差单位」的含义），
 * 曲线在低通之前（先塑形再平滑，避免曲线把低通的平滑结果再次放大）。
 *
 * 曲线只作用于误差 `raw - bias`，**绝不作用于 bias**：否则缓动插值不再线性，
 * `p=0 ⇒ eased=0` 的「触发帧逐位相同」不变量失效，画面就会闪现。
 */
internal object ParallaxFilter {
    /** 死区：小偏移归零，避免静止漂移与手抖。 */
    const val DEADZONE = 0.03f

    /** 低通系数：value = value * SMOOTHING + shaped * (1 - SMOOTHING)。 */
    const val SMOOTHING = 0.85f

    /** 单轴推进一步。[prev] 为上一步输出，[error] 为当前 raw - bias。 */
    fun step(prev: Float, error: Float): Float {
        val e = if (abs(error) < DEADZONE) 0f else error
        return prev * SMOOTHING + ParallaxCurve.shape(e) * (1 - SMOOTHING)
    }
}
