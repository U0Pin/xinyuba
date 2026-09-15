package com.njuse.ea.ui.device

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.SystemClock
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.State
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import kotlin.math.hypot

/**
 * 陀螺仪视差偏移 Hook：返回归一化偏移 [-1,1]，单位已经是无量纲方向因子，
 * 调用方按自身 bleed 上限 (px) 相乘得到实际 translationPx。
 *
 * 传感器策略：优先 TYPE_GAME_ROTATION_VECTOR（对磁场不敏感、最稳）；
 * 不可用时回落 TYPE_ACCELEROMETER + TYPE_MAGNETIC_FIELD 经 getRotationMatrix 得到方向。
 * 仿真器/无传感器 → 恒返回 Offset.Zero，等同于关闭，零退化。
 *
 * 零位校准：首次姿态即零位；此后「角速度连续低于阈值满 1s」（＝安静下来了）触发重校准，
 * 回到前台时也重校准。每次换零位都是 600ms 缓入缓出的平滑滑动（见 [ParallaxBiasTracker]），
 * 不会闪现。角速度只用于判安静，**不拦倾斜输入**——低于阈值时倾斜依然被响应。
 *
 * 调用方控制 [enabled]：false 时不注册 listener，恒返回 Zero。
 */
@Composable
fun rememberParallaxOffset(enabled: Boolean): State<Offset> {
    val context = LocalContext.current
    val state = remember { mutableStateOf(Offset.Zero) }

    if (!enabled) {
        state.value = Offset.Zero
        return state
    }

    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(context, lifecycleOwner) {
        val sm = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val useGameRotation = sm.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR) != null
        var rotMatrix = FloatArray(9)
        var accelValues = FloatArray(3)
        var magValues = FloatArray(3)
        var hasAccel = false
        var hasMag = false

        // 零位校准状态机。必须建在 effect 内（每次注册都是全新实例）：新建组合时
        // 首帧直接对齐（此刻可见偏移本就是 0，跳变不可见），而回前台时复用同一实例
        // 才能平滑滑回。若用 remember 持有，enabled 关→开会带着旧 bias 产生跳变。
        val tracker = ParallaxBiasTracker()

        // 角速度只用于判「安静」：安静满 1s 才回正。无陀螺仪 → 恒为 null → 永不按安静
        // 回正（只在注册/回前台时重置零位），零退化。
        val gyroSensor = sm.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
        var lastGyroMagnitude: Float? = if (gyroSensor == null) null else 0f

        val listener = object : SensorEventListener {
            override fun onSensorChanged(event: SensorEvent) {
                // 单一单调时钟，两条传感器流共用（事件时基见 ParallaxBiasTracker.update）。
                val now = SystemClock.elapsedRealtimeNanos()
                when (event.sensor.type) {
                    Sensor.TYPE_GYROSCOPE -> {
                        // 只记角速度、不碰 state。取 roll/pitch 两轴：纯偏航（转身）
                        // 不改变倾斜角，不该影响「安静」判定。
                        val m = hypot(event.values[0], event.values[1])
                        if (m.isFinite()) lastGyroMagnitude = m
                    }
                    Sensor.TYPE_GAME_ROTATION_VECTOR -> {
                        val r = FloatArray(9)
                        SensorManager.getRotationMatrixFromVector(r, event.values)
                        rotMatrix = r
                        applyOrientation(rotMatrix, now)
                    }
                    Sensor.TYPE_ACCELEROMETER -> {
                        System.arraycopy(event.values, 0, accelValues, 0, 3)
                        hasAccel = true
                        if (hasMag) computeAccMag(now)
                    }
                    Sensor.TYPE_MAGNETIC_FIELD -> {
                        System.arraycopy(event.values, 0, magValues, 0, 3)
                        hasMag = true
                        if (hasAccel) computeAccMag(now)
                    }
                }
            }

            private fun computeAccMag(now: Long) {
                val r = FloatArray(9)
                if (SensorManager.getRotationMatrix(r, null, accelValues, magValues)) {
                    rotMatrix = r
                    applyOrientation(r, now)
                }
            }

            private fun applyOrientation(r: FloatArray, now: Long) {
                val orientation = FloatArray(3)
                SensorManager.getOrientation(r, orientation)
                // pitch=orientation[1], roll=orientation[2]。tanh 把角度(rad)收敛到 [-1,1]。
                val pitch = orientation[1]
                val roll = orientation[2]
                val txRaw = (Math.tanh(roll / 0.6).toFloat()) * -1f   // 右倾→正值(向右移)
                val tyRaw = (Math.tanh(pitch / 0.6).toFloat())
                // 关键防御：仿真器/退化姿态会注入 NaN/Infinity，一旦污染 bias 与 state，
                // 后续每帧输出皆 NaN → graphicsLayer translation 失效，cafe 跑偏、物品错位。
                // 帧内出现任何非有限值则整帧丢弃，不更新 bias、不更新 state。
                if (!txRaw.isFinite() || !tyRaw.isFinite()) return
                // 零位由 tracker 维护：首帧对齐、安静满 1s 重校准、回前台重校准，
                // 且每次换零位都是 600ms 平滑滑动（不闪现）。
                tracker.update(txRaw, tyRaw, now, lastGyroMagnitude)
                val nx = ParallaxFilter.step(state.value.x, txRaw - tracker.biasX)
                val ny = ParallaxFilter.step(state.value.y, tyRaw - tracker.biasY)
                if (!nx.isFinite() || !ny.isFinite()) return
                state.value = Offset(nx, ny)
            }

            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
        }

        var registered = false        // 姿态源已注册（决定「有没有视差」）
        var gyroRegistered = false    // 陀螺仪已注册（只服务「安静」判定）
        fun register() {
            if (registered) return
            // 陀螺仪在姿态源分支之外注册：两条姿态路径都要有「是否在动」这个信号。
            // 它不设 registered —— 否则「无姿态源 → 降级为关闭」这条路径会被破坏。
            if (!gyroRegistered) {
                gyroSensor?.let {
                    sm.registerListener(listener, it, SensorManager.SENSOR_DELAY_GAME)
                    gyroRegistered = true
                }
            }
            // 重新校准零位——但走平滑归位而非瞬间清零：下一帧姿态成为新零位，
            // 可见偏移在 600ms 内连续滑回 0（首次注册时可见偏移本就是 0，看不到滑动）。
            tracker.requestRecalibration()
            if (useGameRotation) {
                sm.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)?.let {
                    sm.registerListener(listener, it, SensorManager.SENSOR_DELAY_GAME)
                    registered = true
                }
            } else {
                sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let {
                    sm.registerListener(listener, it, SensorManager.SENSOR_DELAY_GAME)
                    registered = true
                }
                sm.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD)?.let {
                    sm.registerListener(listener, it, SensorManager.SENSOR_DELAY_GAME)
                    registered = true
                }
            }
            if (!registered) state.value = Offset.Zero
        }

        fun unregister() {
            // 只注册了陀螺仪（无姿态传感器）时也必须注销，否则监听器泄漏。
            if (!registered && !gyroRegistered) return
            sm.unregisterListener(listener)   // 单参重载：注销本 listener 的全部传感器
            registered = false
            gyroRegistered = false
        }

        // 旋转矩阵需根据屏幕方向重映射；这里用默认 DEVICE 基准（不处理旋转），
        // 因为本 App MainActivity 锁定竖屏（默认 unspecified 但实际竖向 UI）。
        // 若后续支持横屏，应在此处监听 Display.rotation 调用 remapCoordinateSystem。

        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> register()
                // 只停传感器，不清零偏移。navigation-compose 在 navigate() 时会立即把
                // 离场目的地降为 CREATED（此时界面还在退出过渡中可见），派发 ON_STOP；
                // 若在此清零，跳转其他界面的过渡里场景会先「回正」再离开。
                // 回正由 register()（ON_START）负责，且走 600ms 平滑归位而非瞬间清零——
                // 所以回到前台时画面是滑回中心，不是「啪」地跳回。
                Lifecycle.Event.ON_STOP -> unregister()
                else -> {}
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        register()

        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            unregister()
        }
    }
    return state
}