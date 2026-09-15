package com.njuse.ea.ui.widget

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.njuse.ea.ui.theme.CreamWhite
import com.njuse.ea.ui.theme.GlassBorder
import com.njuse.ea.ui.theme.MainBrown50
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/**
 * 全局样式 Toast：任意界面（含 ViewModel）调 [ToastBus.show] 即可弹出
 * 半透棕色玻璃气泡。宿主 [AppToastHost] 挂在导航根部（NavHost 之上），
 * 因此页面跳转/返回不会让它消失——解决了「界面一刷新气泡就一闪而过」。
 * 不拦截触摸（无 clickable），纯展示。
 */
object ToastBus {
    private val _events = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val events = _events.asSharedFlow()
    fun show(message: String) {
        _events.tryEmit(message)
    }
}

@Composable
fun AppToastHost() {
    // text 与 visible 分离：淡出期间保留 text，文字与背景一起渐隐。
    var text by remember { mutableStateOf("") }
    var visible by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        ToastBus.events.collect {
            text = it
            visible = true
        }
    }
    LaunchedEffect(visible) {
        if (visible) {
            delay(1800)
            visible = false
        }
    }

    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
        AnimatedVisibility(visible = visible, enter = fadeIn(), exit = fadeOut()) {
            Box(
                Modifier
                    .padding(bottom = 120.dp)
                    .clip(RoundedCornerShape(24.dp))
                    .background(MainBrown50)
                    .border(1.dp, GlassBorder, RoundedCornerShape(24.dp))
                    .padding(horizontal = 28.dp, vertical = 16.dp)
            ) {
                Text(text, color = CreamWhite, fontSize = 16.sp)
            }
        }
    }
}
