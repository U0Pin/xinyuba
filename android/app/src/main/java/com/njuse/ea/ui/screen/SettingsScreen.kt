package com.njuse.ea.ui.screen

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.njuse.ea.R
import com.njuse.ea.ui.theme.AccentTeal
import com.njuse.ea.ui.theme.ChipSelected
import com.njuse.ea.ui.theme.ChipTextDark
import com.njuse.ea.ui.theme.CreamDim
import com.njuse.ea.ui.theme.CreamWhite
import com.njuse.ea.ui.theme.GlassBorder
import com.njuse.ea.ui.theme.GlassCard
import com.njuse.ea.ui.theme.GlassChip
import com.njuse.ea.ui.theme.GlassNav
import com.njuse.ea.ui.theme.SwitchOffTrack
import com.njuse.ea.ui.theme.TealSwitch
import com.njuse.ea.ui.theme.TextLight
import com.njuse.ea.ui.viewmodel.SettingsViewModel
import com.njuse.ea.ui.widget.ToastBus
import kotlinx.coroutines.launch
import androidx.activity.compose.BackHandler

@Composable
fun SettingsScreen(onBackClick: () -> Unit, onEditLayout: () -> Unit = {}) {
    val viewModel: SettingsViewModel = viewModel()
    val parallaxEnabled by viewModel.parallaxEnabled.collectAsState()
    val rainEnabled by viewModel.rainEnabled.collectAsState()
    val userInfo by viewModel.editingUserInfo.collectAsState()   // 编辑中的值（暂存，未落盘）
    val dirty by viewModel.isUserInfoDirty.collectAsState()
    var showConfirmDialog by remember { mutableStateOf(false) }
    var confirmDialogType by remember { mutableStateOf("") }
    var toastMessage by remember { mutableStateOf<String?>(null) }
    var editingNickname by remember { mutableStateOf(false) }
    var editingAge by remember { mutableStateOf(false) }
    var showExitDialog by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    // 退出确认：用户信息有未保存改动时先问是否保存（与布局编辑页同模式）
    val requestExit = {
        if (dirty) showExitDialog = true else onBackClick()
    }
    BackHandler { requestExit() }

    LaunchedEffect(toastMessage) {
        toastMessage?.let { ToastBus.show(it); toastMessage = null }
    }

    Box(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        // 背景（保持现有背景图不变）
        Image(painterResource(R.drawable.bg_room), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)

        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)
        ) {
            Spacer(Modifier.height(6.dp))

            // ===== 顶部导航 =====
            Row(verticalAlignment = Alignment.CenterVertically) {
                GlassBackButton { requestExit() }
                Spacer(Modifier.width(10.dp))
                Text("设置", color = CreamWhite, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            }

            Spacer(Modifier.height(12.dp))

            // ===== 会话 =====
            GlassSectionLabel("会话")
            GlassCard {
                GlassSettingRow("重置对话", "清空本轮对话历史") {
                    GlassIconButton {
                        showConfirmDialog = true; confirmDialogType = "conversation"
                    }
                }
                GlassDivider()
                GlassSettingRow("重置形象", "同时清空对话与用户画像") {
                    GlassIconButton(catIcon = true) {
                        showConfirmDialog = true; confirmDialogType = "profile"
                    }
                }
            }

            Spacer(Modifier.height(8.dp))

            // ===== 外观 =====
            GlassSectionLabel("外观")
            GlassCard {
                GlassSettingRow("陀螺仪视觉", "倾斜手机查看场景视差效果") {
                    TealSwitch(parallaxEnabled) { viewModel.setParallaxEnabled(it) }
                }
                GlassDivider()
                GlassSettingRow("下雨效果", "街景雨滴粒子动画") {
                    TealSwitch(rainEnabled) { viewModel.setRainEnabled(it) }
                }
            }

            Spacer(Modifier.height(8.dp))

            // ===== 布局 =====
            GlassSectionLabel("布局")
            GlassCard {
                GlassSettingRow("自定义布局", "拖动调整场景物品的位置、大小与旋转") {
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Box(
                            Modifier.clip(RoundedCornerShape(12.dp))
                                .background(GlassChip)
                                .border(1.dp, GlassBorder, RoundedCornerShape(12.dp))
                                .clickable {
                                    showConfirmDialog = true; confirmDialogType = "layout"
                                }
                                .padding(horizontal = 12.dp, vertical = 5.dp),
                            contentAlignment = Alignment.Center
                        ) { Text("重置", color = CreamDim, fontSize = 11.sp) }
                        Box(
                            Modifier.clip(RoundedCornerShape(12.dp))
                                .background(GlassChip)
                                .border(1.dp, GlassBorder, RoundedCornerShape(12.dp))
                                .clickable { onEditLayout() }
                                .padding(horizontal = 12.dp, vertical = 5.dp),
                            contentAlignment = Alignment.Center
                        ) { Text("编辑", color = CreamWhite, fontSize = 11.sp) }
                    }
                }
            }

            Spacer(Modifier.height(8.dp))

            // ===== 用户信息 =====
            GlassSectionLabel("用户信息")
            GlassCard {
                GlassEditRow("昵称", userInfo.nickname, editingNickname, "给自己取个名字吧",
                    onEdit = { editingNickname = true },
                    onDone = { viewModel.updateNickname(it.take(12)); editingNickname = false },
                    onCancel = { editingNickname = false })
                GlassDivider()
                // 性别
                Column(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
                    Text("性别", color = CreamWhite, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    Text("选项会影响个人画像中的人格形象哦", color = CreamDim, fontSize = 11.sp)
                    Spacer(Modifier.height(5.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        for ((k, v) in listOf("male" to "男", "female" to "女", "" to "未设置")) {
                            ChoicePill(v, userInfo.gender == k) { viewModel.updateGender(k) }
                        }
                    }
                }
                GlassDivider()
                GlassEditRow("年龄", if (userInfo.age > 0) "${userInfo.age}岁" else "", editingAge, "输入年龄 (1-120)",
                    keyboardType = KeyboardType.Number,
                    onEdit = { editingAge = true },
                    onDone = { viewModel.updateAge(it.toIntOrNull()?.coerceIn(1, 120) ?: userInfo.age); editingAge = false },
                    onCancel = { editingAge = false })
                GlassDivider()
                // 语气偏好
                Column(Modifier.fillMaxWidth().padding(vertical = 5.dp)) {
                    Text("语气偏好", color = CreamWhite, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(5.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        for ((k, v) in listOf("gentle" to "温和", "lively" to "活泼")) {
                            ChoicePill(v, userInfo.tonePreference == k) { viewModel.updateTonePreference(k) }
                        }
                    }
                }
            }

            Spacer(Modifier.height(8.dp))

            // ===== 帮助 =====
            GlassSectionLabel("帮助")
            GlassCard {
                GlassSettingRow("新手引导", "重新查看基本功能的位置") {
                    Box(
                        Modifier.clip(RoundedCornerShape(12.dp))
                            .background(GlassChip)
                            .border(1.dp, GlassBorder, RoundedCornerShape(12.dp))
                            .clickable {
                                viewModel.replayOnboarding()
                                onBackClick()
                            }
                            .padding(horizontal = 12.dp, vertical = 5.dp),
                        contentAlignment = Alignment.Center
                    ) { Text("查看", color = CreamWhite, fontSize = 11.sp) }
                }
            }

            Spacer(Modifier.height(24.dp))
        }

        // ===== 确认弹窗 =====
        if (showConfirmDialog) {
            AlertDialog(
                onDismissRequest = { showConfirmDialog = false },
                containerColor = Color(0xFF6B4A38),
                titleContentColor = CreamWhite,
                textContentColor = CreamDim,
                title = {
                    Text(
                        when (confirmDialogType) {
                            "conversation" -> "重置对话"
                            "layout" -> "重置布局"
                            else -> "重置形象"
                        },
                        fontSize = 15.sp
                    )
                },
                text = {
                    Text(
                        when (confirmDialogType) {
                            "conversation" -> "确定要清空本轮对话吗？\n用户画像和设置将保留。"
                            "layout" -> "确定要恢复默认布局吗？\n你自定义的物品位置、大小与旋转将被还原。"
                            else -> "确定要重置形象吗？\n这将清空对话记录和用户画像数据。"
                        },
                        fontSize = 12.sp
                    )
                },
                confirmButton = {
                    TextButton(onClick = {
                        showConfirmDialog = false
                        toastMessage = when (confirmDialogType) {
                            "conversation" -> { viewModel.resetConversation(); "对话已重置" }
                            "layout" -> { viewModel.resetLayout(); "已恢复默认布局" }
                            else -> { viewModel.resetProfile(); "形象与对话已重置" }
                        }
                    }) { Text("确定", color = CreamWhite, fontSize = 13.sp) }
                },
                dismissButton = {
                    TextButton(onClick = { showConfirmDialog = false }) {
                        Text("取消", color = CreamDim, fontSize = 13.sp)
                    }
                }
            )
        }

        // ===== 退出保存确认弹窗 =====
        if (showExitDialog) {
            SettingsExitConfirmDialog(
                onSave = {
                    showExitDialog = false
                    scope.launch {
                        // 先落 DataStore，成功才退出；后端 PUT 在 VM 内尽力而为
                        val ok = viewModel.saveUserInfoEdits()
                        ToastBus.show(if (ok) "已保存" else "保存失败，请稍后再试")
                        if (ok) onBackClick()
                    }
                },
                onDiscard = {
                    showExitDialog = false
                    viewModel.discardUserInfoEdits()
                    onBackClick()
                },
                onDismiss = { showExitDialog = false }
            )
        }
    }
}

/**
 * 退出设置页确认弹窗：保存并退出 / 不保存（回滚）。样式与布局编辑页的退出
 * 确认一致。internal 供组件测试直接组合。
 */
@Composable
internal fun SettingsExitConfirmDialog(
    onSave: () -> Unit,
    onDiscard: () -> Unit,
    onDismiss: () -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Color(0xFF6B4A38),
        titleContentColor = CreamWhite,
        textContentColor = CreamDim,
        title = { Text("退出设置", fontSize = 18.sp) },
        text = { Text("是否保存对用户信息的更改？", fontSize = 14.sp) },
        confirmButton = {
            TextButton(onClick = onSave) { Text("保存并退出", color = CreamWhite, fontSize = 16.sp) }
        },
        dismissButton = {
            TextButton(onClick = onDiscard) { Text("不保存", color = CreamDim, fontSize = 16.sp) }
        }
    )
}

// ==================== 玻璃组件库 ====================

@Composable
private fun GlassBackButton(onClick: () -> Unit) {
    Box(
        Modifier
            .testTag("settings-back")
            .size(32.dp)
            .shadow(8.dp, RoundedCornerShape(10.dp), ambientColor = Color(0x40000000), spotColor = Color(0x40000000))
            .clip(RoundedCornerShape(10.dp))
            .background(GlassNav)
            .border(1.dp, GlassBorder, RoundedCornerShape(10.dp))
            .clickable { onClick() },
        contentAlignment = Alignment.Center
    ) { Text("←", color = CreamWhite, fontSize = 14.sp) }
}

@Composable
private fun GlassSectionLabel(title: String) {
    Text(title, color = CreamDim, fontSize = 12.sp,
        modifier = Modifier.padding(start = 8.dp, bottom = 4.dp))
}

@Composable
private fun GlassCard(content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxWidth()
            .shadow(10.dp, RoundedCornerShape(28.dp), ambientColor = Color(0x40000000), spotColor = Color(0x40000000))
            .clip(RoundedCornerShape(28.dp))
            .background(GlassCard)
            .border(1.dp, GlassBorder, RoundedCornerShape(28.dp))
            .padding(horizontal = 14.dp, vertical = 4.dp),
        content = content
    )
}

@Composable
private fun GlassDivider() {
    Box(Modifier.fillMaxWidth().height(1.dp).alpha(0.25f).background(CreamWhite))
}

@Composable
private fun GlassSettingRow(label: String, desc: String? = null, trailing: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(label, color = CreamWhite, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            if (desc != null) Text(desc, color = CreamDim, fontSize = 11.sp)
        }
        trailing()
    }
}

@Composable
private fun GlassIconButton(catIcon: Boolean = false, onClick: () -> Unit) {
    Box(
        Modifier.size(40.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(GlassChip)
            .border(1.dp, GlassBorder, RoundedCornerShape(12.dp))
            .clickable { onClick() },
        contentAlignment = Alignment.Center
    ) {
        if (catIcon) {
            Image(painterResource(R.drawable.cat_agent), "猫咪", Modifier.size(28.dp).clip(CircleShape), contentScale = ContentScale.Crop)
        } else {
            Image(painterResource(R.drawable.ic_trash), "重置", Modifier.size(18.dp))
        }
    }
}

@Composable
private fun TealSwitch(checked: Boolean, onChange: (Boolean) -> Unit) {
    Switch(
        checked = checked,
        onCheckedChange = onChange,
        colors = SwitchDefaults.colors(
            checkedThumbColor = Color.White,
            checkedTrackColor = TealSwitch,
            uncheckedThumbColor = Color.White.copy(alpha = 0.7f),
            uncheckedTrackColor = SwitchOffTrack,
            uncheckedBorderColor = Color.Transparent
        )
    )
}

@Composable
private fun ChoicePill(text: String, selected: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.width(58.dp).height(30.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(if (selected) ChipSelected else GlassChip)
            .border(1.dp, if (selected) CreamWhite.copy(alpha = 0.5f) else GlassBorder, RoundedCornerShape(12.dp))
            .clickable { onClick() },
        contentAlignment = Alignment.Center
    ) {
        Text(text, color = if (selected) ChipTextDark else CreamWhite, fontSize = 11.sp)
    }
}

@Composable
private fun GlassEditRow(
    label: String, value: String, isEditing: Boolean, placeholder: String,
    keyboardType: KeyboardType = KeyboardType.Text,
    onEdit: () -> Unit, onDone: (String) -> Unit, onCancel: () -> Unit
) {
    val fieldState = remember(isEditing) { TextFieldState(value) }
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(label, color = CreamWhite, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            if (isEditing) {
                Spacer(Modifier.height(6.dp))
                Box {
                    BasicTextField(
                        state = fieldState,
                        textStyle = TextStyle(color = CreamWhite, fontSize = 12.sp),
                        cursorBrush = SolidColor(CreamWhite),
                        keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
                        lineLimits = androidx.compose.foundation.text.input.TextFieldLineLimits.SingleLine,
                        modifier = Modifier.fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .background(GlassChip)
                            .border(1.dp, GlassBorder, RoundedCornerShape(10.dp))
                            .padding(horizontal = 12.dp, vertical = 7.dp)
                    )
                    if (fieldState.text.isEmpty()) {
                        Text(placeholder, color = CreamDim.copy(alpha = 0.5f), fontSize = 12.sp,
                            modifier = Modifier.padding(horizontal = 14.dp, vertical = 7.dp))
                    }
                }
            } else {
                Text(value.ifEmpty { "未设置" },
                    color = if (value.isEmpty()) CreamDim.copy(alpha = 0.5f) else CreamDim,
                    fontSize = 12.sp)
            }
        }
        if (isEditing) {
            TextButton(onClick = { onDone(fieldState.text.toString()) }) { Text("完成", color = AccentTeal, fontSize = 12.sp) }
            TextButton(onClick = onCancel) { Text("取消", color = CreamDim, fontSize = 12.sp) }
        } else {
            TextButton(onClick = onEdit) { Text("编辑", color = CreamDim, fontSize = 12.sp) }
        }
    }
}
