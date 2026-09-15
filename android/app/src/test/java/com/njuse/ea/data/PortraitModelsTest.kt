package com.njuse.ea.data

import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 画像三态模块解析的纯函数单测。
 * 覆盖：{} 空画像、模块缺失、pending、ready 合法/结构不符、未知 status、
 * 六维对象缺键/字符串数字、词数组结构、hasPendingModules。
 */
class PortraitModelsTest {

    private fun module(json: String): PortraitModule {
        val gson = com.google.gson.Gson()
        return gson.fromJson(json, PortraitModule::class.java)
    }

    // ---- parseModule 三态归一化 ----

    @Test
    fun `parseModule null raw returns None`() {
        assertEquals(ModuleState.None, parseModule<String>(null) { it.asString })
    }

    @Test
    fun `parseModule none status returns None`() {
        assertEquals(ModuleState.None, parseModule(module("""{"status":"none"}""")) { it.asString })
    }

    @Test
    fun `parseModule unknown status returns None`() {
        assertEquals(ModuleState.None, parseModule(module("""{"status":"weird"}""")) { it.asString })
    }

    @Test
    fun `parseModule pending returns Pending`() {
        assertEquals(ModuleState.Pending, parseModule(module("""{"status":"pending"}""")) { it.asString })
    }

    @Test
    fun `parseModule ready with valid value returns Ready`() {
        val state = parseModule(module("""{"status":"ready","value":"beginner"}""")) { it.asString }
        assertEquals(ModuleState.Ready("beginner"), state)
    }

    @Test
    fun `parseModule ready with missing value returns Invalid`() {
        val state = parseModule<String>(module("""{"status":"ready"}""")) { it.asString }
        assertTrue(state is ModuleState.Invalid)
    }

    @Test
    fun `parseModule ready with unparseable value returns Invalid`() {
        // 解析函数期望字符串，实际是对象 → Invalid
        val state = parseModule<String>(module("""{"status":"ready","value":{"a":1}}""")) { it.asString }
        assertTrue(state is ModuleState.Invalid)
    }

    @Test
    fun `parseModule parse throwing returns Invalid`() {
        val state = parseModule<String>(module("""{"status":"ready","value":123}""")) {
            throw IllegalArgumentException("boom")
        }
        assertTrue(state is ModuleState.Invalid)
    }

    // ---- parseActMetrics ----

    @Test
    fun `parseActMetrics full object parses all six dims`() {
        val el = JsonParser.parseString(
            """{"psychological_flexibility":0.1,"emotional_openness":0.2,"cognitive_readiness":0.3,
               "cognitive_fusion":0.4,"experiential_avoidance":0.5,"values_alignment":0.6}"""
        )
        val m = parseActMetrics(el)!!
        assertEquals(0.1f, m.psychologicalFlexibility!!, 1e-6f)
        assertEquals(0.2f, m.emotionalOpenness!!, 1e-6f)
        assertEquals(0.3f, m.cognitiveReadiness!!, 1e-6f)
        assertEquals(0.4f, m.cognitiveFusion!!, 1e-6f)
        assertEquals(0.5f, m.experientialAvoidance!!, 1e-6f)
        assertEquals(0.6f, m.valuesAlignment!!, 1e-6f)
    }

    @Test
    fun `parseActMetrics missing keys tolerated as null`() {
        val el = JsonParser.parseString("""{"psychological_flexibility":0.1}""")
        val m = parseActMetrics(el)!!
        assertEquals(0.1f, m.psychologicalFlexibility!!, 1e-6f)
        assertNull(m.emotionalOpenness)
        assertNull(m.valuesAlignment)
    }

    @Test
    fun `parseActMetrics numeric strings accepted`() {
        val el = JsonParser.parseString("""{"values_alignment":"0.7"}""")
        val m = parseActMetrics(el)!!
        assertEquals(0.7f, m.valuesAlignment!!, 1e-6f)
    }

    @Test
    fun `parseActMetrics non-object returns null`() {
        assertNull(parseActMetrics(JsonParser.parseString("[1,2]")))
    }

    // ---- parsePersonalityKey / parseValueWords ----

    @Test
    fun `parsePersonalityKey string ok non-string null`() {
        assertEquals("beginner", parsePersonalityKey(JsonParser.parseString("\"beginner\"")))
        assertNull(parsePersonalityKey(JsonParser.parseString("123")))
        assertNull(parsePersonalityKey(JsonParser.parseString("{}")))
    }

    @Test
    fun `parseValueWords array of strings ok`() {
        val words = parseValueWords(JsonParser.parseString("""["家庭","成长"]"""))!!
        assertEquals(listOf("家庭", "成长"), words)
    }

    @Test
    fun `parseValueWords non-array or mixed array returns null or filtered`() {
        assertNull(parseValueWords(JsonParser.parseString("""{"a":1}""")))
        // 混入非字符串元素时过滤掉（mapNotNull）
        val words = parseValueWords(JsonParser.parseString("""["家庭",5]"""))!!
        assertEquals(listOf("家庭"), words)
    }

    // ---- hasPendingModules / isAllNone ----

    @Test
    fun `hasPendingModules true only when a module is pending`() {
        assertFalse(PortraitResponse().hasPendingModules())
        assertFalse(
            PortraitResponse(personality = module("""{"status":"ready","value":"beginner"}""")).hasPendingModules()
        )
        assertTrue(
            PortraitResponse(act_metrics = module("""{"status":"pending"}""")).hasPendingModules()
        )
    }

    @Test
    fun `isAllNone for empty response and all-none modules`() {
        assertTrue(PortraitResponse().isAllNone())
        assertTrue(PortraitResponse(value_words = module("""{"status":"none"}""")).isAllNone())
        assertFalse(
            PortraitResponse(personality = module("""{"status":"ready","value":"beginner"}""")).isAllNone()
        )
        assertFalse(
            PortraitResponse(value_words = module("""{"status":"pending"}""")).isAllNone()
        )
    }
}
