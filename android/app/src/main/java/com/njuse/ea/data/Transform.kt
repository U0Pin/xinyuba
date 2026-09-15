package com.njuse.ea.data

import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin

/**
 * 变换纯几何模块（无 Android/Compose 依赖，可 JVM 单测）。
 *
 * 设计原则（根治「旋转耦合」一族问题的数学基础）：
 * 1. 一切以**几何中心**为锚（ItemLayout v2 中心制）。中心是旋转不动点，
 *    与 graphicsLayer(rotationZ) 的旋转中心天然一致。
 * 2. 角缩放为**绝对求解**：手势开始时冻结对角锚点 A 与旋转角 θ，
 *    之后每帧用手指的**绝对位置** finger 反解尺寸——
 *      (finger − A) = R(θ)·(2sx·w/2, 2sy·h/2)
 *    不读上一帧结果、不做增量累加，无反馈回路，浮点不漂移；
 *    θ 全程冻结 → 缩放与旋转彻底解耦；clamp 只作用于 w/h，
 *    位置完全由「锚点 + 旋转向量」派生 → 中心 clamp 永远碰不到锚点。
 * 3. 被拖角在未钳制时严格等于手指位置（1:1 跟手）。
 */

/** 角度制旋转矩阵（屏幕坐标系 y 向下，正角=顺时针，与 Compose rotationZ 一致）。 */
class Rot(deg: Float) {
    val c: Float = cos(Math.toRadians(deg.toDouble())).toFloat()
    val s: Float = sin(Math.toRadians(deg.toDouble())).toFloat()

    /** 正旋转 R(θ)·(x,y)。 */
    fun apply(x: Float, y: Float): Pair<Float, Float> = Pair(x * c - y * s, x * s + y * c)

    /** 逆旋转 R(−θ)·(x,y)。 */
    fun inv(x: Float, y: Float): Pair<Float, Float> = Pair(x * c + y * s, -x * s + y * c)
}

/** 物品某个角（sx,sy ∈ {−1,+1}）在设计坐标里的位置：center + R(θ)·(sx·w/2, sy·h/2)。 */
fun ItemLayout.corner(sx: Int, sy: Int): Pair<Float, Float> {
    val (vx, vy) = Rot(rotation).apply(sx * w / 2f, sy * h / 2f)
    return Pair(cx + vx, cy + vy)
}

/** 被拖角 (sx,sy) 的固定锚点 = 对角 (−sx,−sy)。 */
fun ItemLayout.anchorOf(sx: Int, sy: Int): Pair<Float, Float> = corner(-sx, -sy)

/** 平移：中心 += (dx,dy)（屏幕绝对方向，与旋转无关）。 */
fun ItemLayout.translate(dx: Float, dy: Float): ItemLayout = copy(cx = cx + dx, cy = cy + dy)

/** 旋转到指定角度：只改角度，中心不动。 */
fun ItemLayout.rotateTo(newRot: Float): ItemLayout = copy(rotation = newRot)

/**
 * 角缩放（绝对求解）。拖动角 (sx,sy)，对角 [anchorX,anchorY] 固定（手势开始时由
 * [anchorOf] 冻结），手指当前设计坐标 [fingerX,fingerY] 决定新尺寸与中心。
 * 尺寸钳制在 [minW,maxW]×[minH,maxH]（设计单位），钳制后中心仍由锚点几何派生。
 */
fun ItemLayout.resize(
    sx: Int, sy: Int,
    anchorX: Float, anchorY: Float,
    fingerX: Float, fingerY: Float,
    minW: Float, maxW: Float,
    minH: Float, maxH: Float
): ItemLayout {
    val r = Rot(rotation)
    // 把「锚点→手指」向量反旋转到物品局部轴；被拖角在局部轴上的半尺寸 = 手指分量的一半。
    val (dxLocal, dyLocal) = r.inv(fingerX - anchorX, fingerY - anchorY)
    val w2 = (sx * dxLocal / 2f).coerceIn(minW / 2f, maxW / 2f)
    val h2 = (sy * dyLocal / 2f).coerceIn(minH / 2f, maxH / 2f)
    // 新中心 = 冻结锚点 + R(θ)·(sx·w2, sy·h2)；w/h = 2·半尺寸。
    val (vx, vy) = r.apply(sx * w2, sy * h2)
    return copy(cx = anchorX + vx, cy = anchorY + vy, w = w2 * 2f, h = h2 * 2f)
}

/**
 * 屏幕像素 ↔ 设计坐标 换算（纯算术）。
 * 设计坐标 (540,1200) 对应屏幕中心；1 设计单位 = scale·density 像素。
 * positionInRoot 在边到边（edge-to-edge）布局下即窗口坐标，与 fillMaxSize 的
 * 根布局重合，可直接使用。
 */
class ScreenMath(
    val scale: Float,
    val density: Float,
    val screenWpx: Float,
    val screenHpx: Float
) {
    /** 1 设计单位对应的屏幕像素数 = scale·density。 */
    val unit = scale * density

    fun designToScreenPx(dx: Float, dy: Float): Pair<Float, Float> =
        Pair(screenWpx / 2f + (dx - SCREEN_CX) * unit, screenHpx / 2f + (dy - SCREEN_CY) * unit)

    fun screenToDesignPx(px: Float, py: Float): Pair<Float, Float> =
        Pair(SCREEN_CX + (px - screenWpx / 2f) / unit, SCREEN_CY + (py - screenHpx / 2f) / unit)
}

/** 手指相对物品中心的屏幕方位角（度，顺时针，y 向下）。 */
fun fingerAngleDeg(fx: Float, fy: Float, cxPx: Float, cyPx: Float): Float =
    Math.toDegrees(atan2((fy - cyPx).toDouble(), (fx - cxPx).toDouble())).toFloat()
