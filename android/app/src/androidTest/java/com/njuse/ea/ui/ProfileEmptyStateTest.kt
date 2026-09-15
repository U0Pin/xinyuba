package com.njuse.ea.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.njuse.ea.ui.screen.ProfileEmptyCard
import com.njuse.ea.ui.screen.ProfileErrorCard
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import androidx.test.ext.junit.runners.AndroidJUnit4

/**
 * Profile 空态/错误态卡片的组件级测试（直接组合真实卡片，无网络依赖）：
 * - 空态：标题/引导文案可见，「去和猫猫聊聊」触发返回聊天回调
 * - 错误态：标题/原因文案可见，「重试」触发重载回调
 */
@RunWith(AndroidJUnit4::class)
class ProfileEmptyStateTest {

    @get:Rule
    val compose = createComposeRule()

    @Test
    fun emptyCard_showsHintText_andChatButtonTriggersCallback() {
        var chatClicked = 0
        compose.setContent { ProfileEmptyCard(onChatClick = { chatClicked++ }) }
        compose.onNodeWithText("暂无画像数据").assertIsDisplayed()
        compose.onNodeWithText("去和猫猫聊聊").assertIsDisplayed()
        compose.onNodeWithTag("profile-empty-card").assertIsDisplayed()
        compose.onNodeWithTag("profile-empty-chat-btn").performClick()
        compose.waitForIdle()
        assertEquals(1, chatClicked)
    }

    @Test
    fun errorCard_showsMessage_andRetryTriggersCallback() {
        var retried = 0
        compose.setContent { ProfileErrorCard(message = "加载失败，请检查网络后重试", onRetry = { retried++ }) }
        compose.onNodeWithText("画像加载失败").assertIsDisplayed()
        compose.onNodeWithText("加载失败，请检查网络后重试").assertIsDisplayed()
        compose.onNodeWithTag("profile-error-card").assertIsDisplayed()
        compose.onNodeWithTag("profile-retry-btn").performClick()
        compose.waitForIdle()
        assertTrue(retried == 1)
    }
}
