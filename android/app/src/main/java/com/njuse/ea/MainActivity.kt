package com.njuse.ea

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.lifecycle.Lifecycle
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import com.njuse.ea.ui.screen.MainScreen
import com.njuse.ea.ui.screen.ProfileScreen
import com.njuse.ea.ui.screen.SettingsScreen
import com.njuse.ea.ui.screen.LayoutEditorScreen
import com.njuse.ea.ui.theme.MyApplicationTheme
import com.njuse.ea.ui.widget.AppToastHost

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MyApplicationTheme {
                EaApp()
            }
        }
    }
}

/**
 * 防双击返回：仅当当前目的地处于 RESUMED 时才执行 popBackStack。
 * 第一次点击后导航过渡期间 currentBackStackEntry 的生命周期会离开 RESUMED，
 * 过渡期内紧跟着的第二次点击被忽略——否则第二次 pop 会把栈底的 main 弹掉，
 * NavHost 空渲染导致黑屏。所有返回入口统一走此守卫。
 */
private fun NavHostController.popBackStackGuarded() {
    if (currentBackStackEntry?.lifecycle?.currentState == Lifecycle.State.RESUMED) {
        popBackStack()
    }
}

@Composable
fun EaApp() {
    val navController = rememberNavController()

    Box(Modifier.fillMaxSize()) {
        NavHost(navController = navController, startDestination = "main", modifier = Modifier.fillMaxSize()) {
            composable("main") {
                MainScreen(
                    onSettingClick = { navController.navigate("settings") },
                    onProfileClick = { navController.navigate("profile") }
                )
            }
            composable("settings") {
                SettingsScreen(
                    onBackClick = { navController.popBackStackGuarded() },
                    onEditLayout = { navController.navigate("layout-editor") }
                )
            }
            composable("layout-editor") {
                LayoutEditorScreen(
                    onBackClick = { navController.popBackStackGuarded() }
                )
            }
            composable("profile") {
                ProfileScreen(
                    onBackClick = { navController.popBackStackGuarded() }
                )
            }
        }
        // 全局样式 Toast 宿主：在导航根部，跨页面跳转不消失；不拦截触摸。
        AppToastHost()
    }
}
