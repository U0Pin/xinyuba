package com.njuse.ea.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import com.njuse.ea.ui.screen.RadarPendingPlaceholder
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import androidx.test.ext.junit.runners.AndroidJUnit4

/**
 * ACT 六维「生成中」占位的组件级测试（直接组合 internal composable，无网络依赖）：
 * 占位节点与引导文案可见。占位含无限转圈动画，先冻结帧时钟避免 idle 死锁。
 */
@RunWith(AndroidJUnit4::class)
class ProfilePendingStateTest {

    @get:Rule
    val compose = createComposeRule()

    @Test
    fun radarPendingPlaceholder_showsTagAndHintText() {
        compose.mainClock.autoAdvance = false
        compose.setContent { RadarPendingPlaceholder() }
        compose.onNodeWithTag("radar-pending-placeholder").assertIsDisplayed()
        compose.onNodeWithText("猫猫正在分析你的内心画像…").assertIsDisplayed()
    }
}
