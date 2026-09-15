package com.njuse.ea.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.performClick
import com.njuse.ea.ui.screen.ChatMenuContent
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import androidx.test.ext.junit.runners.AndroidJUnit4

/**
 * 组件级测试：聊天窗内容区（ChatMenuContent）——爪印按钮 Conversation/Menu 两态切换。
 * 气泡列表以插槽传入（测试用桩）。只测功能：默认态/Menu 态的内容切换、入口卡片回调
 * 不串（settings/profile 两条导航接线不串）。跨页导航链路（真实 NavHost）在
 * FullAppSmokeTest 覆盖（应用级）。纯页面布局不在此测（截图人工核对即可）。
 */
@RunWith(AndroidJUnit4::class)
class ChatMenuContentTest {

    @get:Rule
    val compose = createComposeRule()

    private fun setContent() {
        var menuOpen by mutableStateOf(false)
        compose.setContent {
            ChatMenuContent(
                menuOpen = menuOpen,
                onToggleMenu = { menuOpen = !menuOpen },
                onSettings = {},
                onProfile = {},
                scale = 0.5f,
                bubbles = { Text("b", Modifier.fillMaxSize().testTag("bubble-slot")) }
            )
        }
        compose.waitForIdle()
    }

    @Test
    fun defaultState_showsPetButtonAndBubbles() {
        setContent()
        compose.onNodeWithTag("pet-switcher").assertExists()
        compose.onNodeWithTag("bubble-slot").assertExists()
        compose.onNodeWithTag("menu-entry-settings").assertDoesNotExist()
        compose.onNodeWithTag("menu-entry-profile").assertDoesNotExist()
    }

    @Test
    fun tapPet_switchesToMenu() {
        setContent()
        compose.onNodeWithTag("pet-switcher").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("menu-entry-settings").assertExists()
        compose.onNodeWithTag("menu-entry-profile").assertExists()
        compose.onNodeWithTag("bubble-slot").assertDoesNotExist()
    }

    @Test
    fun menuEntry_firesCorrespondingCallbackOnly() {
        var profileClicked = false
        var settingsClicked = false
        var menuOpen by mutableStateOf(true)
        compose.setContent {
            ChatMenuContent(
                menuOpen = menuOpen,
                onToggleMenu = { menuOpen = !menuOpen },
                onSettings = { settingsClicked = true },
                onProfile = { profileClicked = true },
                scale = 0.5f,
                bubbles = {}
            )
        }
        compose.waitForIdle()
        compose.onNodeWithTag("menu-entry-settings").performClick()
        compose.waitForIdle()
        assertTrue("点设置卡片只应触发 settings 回调", settingsClicked && !profileClicked)
        compose.onNodeWithTag("menu-entry-profile").performClick()
        compose.waitForIdle()
        assertTrue("点足迹卡片只应触发 profile 回调", profileClicked)
    }

    @Test
    fun tapPetAgain_returnsToConversation() {
        setContent()
        compose.onNodeWithTag("pet-switcher").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("pet-switcher").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("bubble-slot").assertExists()
        compose.onNodeWithTag("menu-entry-settings").assertDoesNotExist()
        compose.onNodeWithTag("menu-entry-profile").assertDoesNotExist()
    }
}
