package com.njuse.ea.ui

import com.njuse.ea.data.ActMetrics
import com.njuse.ea.data.ModuleState
import com.njuse.ea.ui.screen.MOCK_COGNITIVE_FUSION
import com.njuse.ea.ui.screen.MOCK_COGNITIVE_READINESS
import com.njuse.ea.ui.screen.MOCK_DISTRESS_TOLERANCE
import com.njuse.ea.ui.screen.MOCK_EMOTIONAL_OPENNESS
import com.njuse.ea.ui.screen.MOCK_EXPERIENTIAL_AVOIDANCE
import com.njuse.ea.ui.screen.MOCK_PSYCHOLOGICAL_FLEXIBILITY
import com.njuse.ea.ui.screen.MOCK_VALUES_ALIGNMENT
import com.njuse.ea.ui.screen.PersonalityProfiles
import com.njuse.ea.ui.screen.actMetricsToRadarAxes
import com.njuse.ea.ui.screen.mergePersonality
import com.njuse.ea.ui.screen.mergeValueWords
import com.njuse.ea.ui.screen.personalityFromServerKey
import com.njuse.ea.ui.screen.regenerateFailureMessage
import okhttp3.ResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/** 画像三态 → UI 的合并纯函数：人格枚举映射、雷达轴组装、价值词三级优先、regenerate 失败文案 */
class PortraitMergeTest {

    private fun httpException(code: Int) =
        HttpException(Response.error<Any>(code, ResponseBody.create(null, "")))

    // ---- personalityFromServerKey：九枚举 + 非法值 ----

    @Test
    fun `all nine server keys map to profiles`() {
        assertEquals(PersonalityProfiles.beginner, personalityFromServerKey("beginner"))
        assertEquals(PersonalityProfiles.nightWatcher, personalityFromServerKey("night_watcher"))
        assertEquals(PersonalityProfiles.watcher, personalityFromServerKey("watcher"))
        assertEquals(PersonalityProfiles.stormRider, personalityFromServerKey("storm_rider"))
        assertEquals(PersonalityProfiles.navigator, personalityFromServerKey("navigator"))
        assertEquals(PersonalityProfiles.anchor, personalityFromServerKey("anchor"))
        assertEquals(PersonalityProfiles.lightWeaver, personalityFromServerKey("light_weaver"))
        assertEquals(PersonalityProfiles.loneWanderer, personalityFromServerKey("lone_wanderer"))
        assertEquals(PersonalityProfiles.windMessenger, personalityFromServerKey("wind_messenger"))
    }

    @Test
    fun `unknown or null key returns null`() {
        assertNull(personalityFromServerKey("unknown_kind"))
        assertNull(personalityFromServerKey(null))
        assertNull(personalityFromServerKey(""))
    }

    // ---- mergePersonality ----

    @Test
    fun `ready with valid key wins over local`() {
        val merged = mergePersonality(ModuleState.Ready("anchor"), PersonalityProfiles.beginner)
        assertEquals(PersonalityProfiles.anchor, merged)
    }

    @Test
    fun `ready with invalid key falls back to local`() {
        val merged = mergePersonality(ModuleState.Ready("junk"), PersonalityProfiles.beginner)
        assertEquals(PersonalityProfiles.beginner, merged)
    }

    @Test
    fun `pending none and invalid fall back to local`() {
        val local = PersonalityProfiles.stormRider
        assertEquals(local, mergePersonality(ModuleState.Pending, local))
        assertEquals(local, mergePersonality(ModuleState.None, local))
        assertEquals(local, mergePersonality(ModuleState.Invalid("bad"), local))
    }

    // ---- actMetricsToRadarAxes ----

    @Test
    fun `axes order and labels unchanged with real metrics`() {
        val m = ActMetrics(
            psychologicalFlexibility = 0.9f, emotionalOpenness = 0.8f,
            cognitiveReadiness = 0.7f, cognitiveFusion = 0.6f,
            experientialAvoidance = 0.5f, valuesAlignment = 0.4f
        )
        val axes = actMetricsToRadarAxes(m, distressTolerance = 0.3f)
        assertEquals(
            listOf("走出情绪", "接纳感受", "扛住痛苦", "适合深聊", "想法绑架", "躲避情绪", "活出自己"),
            axes.map { it.first }
        )
        assertEquals(listOf(0.9f, 0.8f, 0.3f, 0.7f, 0.6f, 0.5f, 0.4f), axes.map { it.second })
    }

    @Test
    fun `null metrics fall back to mock constants`() {
        val axes = actMetricsToRadarAxes(null, distressTolerance = null)
        assertEquals(
            listOf(
                MOCK_PSYCHOLOGICAL_FLEXIBILITY, MOCK_EMOTIONAL_OPENNESS, MOCK_DISTRESS_TOLERANCE,
                MOCK_COGNITIVE_READINESS, MOCK_COGNITIVE_FUSION, MOCK_EXPERIENTIAL_AVOIDANCE,
                MOCK_VALUES_ALIGNMENT
            ),
            axes.map { it.second }
        )
    }

    @Test
    fun `partial metrics only fill missing axes with mock`() {
        val m = ActMetrics(psychologicalFlexibility = 0.2f)
        val axes = actMetricsToRadarAxes(m, distressTolerance = null)
        assertEquals(0.2f, axes[0].second, 1e-6f)
        assertEquals(MOCK_EMOTIONAL_OPENNESS, axes[1].second)
        assertEquals(MOCK_DISTRESS_TOLERANCE, axes[2].second)
    }

    @Test
    fun `out of range values are clamped into 0 and 1`() {
        val m = ActMetrics(psychologicalFlexibility = 1.5f, emotionalOpenness = -0.2f)
        val axes = actMetricsToRadarAxes(m, distressTolerance = 99f)
        assertEquals(1f, axes[0].second, 1e-6f)
        assertEquals(0f, axes[1].second, 1e-6f)
        assertEquals(1f, axes[2].second, 1e-6f)
    }

    // ---- mergeValueWords ----

    @Test
    fun `ready words win over everything`() {
        val words = mergeValueWords(ModuleState.Ready(listOf("真词")), listOf("旧词"))
        assertEquals(listOf("真词"), words)
    }

    @Test
    fun `empty ready words fall through to personal values`() {
        val words = mergeValueWords(ModuleState.Ready(emptyList()), listOf("旧词"))
        assertEquals(listOf("旧词"), words)
    }

    @Test
    fun `pending none invalid fall to personal values`() {
        for (state in listOf(ModuleState.Pending, ModuleState.None, ModuleState.Invalid("x"))) {
            assertEquals(listOf("旧词"), mergeValueWords(state, listOf("旧词")))
        }
    }

    @Test
    fun `everything empty falls to builtin words`() {
        val words = mergeValueWords(ModuleState.None, emptyList())
        assertTrue(words.contains("家庭"))
        assertEquals(10, words.size)
    }

    // ---- regenerateFailureMessage ----

    @Test
    fun `404 message mentions insufficient conversation`() {
        val msg = regenerateFailureMessage(httpException(404))
        assertTrue(msg.contains("对话记录"))
    }

    @Test
    fun `429 message mentions frequency`() {
        val msg = regenerateFailureMessage(httpException(429))
        assertTrue(msg.contains("频繁"))
    }

    @Test
    fun `other errors get generic network message`() {
        val msg = regenerateFailureMessage(httpException(500))
        assertTrue(msg.contains("网络"))
        val ioMsg = regenerateFailureMessage(java.io.IOException("timeout"))
        assertTrue(ioMsg.contains("网络"))
    }
}
