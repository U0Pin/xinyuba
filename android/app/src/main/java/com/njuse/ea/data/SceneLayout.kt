package com.njuse.ea.data

import com.google.gson.JsonParser

// 设计稿画布：Screen 1080×2400；cafe_room/background 为 1188×2642 @ (-54,-121)，
// 两侧各留 54px 为陀螺仪视差预留。
// 常量从 ui 层上收到 data 层：布局几何（Transform/ScreenMath）与数据模型同源。
const val SCREEN_W = 1080f
const val SCREEN_H = 2400f
const val CAFE_W = 1188f
const val CAFE_H = 2642f
const val SCREEN_CX = SCREEN_W / 2f   // 540
const val SCREEN_CY = SCREEN_H / 2f   // 1200

/** 布局数据格式版本。v1=左上角存储(x,y)，v2=中心存储(cx,cy)。 */
const val LAYOUT_VERSION = 2

/**
 * 主界面场景布局（可被用户自定义并持久化）。
 *
 * 坐标一律使用 **Figma 设计稿坐标系**（Screen 1080×2400，左上为原点，单位=设计 px），
 * 运行时由 MainScreen/编辑器统一乘 scale 映射到真实屏幕。
 *
 * v2 起 [ItemLayout] 以**几何中心**存储（cx,cy）：
 * - 中心是旋转不动点，与 graphicsLayer(rotationZ) 的旋转中心天然一致；
 * - 移动=改中心、旋转=改角度（中心不动）、缩放=锚点+手指绝对反解——
 *   全部运算只围绕中心与对角锚点，不再有「左上角随旋转漂移」的换算问题。
 *
 * 只包含「可摆放」的组件：5 个场景物品。
 * 背景(bg_landscape/bg_cafe)、雨层、底部输入栏不可动，不在此表内。
 */
data class SceneLayout(
    val version: Int = LAYOUT_VERSION,
    val items: Map<String, ItemLayout> = emptyMap()
)

/**
 * 单个组件的布局：cx/cy 为设计稿几何中心，w/h 为设计稿尺寸（未旋转的外接框），
 * rotation 为绕中心顺时针旋转角度(度)。visible 预留（v1 编辑页不做隐藏 UI），默认 true。
 */
data class ItemLayout(
    val cx: Float,
    val cy: Float,
    val w: Float,
    val h: Float,
    val rotation: Float = 0f,
    val visible: Boolean = true
)

/** 组件 id 常量。 */
object SceneItemId {
    const val BOARD = "board"            // 黑板（点击进个人画像）
    const val CAT = "cat"                // Agent 猫咪
    const val PLANT = "plant"            // 盆栽（装饰）
    const val NOTEBOOK = "notebook"      // 笔记本（点击进设置）
    const val COFFEE_CUP = "coffee_cup"  // 咖啡杯（装饰）
    const val CHAT_WINDOW = "chat_window" // 对话气泡窗口（主界面定位用；编辑器中不可编辑）
}

/**
 * 默认布局：由原 Figma 左上角数值换算为中心坐标（cx = x + w/2, cy = y + h/2）。
 * 作为「底」——用户未自定义的项、以及旧数据缺失的项，都回落到这里。
 */
object DefaultLayout {

    val items: Map<String, ItemLayout> = linkedMapOf(
        // 原 x=939,y=1049,w=230,h=302
        SceneItemId.BOARD to ItemLayout(cx = 1054f, cy = 1200f, w = 230f, h = 302f),
        // 原 x=325,y=1257,w=1048,h=684
        SceneItemId.CAT to ItemLayout(cx = 849f, cy = 1599f, w = 1048f, h = 684f),
        // 原 x=-285,y=1244,w=610,h=744
        SceneItemId.PLANT to ItemLayout(cx = 20f, cy = 1616f, w = 610f, h = 744f),
        // 原 x=510.6,y=1837,w=577.32,h=342.1, rotation=1.32°
        SceneItemId.NOTEBOOK to ItemLayout(cx = 799.26f, cy = 2008.05f, w = 577.32f, h = 342.1f, rotation = 1.32f),
        // 原 x=54,y=1827,w=453,h=322
        SceneItemId.COFFEE_CUP to ItemLayout(cx = 280.5f, cy = 1988f, w = 453f, h = 322f),
        // 原 x=104,y=351,w=760,h=1000（对话窗仅主界面定位，编辑器不可编辑）
        SceneItemId.CHAT_WINDOW to ItemLayout(cx = 484f, cy = 851f, w = 760f, h = 1000f)
    )

    val layout: SceneLayout = SceneLayout(version = LAYOUT_VERSION, items = items)

    /**
     * 以默认布局为底，用用户存储的覆盖值逐项合并。
     * - 存储里缺某个 id → 用默认（新增组件/老数据自动补全）。
     * - 存储里多余/未知 id → 忽略（前向兼容）。
     */
    fun effective(stored: SceneLayout?): SceneLayout {
        if (stored == null) return layout
        val merged = LinkedHashMap<String, ItemLayout>(items.size)
        for ((id, default) in items) {
            merged[id] = stored.items[id] ?: default
        }
        return SceneLayout(version = LAYOUT_VERSION, items = merged)
    }
}

/**
 * SceneLayout JSON 编解码（含 v1 旧数据迁移）。
 *
 * v1 旧 JSON 的 ItemLayout 是 {"x","y","w","h",...}（左上角）；
 * v2 是 {"cx","cy","w","h",...}（中心）。解析时按字段探测：
 * 有 cx/cy 走 v2；只有 x/y 则迁移为 cx=x+w/2, cy=y+h/2。
 * 手动解析（而非 Gson 反射直填）是为了让旧数据零丢失升级，也便于单测。
 */
object SceneLayoutCodec {

    fun toJson(layout: SceneLayout): String = com.google.gson.Gson().toJson(layout)

    fun parse(json: String): SceneLayout {
        val root = JsonParser.parseString(json)
        if (!root.isJsonObject) return DefaultLayout.layout
        val obj = root.asJsonObject
        val version = obj.get("version")?.takeIf { it.isJsonPrimitive }?.asInt ?: 1
        val items = LinkedHashMap<String, ItemLayout>()
        val itemsEl = obj.get("items")
        if (itemsEl != null && itemsEl.isJsonObject) {
            for ((id, el) in itemsEl.asJsonObject.entrySet()) {
                if (!el.isJsonObject) continue
                val o = el.asJsonObject
                val w = o.get("w")?.takeIf { it.isJsonPrimitive }?.asFloat ?: continue
                val h = o.get("h")?.takeIf { it.isJsonPrimitive }?.asFloat ?: continue
                val rotation = o.get("rotation")?.takeIf { it.isJsonPrimitive }?.asFloat ?: 0f
                val visible = o.get("visible")?.takeIf { it.isJsonPrimitive }?.asBoolean ?: true
                val cxEl = o.get("cx")?.takeIf { it.isJsonPrimitive }
                val cyEl = o.get("cy")?.takeIf { it.isJsonPrimitive }
                val item = if (cxEl != null && cyEl != null) {
                    // v2：中心存储
                    ItemLayout(cxEl.asFloat, cyEl.asFloat, w, h, rotation, visible)
                } else {
                    // v1：左上角存储 → 迁移为中心
                    val x = o.get("x")?.takeIf { it.isJsonPrimitive }?.asFloat ?: continue
                    val y = o.get("y")?.takeIf { it.isJsonPrimitive }?.asFloat ?: continue
                    ItemLayout(x + w / 2f, y + h / 2f, w, h, rotation, visible)
                }
                items[id] = item
            }
        }
        return SceneLayout(version = version, items = items)
    }
}
