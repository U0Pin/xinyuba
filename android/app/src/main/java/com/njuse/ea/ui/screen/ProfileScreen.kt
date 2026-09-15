package com.njuse.ea.ui.screen

import androidx.annotation.DrawableRes
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.njuse.ea.R
import com.njuse.ea.data.ActMetrics
import com.njuse.ea.data.ModuleState
import com.njuse.ea.data.parseActMetrics
import com.njuse.ea.data.parseModule
import com.njuse.ea.data.parsePersonalityKey
import com.njuse.ea.data.parseValueWords
import com.njuse.ea.ui.theme.AccentTeal
import com.njuse.ea.ui.theme.ChipTextDark
import com.njuse.ea.ui.theme.CreamDim
import com.njuse.ea.ui.theme.CreamWhite
import com.njuse.ea.ui.theme.GlassBorder
import com.njuse.ea.ui.theme.GlassCard
import com.njuse.ea.ui.theme.GlassChip
import com.njuse.ea.ui.theme.GlassNav
import com.njuse.ea.ui.viewmodel.ProfileViewModel
import com.njuse.ea.ui.viewmodel.SettingsViewModel
import com.njuse.ea.ui.widget.ToastBus
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import retrofit2.HttpException

data class PersonalityProfile(
    val id: Int,
    val emoji: String,
    val nameZh: String,
    val nameEn: String,
    val tagline: String,
    @DrawableRes val imageMale: Int,
    @DrawableRes val imageFemale: Int,
    val interpretation: String
) {
    /** 按性别解析形象图片：男→男生形象，其余（女/未设置）→女生形象 */
    @DrawableRes
    fun imageRes(gender: String): Int = if (gender == "male") imageMale else imageFemale
}

object PersonalityProfiles {
    val beginner = PersonalityProfile(
        id = 0,
        emoji = "🌱",
        nameZh = "初识者",
        nameEn = "Beginner",
        tagline = "故事才刚刚开始。",
        imageMale = R.drawable.personality_beginner_male,
        imageFemale = R.drawable.personality_beginner_female,
        interpretation = "第一次见面，彼此都还陌生。你带着一点好奇、一点防备，试着把心里的话说出来。这不需要多大的勇气，只需要真实。你说的每一句，猫猫都在认真记着——慢慢地，它会越来越懂你，你也会越来越懂自己。"
    )

    val nightWatcher = PersonalityProfile(
        id = 1,
        emoji = "🌙",
        nameZh = "守夜人",
        nameEn = "Night Watcher",
        tagline = "你看得见别人呼吸时的颜色，却常忘了自己的。",
        imageMale = R.drawable.personality_night_watcher_male,
        imageFemale = R.drawable.personality_night_watcher_female,
        interpretation = "夜深了，对方的头像一直没有亮起。你把聊天记录翻来覆去看了好几遍，回想自己哪句话说错了。别人的一个停顿、一个语气，在你心里都会掀起波澜。可你要知道：不是你太敏感，是你太在乎了。这样的你，也值得把同样的心疼，分一点给自己。"
    )

    val watcher = PersonalityProfile(
        id = 2,
        emoji = "🔭",
        nameZh = "守望者",
        nameEn = "Watcher",
        tagline = "大脑像停不下来的雷达，但有些信号不需要被当真。",
        imageMale = R.drawable.personality_watcher_male,
        imageFemale = R.drawable.personality_watcher_female,
        interpretation = "凌晨两点，台灯还亮着。你盯着天花板，念头一个接一个：明天的安排、上周说错的话、还没回的消息……越想越清醒，于是你抓起手机，让自己忙到没空去想。念头跑得很快，但念头只是念头，不等于事实。下次它们再来的时候，试着只看着它们跑过去，别跟着上车。"
    )

    val stormRider = PersonalityProfile(
        id = 3,
        emoji = "🌊",
        nameZh = "风暴骑手",
        nameEn = "Storm Rider",
        tagline = "你不是不痛，是痛着继续走。",
        imageMale = R.drawable.personality_storm_rider_male,
        imageFemale = R.drawable.personality_storm_rider_female,
        interpretation = "冷浪拍岸，雷声乍响，你独自站在海边。风很大，雨很急，你也怕，也冷，也想躲——但你没有跑。你裹紧衣服，等这一阵浪过去。这就是你最了不起的地方：不是不痛，而是痛着的时候，你依然在走。这份扛事的本事是你一步一步磨出来的，它比你自己以为的更可靠。"
    )

    val navigator = PersonalityProfile(
        id = 4,
        emoji = "🧭",
        nameZh = "航海者",
        nameEn = "Navigator",
        tagline = "心里有一张地图，情绪的风吹不偏航向。",
        imageMale = R.drawable.personality_navigator_male,
        imageFemale = R.drawable.personality_navigator_female,
        interpretation = "海上起了大雾，四周白茫茫一片，看不见岸。你没有慌着乱划，而是停下来听了听心里的指南针——什么对你是重要的，你一直都知道。情绪的风会把船吹得摇晃，却很少把你吹离航向。继续跟着它走，雾会散的。"
    )

    val anchor = PersonalityProfile(
        id = 5,
        emoji = "⚓",
        nameZh = "定锚者",
        nameEn = "Anchor",
        tagline = "你行动，你执行，你搞定。但你的感受呢？",
        imageMale = R.drawable.personality_anchor_male,
        imageFemale = R.drawable.personality_anchor_female,
        interpretation = "起风的时候，所有人都在慌，你已经开始收帆、固定缆绳、检查船舱。你习惯先做事，再感受——事情一件件做完，心就稳了。只是别忘了，船固定好之后，也停下来问问自己：我现在累不累？那些被你压在行动下面的感受，也值得被听见。"
    )

    val lightWeaver = PersonalityProfile(
        id = 6,
        emoji = "✨",
        nameZh = "织光者",
        nameEn = "Light Weaver",
        tagline = "你接住了自己。这是很多人穷尽一生在学的事。",
        imageMale = R.drawable.personality_light_weaver_male,
        imageFemale = R.drawable.personality_light_weaver_female,
        interpretation = "雨刚停，云缝里漏下一束光。你没有急着把地上的水洼擦干，而是搬了把椅子，坐在旁边看了一会儿。难过来了，你让它坐一会儿；开心来了，你也接得住。你不跟情绪打架，也不被念头骗走。这种本事很多人要练一辈子，而你已经在路上了。"
    )

    val loneWanderer = PersonalityProfile(
        id = 7,
        emoji = "🏔️",
        nameZh = "独行者",
        nameEn = "Lone Wanderer",
        tagline = "你把很多人拒之门外，不是不想要，是怕门开了之后的事。",
        imageMale = R.drawable.personality_lone_wanderer_male,
        imageFemale = R.drawable.personality_lone_wanderer_female,
        interpretation = "雪山的夜，风在帐篷外面吼。你一个人煮水、一个人收拾、一个人看星星。不是不想要陪伴，是门打开之后可能发生的事，让你更害怕。所以你把最软的地方藏得很深，什么事都自己扛。扛得住是一种本事，但你也值得偶尔把重量分出去一点——比如，先分给这只猫猫。"
    )

    val windMessenger = PersonalityProfile(
        id = 8,
        emoji = "🕊️",
        nameZh = "风信使",
        nameEn = "Wind Messenger",
        tagline = "每一种人格都是一种可能，你还流动着。",
        imageMale = R.drawable.personality_wind_messenger_male,
        imageFemale = R.drawable.personality_wind_messenger_female,
        interpretation = "春天的路口，风一会儿从东边来，一会儿从西边来。你站在中间，还没有成为任何一种固定的样子。这不是迷茫，是你还在流动——今天的你和昨天的你不一样，恰恰说明你在生长。别急，风会把消息带到，你也会慢慢长成自己的形状。"
    )

    /** 全部人格，按 id 顺序，用于图鉴展示 */
    val all = listOf(
        beginner, nightWatcher, watcher, stormRider, navigator,
        anchor, lightWeaver, loneWanderer, windMessenger
    )
}

data class PersonalityMetrics(
    val psychologicalFlexibility: Float,
    val emotionalOpenness: Float,
    val distressTolerance: Float,
    val cognitiveReadiness: Float,
    val cognitiveFusion: Float,
    val experientialAvoidance: Float,
    val valuesAlignment: Float,
    val attachmentStyle: String?,
    val conversationCount: Int
)

fun determinePersonality(
    conversationCount: Int,
    attachmentStyle: String?,
    rejectionSensitivity: Float?,
    distressTolerance: Float?,
    psychologicalFlexibility: Float,
    emotionalOpenness: Float,
    cognitiveFusion: Float,
    experientialAvoidance: Float,
    valuesAlignment: Float
): PersonalityProfile {
    return when {
        conversationCount < 3 -> PersonalityProfiles.beginner
        (rejectionSensitivity ?: 0f) >= 0.60f && attachmentStyle == "anxious" -> PersonalityProfiles.nightWatcher
        cognitiveFusion >= 0.55f && experientialAvoidance >= 0.45f -> PersonalityProfiles.watcher
        (distressTolerance ?: 0f) >= 0.50f && psychologicalFlexibility >= 0.50f -> PersonalityProfiles.stormRider
        valuesAlignment >= 0.55f && psychologicalFlexibility >= 0.60f -> PersonalityProfiles.navigator
        experientialAvoidance <= 0.25f && emotionalOpenness <= 0.35f && valuesAlignment >= 0.55f -> PersonalityProfiles.anchor
        psychologicalFlexibility >= 0.70f && emotionalOpenness >= 0.55f -> PersonalityProfiles.lightWeaver
        attachmentStyle == "avoidant" || emotionalOpenness <= 0.20f -> PersonalityProfiles.loneWanderer
        else -> PersonalityProfiles.windMessenger
    }
}

// ---- 后端画像三态合并（JVM 可测纯函数）----

// 兜底数值：仅在后端 none/生成中/结构不符时使用，保证界面不塌。
// 七个轴统一取 0.7——刻意是一眼能看出的「等长满盘」（真实画像几乎不可能七维齐平），
// 不再用一组高低错落的具体数字假装是测出来的数据。
internal const val MOCK_PSYCHOLOGICAL_FLEXIBILITY = 0.7f
internal const val MOCK_EMOTIONAL_OPENNESS = 0.7f
internal const val MOCK_DISTRESS_TOLERANCE = 0.7f
internal const val MOCK_COGNITIVE_READINESS = 0.7f
internal const val MOCK_COGNITIVE_FUSION = 0.7f
internal const val MOCK_EXPERIENTIAL_AVOIDANCE = 0.7f
internal const val MOCK_VALUES_ALIGNMENT = 0.7f

/** 后端人格枚举 → 人格卡；未知/空 key 返回 null（调用方回退本地判定） */
internal fun personalityFromServerKey(key: String?): PersonalityProfile? = when (key) {
    "beginner" -> PersonalityProfiles.beginner
    "night_watcher" -> PersonalityProfiles.nightWatcher
    "watcher" -> PersonalityProfiles.watcher
    "storm_rider" -> PersonalityProfiles.stormRider
    "navigator" -> PersonalityProfiles.navigator
    "anchor" -> PersonalityProfiles.anchor
    "light_weaver" -> PersonalityProfiles.lightWeaver
    "lone_wanderer" -> PersonalityProfiles.loneWanderer
    "wind_messenger" -> PersonalityProfiles.windMessenger
    else -> null
}

/** 后端人格优先；pending/none/结构不符/未知枚举 → 本地判定结果 */
internal fun mergePersonality(server: ModuleState<String>, local: PersonalityProfile): PersonalityProfile =
    if (server is ModuleState.Ready) personalityFromServerKey(server.value) ?: local else local

/** ACT 六维（缺键用 mock 兜底）→ 雷达七轴（轴序与 explanations 键保持不变） */
internal fun actMetricsToRadarAxes(
    m: ActMetrics?,
    distressTolerance: Float?
): List<Pair<String, Float>> = listOf(
    "走出情绪" to ((m?.psychologicalFlexibility ?: MOCK_PSYCHOLOGICAL_FLEXIBILITY).coerceIn(0f, 1f)),
    "接纳感受" to ((m?.emotionalOpenness ?: MOCK_EMOTIONAL_OPENNESS).coerceIn(0f, 1f)),
    "扛住痛苦" to ((distressTolerance ?: MOCK_DISTRESS_TOLERANCE).coerceIn(0f, 1f)),
    "适合深聊" to ((m?.cognitiveReadiness ?: MOCK_COGNITIVE_READINESS).coerceIn(0f, 1f)),
    "想法绑架" to ((m?.cognitiveFusion ?: MOCK_COGNITIVE_FUSION).coerceIn(0f, 1f)),
    "躲避情绪" to ((m?.experientialAvoidance ?: MOCK_EXPERIENTIAL_AVOIDANCE).coerceIn(0f, 1f)),
    "活出自己" to ((m?.valuesAlignment ?: MOCK_VALUES_ALIGNMENT).coerceIn(0f, 1f))
)

/** 价值词三级优先：后端真词 > 旧接口 personal_values > 内置兜底 */
internal fun mergeValueWords(state: ModuleState<List<String>>, personalValues: List<String>): List<String> {
    if (state is ModuleState.Ready && state.value.isNotEmpty()) return state.value
    if (personalValues.isNotEmpty()) return personalValues
    return listOf("家庭", "成长", "诚实", "独立", "勇气", "善良", "自由", "创造", "智慧", "责任")
}

/** 「重新生成报告」失败的轻提示文案：404 无画像可判 / 429 频控 / 其他网络问题 */
fun regenerateFailureMessage(e: Throwable): String = when {
    e is HttpException && e.code() == 404 -> "还没有足够的对话记录来重新评估人格"
    e is HttpException && e.code() == 429 -> "操作太频繁啦，稍等几秒再试试"
    else -> "网络好像开小差了，稍后再试试吧"
}

@Composable
fun ProfileScreen(onBackClick: () -> Unit) {
    val viewModel: ProfileViewModel = viewModel()
    val profileState by viewModel.state.collectAsState()
    val settingsViewModel: SettingsViewModel = viewModel()
    val gender by settingsViewModel.personalityGender.collectAsState()
    val scrollState = rememberScrollState()

    // 画像三态解析：portrait 为 null（尚未拉到）视同全 none
    val portrait = profileState.portrait
    val actState = remember(portrait) { parseModule(portrait?.act_metrics, ::parseActMetrics) }
    val personalityState = remember(portrait) { parseModule(portrait?.personality, ::parsePersonalityKey) }
    val wordsState = remember(portrait) { parseModule(portrait?.value_words, ::parseValueWords) }
    val actMetrics = (actState as? ModuleState.Ready)?.value
    val personalityPending = personalityState is ModuleState.Pending

    // 人格：后端 ready 用后端枚举；否则回退本地阈值判定（旧画像字段 + mock 兜底）
    val currentProfile: PersonalityProfile = run {
        val s = profileState.status
        val local = if (s != null) {
            determinePersonality(
                conversationCount = s.conversation_count,
                attachmentStyle = s.attachment_style,
                rejectionSensitivity = s.rejection_sensitivity,
                distressTolerance = s.distress_tolerance ?: MOCK_DISTRESS_TOLERANCE,
                psychologicalFlexibility = actMetrics?.psychologicalFlexibility ?: MOCK_PSYCHOLOGICAL_FLEXIBILITY,
                emotionalOpenness = actMetrics?.emotionalOpenness ?: MOCK_EMOTIONAL_OPENNESS,
                cognitiveFusion = actMetrics?.cognitiveFusion ?: MOCK_COGNITIVE_FUSION,
                experientialAvoidance = actMetrics?.experientialAvoidance ?: MOCK_EXPERIENTIAL_AVOIDANCE,
                valuesAlignment = actMetrics?.valuesAlignment ?: MOCK_VALUES_ALIGNMENT
            )
        } else {
            PersonalityProfiles.windMessenger
        }
        mergePersonality(personalityState, local)
    }

    // 一次性轻提示（重新生成失败等）
    val transientMessage by viewModel.transientMessage.collectAsState()
    LaunchedEffect(transientMessage) {
        transientMessage?.let {
            ToastBus.show(it)
            viewModel.consumeTransientMessage()
        }
    }

    var isImageExpanded by remember { mutableStateOf(false) }
    var showPersonalityGallery by remember { mutableStateOf(false) }
    var galleryDetailProfile by remember { mutableStateOf<PersonalityProfile?>(null) }

    Box(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
        // 背景（保持现有背景图不变）+ 暗色遮罩保证可读性
        Image(painterResource(R.drawable.bg_room), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        Box(Modifier.fillMaxSize().background(Color(0x2E000000)))

        Column(Modifier.fillMaxSize().verticalScroll(scrollState).padding(horizontal = 16.dp)) {
            Spacer(Modifier.height(6.dp))

            // ===== 顶部导航 =====
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(32.dp)
                        .shadow(8.dp, RoundedCornerShape(10.dp), ambientColor = Color(0x40000000), spotColor = Color(0x40000000))
                        .clip(RoundedCornerShape(10.dp))
                        .background(GlassNav)
                        .border(1.dp, GlassBorder, RoundedCornerShape(10.dp))
                        .clickable { onBackClick() },
                    contentAlignment = Alignment.Center
                ) { Text("←", color = CreamWhite, fontSize = 14.sp) }
                Spacer(Modifier.width(10.dp))
                Text("个人画像", color = CreamWhite, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            }

            Spacer(Modifier.height(12.dp))

            val status = profileState.status
            val errorMessage = profileState.error
            if (profileState.loading) {
                Box(Modifier.fillMaxWidth().padding(80.dp), contentAlignment = Alignment.Center) {
                    Text("加载中...", color = CreamDim, fontSize = 14.sp)
                }
            } else if (profileState.empty) {
                // 后端尚未构建画像 → 空态引导，不展示编造数据
                Spacer(Modifier.height(64.dp))
                ProfileEmptyCard(onChatClick = onBackClick)
            } else if (errorMessage != null) {
                Spacer(Modifier.height(64.dp))
                ProfileErrorCard(message = errorMessage, onRetry = { viewModel.loadProfile() })
            } else if (status != null) {
                val s = status

                // ===== 卡片区：个人形象 → 内心仪表盘 → 成长轨迹 → 价值核心 =====
                Column {
                    // 综合评估报告 / 心语人格
                    // 人格由后端 ready 枚举驱动（currentProfile 顶层派生）；
                    // 「重新生成报告」走 POST regenerate + 轮询，不再本地假刷新。
                    AssessmentReport(
                        profile = currentProfile,
                        gender = gender,
                        onImageClick = { isImageExpanded = true },
                        onGalleryClick = { showPersonalityGallery = true },
                        onRegenerate = viewModel::regeneratePersonality,
                        isLoading = profileState.regenerating || personalityPending
                    )

                    Spacer(Modifier.height(10.dp))

                    // 你的内心仪表盘
                    GlassCard {
                        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("你的内心仪表盘", color = CreamWhite, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                            Spacer(Modifier.height(8.dp))
                            if (actState is ModuleState.Pending) {
                                // ACT 六维生成中：占位，轮询就绪后自动换成真实雷达图
                                RadarPendingPlaceholder()
                            } else {
                            RadarChart(
                                values = actMetricsToRadarAxes(actMetrics, s.distress_tolerance),
                                explanations = mapOf(
                                    "走出情绪" to "坏情绪来了，谁都不好受。区别只在于：有人会被它拖住一整天，有人难受一会儿，还能接着去过自己的日子。这一项看的就是你有多容易从情绪里走出来，继续做自己该做、想做的事。",
                                    "接纳感受" to "我们从小被教育要坚强——难过要忍着，生气要压着。但感受这东西，越压越反弹。这一项看的是你愿不愿意承认「我现在就是难受」，先让它待一会儿，而不是急着把它赶走。",
                                    "扛住痛苦" to "不是说不痛了，而是痛的时候你还能站稳：不冲动做决定，不做让自己后悔的事，先熬过今晚再说。这不是天生的，是一种很实在、能练出来的本事。",
                                    "适合深聊" to "刚经历糟糕的一天，人是没有力气剖析自己的。这一项看的是你现在的状态适不适合聊得深一点。如果还不到时候，我们会先陪着你，等你觉得安全了，再往深处走。",
                                    "想法绑架" to "「我肯定要搞砸了」「没人喜欢我」——这些只是脑子里闪过的念头，不一定是事实。这一项看的是你有多容易把念头当真，被它牵着走。",
                                    "躲避情绪" to "刷手机到凌晨三点、一难受就吃东西、把自己忙到停不下来——有时候我们不是在做事情，而是在躲感觉。这一项看你躲得多不多。",
                                    "活出自己" to "你每天忙的事，和你真正在乎的东西，是一回事吗？这一项看的是你的生活有没有朝着你想要的方向走。偏了不代表失败，看到了就是开始。"
                                )
                            )
                            }   // actState 非 pending
                            Spacer(Modifier.height(4.dp))
                        }
                    }

                    Spacer(Modifier.height(10.dp))

                    // 成长轨迹（三个陪伴指标并排）
                    GlassCard {
                        val companion = profileState.companion
                        val timeParts = companionTimeParts(companion?.companionship_seconds ?: (s.turn_count * 45L))
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            CompanionStat(timeParts.first, timeParts.second, "陪伴时间", Modifier.weight(1f))
                            Box(Modifier.width(1.dp).height(28.dp).background(GlassBorder))
                            CompanionStat(
                                "${companion?.agent_message_count ?: s.turn_count}", " 句",
                                "猫猫说了", Modifier.weight(1f)
                            )
                            Box(Modifier.width(1.dp).height(28.dp).background(GlassBorder))
                            CompanionStat(
                                "${companion?.days_together ?: 1}", " 天",
                                "一起经历", Modifier.weight(1f)
                            )
                        }
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "猫猫会一直陪着你 🐾",
                            color = CreamDim.copy(alpha = 0.7f),
                            fontSize = 10.sp,
                            modifier = Modifier.fillMaxWidth().wrapContentWidth(Alignment.CenterHorizontally)
                        )
                    }

                    Spacer(Modifier.height(10.dp))

                    // 价值核心（独占整行，波浪完整展示）
                    GlassCard {
                        ValuesWave(mergeValueWords(wordsState, s.personal_values))
                    }

                    Spacer(Modifier.height(10.dp))
                    Text("数据仅存储在本地设备，不会上传至服务器",
                        color = CreamDim.copy(alpha = 0.5f), fontSize = 10.sp,
                        modifier = Modifier.fillMaxWidth().wrapContentWidth(Alignment.CenterHorizontally))
                }
            }

            Spacer(Modifier.height(24.dp))
        }

        // 回到顶部
        if (scrollState.value > 300) {
            val scope = rememberCoroutineScope()
            Box(
                Modifier.align(Alignment.BottomEnd).padding(20.dp).size(48.dp)
                    .clip(CircleShape).background(GlassNav)
                    .border(1.dp, GlassBorder, CircleShape)
                    .clickable { scope.launch { scrollState.animateScrollTo(0) } },
                contentAlignment = Alignment.Center
            ) { Text("↑", color = CreamWhite, fontSize = 20.sp) }
        }

        // 人格插画放大浮层
        if (isImageExpanded) {
            val profile = currentProfile
            Box(
                Modifier
                    .fillMaxSize()
                    .background(Color(0xE6000000))
                    .clickable { isImageExpanded = false },
                contentAlignment = Alignment.Center
            ) {
                Image(
                    painter = painterResource(profile.imageRes(gender)),
                    contentDescription = profile.nameZh,
                    modifier = Modifier
                        .fillMaxWidth(0.85f)
                        .aspectRatio(0.8f)
                        .clip(RoundedCornerShape(28.dp))
                        .border(2.dp, GlassBorder, RoundedCornerShape(28.dp)),
                    contentScale = ContentScale.Crop
                )
            }
        }

        // 人格图鉴浮层（点击人格卡片插画右上角的 ··· 进入）
        if (showPersonalityGallery) {
            PersonalityGalleryOverlay(
                currentProfileId = currentProfile.id,
                gender = gender,
                onSelect = { galleryDetailProfile = it },
                onClose = { showPersonalityGallery = false }
            )
        }

        // 图鉴单人格详情浮层（盖在图鉴之上）
        galleryDetailProfile?.let { detail ->
            PersonalityDetailOverlay(
                profile = detail,
                gender = gender,
                isCurrent = detail.id == currentProfile.id,
                onBack = { galleryDetailProfile = null },
                onClose = {
                    galleryDetailProfile = null
                    showPersonalityGallery = false
                }
            )
        }
    }
}

// ==================== 玻璃卡片 ====================

@Composable
private fun GlassCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .fillMaxWidth()
            .shadow(10.dp, RoundedCornerShape(28.dp), ambientColor = Color(0x40000000), spotColor = Color(0x40000000))
            .clip(RoundedCornerShape(28.dp))
            .background(GlassCard)
            .border(1.dp, GlassBorder, RoundedCornerShape(28.dp))
            .padding(horizontal = 14.dp, vertical = 10.dp),
        content = content
    )
}

// ==================== 空态 / 错误态 ====================

/** ACT 六维生成中占位。internal 供组件测试直接组合 */
@Composable
internal fun RadarPendingPlaceholder() {
    Column(
        Modifier.fillMaxWidth().testTag("radar-pending-placeholder").padding(vertical = 60.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        CircularProgressIndicator(color = CreamDim, modifier = Modifier.size(28.dp), strokeWidth = 2.dp)
        Spacer(Modifier.height(12.dp))
        Text("猫猫正在分析你的内心画像…", color = CreamDim, fontSize = 12.sp)
    }
}

/** 后端尚未构建画像：引导去聊天，而不是展示编造数据。internal 供组件测试直接组合 */
@Composable
internal fun ProfileEmptyCard(onChatClick: () -> Unit) {
    GlassCard(Modifier.testTag("profile-empty-card")) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 36.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text("🐾", fontSize = 36.sp)
            Spacer(Modifier.height(10.dp))
            Text("暂无画像数据", color = CreamWhite, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(8.dp))
            Text(
                "猫猫还没有足够了解你～\n多聊几句话，画像就会慢慢生长出来",
                color = CreamDim,
                fontSize = 12.sp,
                lineHeight = 19.sp,
                textAlign = TextAlign.Center
            )
            Spacer(Modifier.height(18.dp))
            Box(
                Modifier.testTag("profile-empty-chat-btn")
                    .clip(RoundedCornerShape(18.dp))
                    .background(GlassChip)
                    .border(1.dp, GlassBorder, RoundedCornerShape(18.dp))
                    .clickable { onChatClick() }
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                contentAlignment = Alignment.Center
            ) { Text("去和猫猫聊聊", color = CreamWhite, fontSize = 13.sp) }
        }
    }
}

/** 画像加载失败：原因 + 重试入口。internal 供组件测试直接组合 */
@Composable
internal fun ProfileErrorCard(message: String, onRetry: () -> Unit) {
    GlassCard(Modifier.testTag("profile-error-card")) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 36.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text("💭", fontSize = 36.sp)
            Spacer(Modifier.height(10.dp))
            Text("画像加载失败", color = CreamWhite, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(8.dp))
            Text(
                message,
                color = CreamDim,
                fontSize = 12.sp,
                lineHeight = 19.sp,
                textAlign = TextAlign.Center
            )
            Spacer(Modifier.height(18.dp))
            Box(
                Modifier.testTag("profile-retry-btn")
                    .clip(RoundedCornerShape(18.dp))
                    .background(GlassChip)
                    .border(1.dp, GlassBorder, RoundedCornerShape(18.dp))
                    .clickable { onRetry() }
                    .padding(horizontal = 20.dp, vertical = 8.dp),
                contentAlignment = Alignment.Center
            ) { Text("重试", color = CreamWhite, fontSize = 13.sp, fontWeight = FontWeight.Medium) }
        }
    }
}

// ==================== 雷达图 ====================

@Composable
private fun RadarChart(
    values: List<Pair<String, Float>>,
    explanations: Map<String, String>
) {
    val n = values.size
    val angles = (0 until n).map { Math.toRadians((it * 360.0 / n - 90.0)) }
    var tooltipTarget by remember { mutableStateOf("") }

    // 入场动画：各点从原点弹出 → 面积扩散 → 脉冲
    val pointProgress = remember { values.map { Animatable(0f) } }
    val fillAlpha = remember { Animatable(0f) }
    val pulseRadius = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        pointProgress.forEachIndexed { i, anim ->
            launch { delay((300 + i * 150).toLong()); anim.animateTo(1f, tween(700, easing = FastOutSlowInEasing)) }
        }
        launch { delay(900); fillAlpha.animateTo(1f, tween(600)) }
        launch { delay(1300); pulseRadius.animateTo(1f, tween(500)) }
    }

    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Box(Modifier.size(200.dp), contentAlignment = Alignment.Center) {
            Canvas(Modifier.fillMaxSize()) {
                val c = this.center
                val maxR = size.minDimension / 2f * 0.62f
                val levels = 4
                val d = this@Canvas.density

                // 等高线（四边形环）
                for (lvl in 1..levels) {
                    val r = maxR * lvl / levels
                    val path = androidx.compose.ui.graphics.Path()
                    for (i in 0 until n) {
                        val px = c.x + kotlin.math.cos(angles[i]).toFloat() * r
                        val py = c.y + kotlin.math.sin(angles[i]).toFloat() * r
                        if (i == 0) path.moveTo(px, py) else path.lineTo(px, py)
                    }
                    path.close()
                    drawPath(path, AccentTeal.copy(alpha = if (lvl == levels) 0.35f else 0.15f), style = Stroke(width = 1f))
                }
                // 轴线
                for (angle in angles) {
                    val ex = c.x + kotlin.math.cos(angle).toFloat() * maxR
                    val ey = c.y + kotlin.math.sin(angle).toFloat() * maxR
                    drawLine(AccentTeal.copy(alpha = 0.15f), c, Offset(ex, ey), strokeWidth = 1f)
                }
                // 数据多边形
                if (n >= 3 && pointProgress[0].value > 0f) {
                    val path = androidx.compose.ui.graphics.Path()
                    val pts = values.mapIndexed { i, (_, v) ->
                        val r = maxR * v.coerceIn(0f, 1f) * pointProgress[i].value
                        Offset(c.x + kotlin.math.cos(angles[i]).toFloat() * r, c.y + kotlin.math.sin(angles[i]).toFloat() * r)
                    }
                    path.moveTo(pts[0].x, pts[0].y)
                    for (i in 1 until pts.size) path.lineTo(pts[i].x, pts[i].y)
                    path.close()
                    drawPath(path, AccentTeal.copy(alpha = 0.25f * fillAlpha.value))
                    drawPath(path, AccentTeal.copy(alpha = 0.9f * fillAlpha.value), style = Stroke(width = 1.5.dp.toPx()))
                }
                // 数据点
                values.forEachIndexed { i, (_, v) ->
                    val r = maxR * v.coerceIn(0f, 1f) * pointProgress[i].value
                    val px = c.x + kotlin.math.cos(angles[i]).toFloat() * r
                    val py = c.y + kotlin.math.sin(angles[i]).toFloat() * r
                    drawCircle(AccentTeal, radius = 5.dp.toPx(), center = Offset(px, py))
                    drawCircle(Color.White, radius = 2.dp.toPx(), center = Offset(px, py))
                }
                // 脉冲波纹
                if (pulseRadius.value > 0.01f) {
                    drawCircle(AccentTeal.copy(alpha = 0.15f * (1f - pulseRadius.value * 0.7f)),
                        radius = maxR * 0.9f * pulseRadius.value, center = c, style = Stroke(width = 1.5f))
                }
                // 轴标签（米白 12sp）
                val lp = android.graphics.Paint().apply {
                    color = 0xFFF4E6D0.toInt(); textSize = 12.sp.toPx()
                    textAlign = android.graphics.Paint.Align.CENTER; isAntiAlias = true; isFakeBoldText = true
                }
                for (i in 0 until n) {
                    val lr = maxR + 22.dp.toPx()
                    val lx = c.x + kotlin.math.cos(angles[i]).toFloat() * lr
                    val ly = c.y + kotlin.math.sin(angles[i]).toFloat() * lr + lp.textSize / 3f
                    drawContext.canvas.nativeCanvas.drawText(values[i].first, lx, ly, lp)
                }
                // 百分比（青绿 11sp）
                val pp = android.graphics.Paint().apply {
                    color = 0xFF35D0C5.toInt(); textSize = 11.sp.toPx()
                    textAlign = android.graphics.Paint.Align.CENTER; isAntiAlias = true; isFakeBoldText = true
                }
                values.forEachIndexed { i, (_, v) ->
                    val pr = maxR * v.coerceIn(0f, 1f) + 12.dp.toPx()
                    val px = c.x + kotlin.math.cos(angles[i]).toFloat() * pr
                    val py = c.y + kotlin.math.sin(angles[i]).toFloat() * pr + pp.textSize / 3f
                    drawContext.canvas.nativeCanvas.drawText("${(v * 100).toInt()}%", px, py, pp)
                }
            }

            // ! 图标：半径 = 标签半径(84dp) + 文字径向跨度 + 间隙 + 圆点半径，避免与标签重叠
            values.forEachIndexed { i, (label, _) ->
                val cosA = kotlin.math.cos(angles[i])
                val sinA = kotlin.math.sin(angles[i])
                val textHalfW = label.length * 6.0  // 12sp 中文单字宽约 12dp
                val dotR = 84.0 + kotlin.math.abs(cosA) * textHalfW + kotlin.math.abs(sinA) * 7.0 + 13.0
                InfoDot(
                    Modifier.align(Alignment.Center).offset(
                        x = (cosA * dotR).dp,
                        y = (sinA * dotR).dp
                    ),
                    tooltipTarget == label
                ) {
                    tooltipTarget = if (tooltipTarget == label) "" else label
                }
            }
        }

        // 注释展开区
        values.forEach { (label, _) ->
            AnimatedVisibility(
                visible = tooltipTarget == label,
                enter = expandVertically(tween(350, easing = FastOutSlowInEasing)) + fadeIn(tween(300)),
                exit = shrinkVertically(tween(300, easing = FastOutSlowInEasing)) + fadeOut(tween(200))
            ) {
                Box(
                    Modifier.fillMaxWidth().padding(vertical = 6.dp)
                        .clip(RoundedCornerShape(12.dp))
                        .background(Color(0xE65B351D))
                        .border(1.dp, GlassBorder, RoundedCornerShape(12.dp))
                        .padding(10.dp)
                ) {
                    Text(explanations[label] ?: "", color = CreamWhite, fontSize = 12.sp, lineHeight = 18.sp)
                }
            }
        }
    }
}

@Composable
private fun InfoDot(modifier: Modifier, active: Boolean, onClick: () -> Unit) {
    Box(
        modifier.size(18.dp).clip(CircleShape)
            .background(if (active) AccentTeal else GlassChip)
            .border(1.dp, GlassBorder, CircleShape)
            .clickable { onClick() },
        contentAlignment = Alignment.Center
    ) { Text("!", color = if (active) ChipTextDark else CreamWhite, fontSize = 10.sp, fontWeight = FontWeight.Bold) }
}

// ==================== 成长轨迹 ====================

/** 陪伴指标：大数字 + 小单位 + 底部标签，任何长度都不会被截断 */
@Composable
private fun CompanionStat(main: String, unit: String, label: String, modifier: Modifier = Modifier) {
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        Row(verticalAlignment = Alignment.Bottom) {
            Text(main, color = AccentTeal, fontSize = 16.sp, fontWeight = FontWeight.Bold)
            if (unit.isNotEmpty()) {
                Text(
                    unit,
                    color = AccentTeal.copy(alpha = 0.85f),
                    fontSize = 10.sp,
                    modifier = Modifier.padding(start = 2.dp, bottom = 1.dp)
                )
            }
        }
        Spacer(Modifier.height(2.dp))
        Text(label, color = CreamDim, fontSize = 11.sp)
    }
}

/** 陪伴秒数 → (大数字, 小单位)，如 "3"+"小时6分" / "38"+"分钟" / "186"+"小时" */
private fun companionTimeParts(seconds: Long): Pair<String, String> {
    val minutes = seconds / 60
    return when {
        minutes < 1 -> "刚刚" to "相识"
        minutes < 60 -> "$minutes" to "分钟"
        minutes < 600 -> "${minutes / 60}" to "小时${minutes % 60}分"
        else -> "${minutes / 60}" to "小时"
    }
}

// ==================== 人格评估报告 ====================

@Composable
private fun AssessmentReport(
    profile: PersonalityProfile,
    gender: String,
    onImageClick: () -> Unit,
    onGalleryClick: () -> Unit,
    onRegenerate: () -> Unit,
    isLoading: Boolean
) {
    GlassCard {
        Column(Modifier.fillMaxWidth()) {
            // IntrinsicSize.Min 让插画高度与右侧文字高度一致，不再留出大片空白
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                Box(Modifier.width(100.dp).fillMaxHeight()) {
                    Image(
                        painter = painterResource(profile.imageRes(gender)),
                        contentDescription = profile.nameZh,
                        modifier = Modifier
                            .fillMaxSize()
                            .clip(RoundedCornerShape(20.dp))
                            .border(1.dp, GlassBorder, RoundedCornerShape(20.dp))
                            .clickable { onImageClick() },
                        contentScale = ContentScale.Crop
                    )
                    // ··· 人格图鉴入口
                    Box(
                        Modifier
                            .align(Alignment.TopEnd)
                            .padding(6.dp)
                            .size(26.dp)
                            .clip(CircleShape)
                            .background(Color(0x99000000))
                            .border(1.dp, GlassBorder, CircleShape)
                            .clickable { onGalleryClick() },
                        contentAlignment = Alignment.Center
                    ) {
                        Text(
                            "···",
                            color = CreamWhite,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.offset(y = (-2).dp)
                        )
                    }
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        "${profile.emoji} ${profile.nameZh}  ·  ${profile.nameEn}",
                        color = CreamWhite,
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "「${profile.tagline}」",
                        color = CreamWhite,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Medium,
                        lineHeight = 18.sp
                    )
                    Spacer(Modifier.height(6.dp))
                    Text(
                        profile.interpretation,
                        color = CreamDim,
                        fontSize = 12.sp,
                        lineHeight = 17.sp
                    )
                }
            }

            Spacer(Modifier.height(8.dp))
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                Box(
                    Modifier.clip(RoundedCornerShape(18.dp))
                        .background(GlassChip)
                        .border(1.dp, GlassBorder, RoundedCornerShape(18.dp))
                        .clickable(enabled = !isLoading) { onRegenerate() }
                        .padding(horizontal = 12.dp, vertical = 6.dp),
                    contentAlignment = Alignment.Center
                ) {
                    if (isLoading) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(16.dp),
                            color = AccentTeal,
                            strokeWidth = 2.dp
                        )
                    } else {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("重新生成报告", color = CreamWhite, fontSize = 12.sp)
                        }
                    }
                }
            }
        }
    }
}

// ==================== 人格图鉴 ====================

@Composable
private fun PersonalityGalleryOverlay(
    currentProfileId: Int,
    gender: String,
    onSelect: (PersonalityProfile) -> Unit,
    onClose: () -> Unit
) {
    Box(
        Modifier
            .fillMaxSize()
            .background(Color(0xF2000000))
            .clickable { onClose() }
    ) {
        Column(
            Modifier
                .fillMaxSize()
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null
                ) { }
                .padding(horizontal = 20.dp)
        ) {
            Spacer(Modifier.height(45.dp))
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("心语人格图鉴", color = CreamWhite, fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(4.dp))
                    Text("九种人格，九种看待自己的方式", color = CreamDim, fontSize = 13.sp)
                }
                Box(
                    Modifier
                        .size(36.dp)
                        .clip(CircleShape)
                        .background(GlassChip)
                        .border(1.dp, GlassBorder, CircleShape)
                        .clickable { onClose() },
                    contentAlignment = Alignment.Center
                ) { Text("✕", color = CreamWhite, fontSize = 15.sp) }
            }
            Spacer(Modifier.height(20.dp))
            LazyVerticalGrid(
                columns = GridCells.Fixed(3),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
                modifier = Modifier.fillMaxSize()
            ) {
                itemsIndexed(PersonalityProfiles.all, key = { _, p -> p.id }) { index, p ->
                    PersonalityGalleryCell(
                        profile = p,
                        gender = gender,
                        isCurrent = p.id == currentProfileId,
                        enterDelayMs = index * 45,
                        onClick = { onSelect(p) }
                    )
                }
            }
        }
    }
}

@Composable
private fun PersonalityGalleryCell(
    profile: PersonalityProfile,
    gender: String,
    isCurrent: Boolean,
    enterDelayMs: Int,
    onClick: () -> Unit
) {
    // 错峰淡入 + 轻微放大
    val progress = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        delay(enterDelayMs.toLong())
        progress.animateTo(1f, tween(350, easing = FastOutSlowInEasing))
    }

    Column(
        Modifier.graphicsLayer {
            alpha = progress.value
            val s = 0.88f + 0.12f * progress.value
            scaleX = s
            scaleY = s
        },
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box {
            Image(
                painter = painterResource(profile.imageRes(gender)),
                contentDescription = profile.nameZh,
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(0.8f)
                    .clip(RoundedCornerShape(18.dp))
                    .border(
                        width = if (isCurrent) 2.dp else 1.dp,
                        color = if (isCurrent) AccentTeal else GlassBorder,
                        shape = RoundedCornerShape(18.dp)
                    )
                    .clickable { onClick() },
                contentScale = ContentScale.Crop
            )
            if (isCurrent) {
                Box(
                    Modifier
                        .align(Alignment.TopEnd)
                        .padding(6.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(AccentTeal)
                        .padding(horizontal = 6.dp, vertical = 2.dp)
                ) {
                    Text("当前", color = ChipTextDark, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                }
            }
        }
        Spacer(Modifier.height(6.dp))
        Text(
            "${profile.emoji} ${profile.nameZh}",
            color = if (isCurrent) AccentTeal else CreamWhite,
            fontSize = 13.sp,
            fontWeight = FontWeight.Medium
        )
        Text(profile.nameEn, color = CreamDim, fontSize = 10.sp)
    }
}

@Composable
private fun PersonalityDetailOverlay(
    profile: PersonalityProfile,
    gender: String,
    isCurrent: Boolean,
    onBack: () -> Unit,
    onClose: () -> Unit
) {
    Box(
        Modifier
            .fillMaxSize()
            .background(Color(0xF2000000))
            .clickable { onBack() },
        contentAlignment = Alignment.Center
    ) {
        Column(
            Modifier
                .fillMaxWidth(0.86f)
                .fillMaxHeight(0.84f)
                .shadow(16.dp, RoundedCornerShape(28.dp), ambientColor = Color(0x60000000), spotColor = Color(0x60000000))
                .clip(RoundedCornerShape(28.dp))
                .background(Color(0xFF241610))
                .border(1.dp, GlassBorder, RoundedCornerShape(28.dp))
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null
                ) { }
                .padding(20.dp)
        ) {
            // 顶部操作行：返回图鉴 / 全部关闭
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    Modifier
                        .clip(RoundedCornerShape(14.dp))
                        .background(GlassChip)
                        .border(1.dp, GlassBorder, RoundedCornerShape(14.dp))
                        .clickable { onBack() }
                        .padding(horizontal = 12.dp, vertical = 6.dp)
                ) { Text("← 图鉴", color = CreamWhite, fontSize = 13.sp) }
                Box(
                    Modifier
                        .size(32.dp)
                        .clip(CircleShape)
                        .background(GlassChip)
                        .border(1.dp, GlassBorder, CircleShape)
                        .clickable { onClose() },
                    contentAlignment = Alignment.Center
                ) { Text("✕", color = CreamWhite, fontSize = 14.sp) }
            }

            Spacer(Modifier.height(14.dp))

            Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                Image(
                    painter = painterResource(profile.imageRes(gender)),
                    contentDescription = profile.nameZh,
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(0.85f)
                        .clip(RoundedCornerShape(20.dp))
                        .border(1.dp, GlassBorder, RoundedCornerShape(20.dp)),
                    contentScale = ContentScale.Crop
                )
                Spacer(Modifier.height(14.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "${profile.emoji} ${profile.nameZh}",
                        color = CreamWhite,
                        fontSize = 20.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(profile.nameEn, color = CreamDim, fontSize = 13.sp)
                    if (isCurrent) {
                        Spacer(Modifier.weight(1f))
                        Box(
                            Modifier
                                .clip(RoundedCornerShape(10.dp))
                                .background(AccentTeal)
                                .padding(horizontal = 8.dp, vertical = 3.dp)
                        ) {
                            Text("当前人格", color = ChipTextDark, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    "「${profile.tagline}」",
                    color = CreamWhite,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Medium,
                    lineHeight = 22.sp
                )
                Spacer(Modifier.height(10.dp))
                Text(
                    profile.interpretation,
                    color = CreamDim,
                    fontSize = 14.sp,
                    lineHeight = 22.sp
                )
                if (isCurrent) {
                    Spacer(Modifier.height(12.dp))
                    Text("✦ 这是你当前的人格画像", color = AccentTeal, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                }
                Spacer(Modifier.height(14.dp))
                Text(
                    "点击空白处返回图鉴",
                    color = CreamDim.copy(alpha = 0.6f),
                    fontSize = 11.sp,
                    modifier = Modifier.fillMaxWidth().wrapContentWidth(Alignment.CenterHorizontally)
                )
            }
        }
    }
}

// ==================== 价值核心波浪 ====================

private val WaveTextColors = listOf(
    Color(0xFFF4E6D0),
    Color(0xFFE8D4B8),
    Color(0xFFD4B898),
    Color(0xFFC4A584)
)

@Composable
private fun ValuesWave(values: List<String>) {
    val displayValues = values.takeIf { it.isNotEmpty() }
        ?: listOf("家庭", "成长", "诚实", "独立", "勇气", "善良", "自由", "创造", "智慧", "责任")

    if (displayValues.isEmpty()) {
        Box(Modifier.fillMaxWidth().height(120.dp), contentAlignment = Alignment.Center) {
            Text("暂无数据", color = CreamDim, fontSize = 14.sp)
        }
        return
    }

    val columnCount = 5
    val columns = remember(displayValues) {
        List(columnCount) { col ->
            displayValues.filterIndexed { index, _ -> index % columnCount == col }
        }
    }

    val density = LocalDensity.current
    val containerHeightDp = 130.dp
    val containerHeightPx = with(density) { containerHeightDp.toPx() }
    val chipHeightPx = with(density) { 46.dp.toPx() }
    val containerBg = Color(0x30000000)

    Box(
        Modifier
            .fillMaxWidth()
            .height(containerHeightDp)
            .clip(RoundedCornerShape(16.dp))
            .background(containerBg)
            .border(1.dp, GlassBorder, RoundedCornerShape(16.dp))
    ) {
        Row(
            Modifier.fillMaxSize(),
            horizontalArrangement = Arrangement.SpaceEvenly
        ) {
            columns.forEachIndexed { colIndex, words ->
                val durationMs = 12000 + colIndex * 900
                val colPhaseOffset = colIndex * 0.22f
                val wordPhaseStep = if (words.isNotEmpty()) 1f / words.size else 0f

                WaveColumn(
                    words = words,
                    durationMs = durationMs,
                    colPhaseOffset = colPhaseOffset,
                    wordPhaseStep = wordPhaseStep,
                    containerHeightPx = containerHeightPx,
                    chipHeightPx = chipHeightPx,
                    modifier = Modifier.weight(1f).fillMaxHeight()
                )
            }
        }

        Box(
            Modifier.fillMaxWidth().height(24.dp).align(Alignment.BottomCenter)
                .background(Brush.verticalGradient(listOf(containerBg, Color.Transparent)))
        )
        Box(
            Modifier.fillMaxWidth().height(24.dp).align(Alignment.TopCenter)
                .background(Brush.verticalGradient(listOf(Color.Transparent, containerBg)))
        )
    }
}

@Composable
private fun WaveColumn(
    words: List<String>,
    durationMs: Int,
    colPhaseOffset: Float,
    wordPhaseStep: Float,
    containerHeightPx: Float,
    chipHeightPx: Float,
    modifier: Modifier
) {
    val infiniteTransition = rememberInfiniteTransition(label = "waveCol")
    val baseProgress by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            tween(durationMs, easing = LinearEasing),
            RepeatMode.Restart
        ),
        label = "base"
    )

    Box(modifier) {
        words.forEachIndexed { wordIndex, word ->
            val totalPhase = (baseProgress + colPhaseOffset + wordIndex * wordPhaseStep) % 1f

            val totalRange = containerHeightPx + 2f * chipHeightPx
            val translateY = containerHeightPx + chipHeightPx - totalPhase * totalRange

            val opacity = when {
                totalPhase < 0.18f -> totalPhase / 0.18f
                totalPhase < 0.75f -> 1f
                else -> 1f - (totalPhase - 0.75f) / 0.25f
            }

            val textColor = WaveTextColors[wordIndex % WaveTextColors.size]
            val verticalText = word.toCharArray().joinToString("\n")

            Text(
                text = verticalText,
                color = textColor,
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium,
                letterSpacing = 2.sp,
                lineHeight = 16.sp,
                textAlign = TextAlign.Center,
                modifier = Modifier
                    .graphicsLayer {
                        translationY = translateY
                        alpha = opacity
                    }
                    .clip(RoundedCornerShape(10.dp))
                    .background(Color(0x20F4E6D0))
                    .border(1.dp, GlassBorder, RoundedCornerShape(10.dp))
                    .padding(horizontal = 9.dp, vertical = 7.dp)
            )
        }
    }
}

// ==================== 标签映射 ====================

private fun riskLabel(state: String): String = when (state) {
    "SAFE" -> "🟢 安全"
    "LOW_RISK" -> "🟡 低风险"
    "MEDIUM_RISK" -> "🟠 中等风险"
    "HIGH_RISK" -> "🔴 高风险"
    "CRISIS" -> "⛔ 危机"
    else -> state.ifEmpty { "暂无" }
}
