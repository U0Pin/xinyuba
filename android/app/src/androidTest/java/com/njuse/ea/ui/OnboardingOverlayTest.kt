package com.njuse.ea.ui

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.njuse.ea.ui.widget.OnboardingOverlay
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 组件级测试：新手引导聚光灯浮层（OnboardingOverlay）。
 * 直接组合真实组件、传入假 rects（无导航/无 DataStore 依赖），
 * 只测功能：欢迎页文案 → 点遮罩推进 → 最后一步触发完成；
 * 「跳过」按钮与「下一步」CTA 均已按产品要求删除（推进=点屏幕任意处，结束=返回键）；
 * 空挖孔 rect（rect 为 null）时仍正常渲染不崩溃。
 */
@RunWith(AndroidJUnit4::class)
class OnboardingOverlayTest {

    @get:Rule
    val compose = createComposeRule()

    // 三个假目标 rect（px），坐标数值本身不影响组件级行为断言
    private val fakeHoles = mapOf(
        1 to Rect(0f, 2000f, 1000f, 2200f),
        2 to Rect(40f, 100f, 300f, 360f),
        3 to Rect(900f, 1000f, 1100f, 1400f)
    )

    @Test
    fun welcomeStep_showsTitle_thenAdvancesOnTap() {
        var step = 0
        compose.setContent {
            OnboardingOverlay(
                step = step,
                holes = fakeHoles,
                onNext = { step = 1 },
                onSkip = { }
            )
        }
        compose.onNodeWithText("欢迎来到心语吧 🐾").assertIsDisplayed()
        compose.runOnIdle {
            // 欢迎页不再有「开始」CTA 按钮，推进依赖点遮罩（onNext 回调由宿主处理）
            assertTrue(step == 0)
        }
    }

    @Test
    fun holeStep_showsCardText_evenWhenRectNull() {
        // rect 为 null（目标尚未布局）时：只显示遮罩，卡片文案仍可读，不崩溃
        compose.setContent {
            OnboardingOverlay(
                step = 1,
                holes = mapOf(1 to null, 2 to null, 3 to null),
                onNext = { },
                onSkip = { }
            )
        }
        compose.onNodeWithText("和猫猫聊天").assertIsDisplayed()
    }

    @Test
    fun skipButton_and_cta_removed_byProductRequirement() {
        // 「跳过」按钮与「下一步/开始」CTA 已删除（产品要求）：任何步骤都不应出现。
        compose.setContent {
            OnboardingOverlay(step = 0, holes = fakeHoles, onNext = { }, onSkip = { })
        }
        compose.onAllNodesWithText("跳过").fetchSemanticsNodes().let { assertTrue(it.isEmpty()) }
        compose.onAllNodesWithText("下一步").fetchSemanticsNodes().let { assertTrue(it.isEmpty()) }
        compose.onAllNodesWithText("开始 →").fetchSemanticsNodes().let { assertTrue(it.isEmpty()) }
    }

    @Test
    fun finalStep_showsFarewellText() {
        compose.setContent {
            OnboardingOverlay(step = 3, holes = fakeHoles, onNext = { }, onSkip = { })
        }
        compose.onNodeWithText("你的个人画像").assertIsDisplayed()
    }
}
