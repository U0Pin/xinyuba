package com.njuse.ea.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.State
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.compose.ui.test.junit4.createComposeRule
import com.njuse.ea.ui.device.rememberParallaxOffset
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import androidx.test.ext.junit.runners.AndroidJUnit4

/**
 * 视差钩子 × 跨页导航的组件级测试。
 *
 * 背景 bug：从 main 跳转 settings/profile 时，场景先「回正」再播放跳转过渡。
 * 根因：navigation-compose 在 navigate() 时立即把离场目的地 maxLifecycle 降为
 * CREATED（源码注释："Created - not visible or on the process of being animated out"），
 * 其 NavBackStackEntry 在过渡仍可见时就派发 ON_STOP；钩子的 ON_STOP 分支原本
 * state.value = Offset.Zero → 过渡期间视差层瞬间回正。
 *
 * 判据：视差已有偏移时执行 navigate，偏移不得被清零（回正）。
 * 注入方式：钩子返回的 State 实际是 mutableStateOf 创建的 MutableState，
 * 测试直接回转型断言写入非零偏移。模拟器无真实倾斜，传感器事件（若有）只会把
 * 偏移向 0 平滑衰减（乘性，永不为精确零）；而 bug 路径是精确清零——断言
 * assertNotEquals(Zero) 对两种环境都确定成立。
 */
@RunWith(AndroidJUnit4::class)
class ParallaxNavigationTest {

    @get:Rule
    val compose = createComposeRule()

    @Test
    fun parallaxOffsetSurvivesForwardNavigation() {
        var offsetState: State<Offset>? = null
        var navController: NavHostController? = null
        compose.setContent {
            val nc = rememberNavController()
            navController = nc
            NavHost(navController = nc, startDestination = "main", modifier = Modifier.fillMaxSize()) {
                composable("main") {
                    offsetState = rememberParallaxOffset(true)
                    Text("main")
                }
                composable("other") { Text("other") }
            }
        }
        compose.waitForIdle()
        assertNotNull(offsetState)
        val state = offsetState!!

        // 同一主线程回合内：注入非零偏移 + 立即跳转，杜绝中间插入传感器事件
        compose.runOnIdle {
            (state as MutableState<Offset>).value = Offset(0.5f, 0.5f)
            navController!!.navigate("other")
        }
        compose.waitForIdle()

        assertNotEquals(Offset.Zero, state.value)
    }
}
