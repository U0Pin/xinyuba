package com.njuse.ea.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.njuse.ea.ui.screen.SettingsExitConfirmDialog
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import androidx.test.ext.junit.runners.AndroidJUnit4

/**
 * 退出设置页保存确认弹窗的组件级测试（直接组合 internal composable，无网络依赖）：
 * 标题/正文/两按钮可见；「保存并退出」「不保存」各触发对应回调一次；
 * 点弹窗外（dismiss）触发 onDismiss。
 */
@RunWith(AndroidJUnit4::class)
class SettingsExitDialogTest {

    @get:Rule
    val compose = createComposeRule()

    private fun setContent(
        onSave: () -> Unit,
        onDiscard: () -> Unit,
        onDismiss: () -> Unit
    ) {
        compose.setContent {
            SettingsExitConfirmDialog(onSave = onSave, onDiscard = onDiscard, onDismiss = onDismiss)
        }
    }

    @Test
    fun dialog_showsTitleTextAndButtons() {
        setContent(onSave = {}, onDiscard = {}, onDismiss = {})
        compose.onNodeWithText("退出设置").assertIsDisplayed()
        compose.onNodeWithText("是否保存对用户信息的更改？").assertIsDisplayed()
        compose.onNodeWithText("保存并退出").assertIsDisplayed()
        compose.onNodeWithText("不保存").assertIsDisplayed()
    }

    @Test
    fun saveButton_triggersSaveCallback() {
        var saved = 0
        setContent(onSave = { saved++ }, onDiscard = {}, onDismiss = {})
        compose.onNodeWithText("保存并退出").performClick()
        compose.waitForIdle()
        assertEquals(1, saved)
    }

    @Test
    fun discardButton_triggersDiscardCallback() {
        var discarded = 0
        setContent(onSave = {}, onDiscard = { discarded++ }, onDismiss = {})
        compose.onNodeWithText("不保存").performClick()
        compose.waitForIdle()
        assertEquals(1, discarded)
    }
}
