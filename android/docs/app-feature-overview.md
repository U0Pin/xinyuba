# 心语吧 Android 客户端 · 功能总览

> 本文档描述本仓库 Android 客户端当前的功能形态，供评审与二次开发参考。
> 后端（Python / FastAPI）见仓库 [`backend/`](../../backend/)，其架构说明见 [`backend/ARCHITECTURE.md`](../../backend/ARCHITECTURE.md)。
> 面向 AI Agent 的架构与开发规范见 [../AGENTS.md](../AGENTS.md)；各模块实现细节见对应源文件头注释。

## 0. 应用概况

| 项 | 值 |
| --- | --- |
| 应用名 | 心语吧 |
| 定位 | 情绪疏导陪伴 Agent 的安卓客户端：雨天咖啡馆场景 + AI 猫咪对话 |
| 技术栈 | Kotlin + Jetpack Compose（单模块 `:app`）；Retrofit + OkHttp + Gson；DataStore Preferences；Navigation Compose；零第三方动画/特效库 |
| SDK | applicationId / namespace `com.njuse.ea`，compileSdk 37 / targetSdk 37 / minSdk 24 |
| 构建 | AGP 9.2.1 + Kotlin 2.2，需 JDK 21 工具链 |
| 后端 | 默认连生产演示后端 `https://ea.eznju.com/`（硬编码在 `ChatRepository.BASE_URL`）；接口文档 https://ea.eznju.com/docs ，本地自建见 `backend/README.md` |
| 用户标识 | `Settings.Secure.ANDROID_ID`（无账号体系，设备即用户） |

页面结构（`MainActivity.kt`，Navigation Compose 单 Activity）：

```
main（主界面：场景 + 对话）
 ├─ settings（设置）─→ layout-editor（布局编辑器）
 └─ profile（个人画像）
```

导航栈根部另挂一个全局 Toast 宿主 `AppToastHost`，因此页面跳转/返回时提示气泡不会消失；所有返回入口统一走 `popBackStackGuarded()`（仅当前目的地处于 RESUMED 才 pop），防连点导致把栈底 main 弹掉黑屏。

---

## 1. 主界面：雨天咖啡馆场景

主界面是本 App 的门面，由「背景层 + 场景物品 + 对话气泡窗口 + 输入栏」四部分按固定 Z 序叠成，全部按 Figma 设计稿坐标系（画布 1080×2400，cafe 背景图 1188×2642）等比映射到真实屏幕。

**Z 序与分层视差**（`MainScreen.kt`）：

| 层 | 内容 | 视差行为 |
| --- | --- | --- |
| L0 | 背景图 `bg_landscape`（Crop 铺满，永不露边） | 完全不动 |
| L0.5 | 雨滴粒子 Canvas | 不参与视差（户外远景层） |
| L1 | cafe_room 背景 + 5 个场景物品 | 整层 zoom + translation，一起平移 |
| L2 | 对话气泡窗口（含爪印按钮与菜单卡片） | 平移量为 L1 的 2 倍，制造前层深度感 |
| — | 底部输入栏 | 不参与视差；仅它在键盘弹出时上移 |

- **场景物品**（`SceneItemId`）：黑板（点击进个人画像）、Agent 猫咪（点击有反馈动画）、盆栽（装饰）、笔记本（装饰）、咖啡杯（装饰）。
- **对话窗口**：设计框 760×1000 @ (104,351)，窗内可上下滑动；气泡靠近视口上下边缘时淡出（150dp 渐变带），超高气泡不会消失。
- **入口收口**：设置与画像入口统一放在聊天窗顶部的爪印按钮菜单里，场景物品不再承载入口（黑板除外）。
- **坐标换算**：物品用「设计中心坐标 + 尺寸 + 旋转」存放，`SceneScale`／`DesignBox` 负责换算；物品相对 cafe 背景的位置在任何分辨率下恒定。

### 1.1 陀螺仪视差（`ui/device/Parallax.kt`、`ParallaxBiasTracker.kt`）

倾斜手机，整个咖啡馆场景（含物品）与对话气泡按不同深度轻微位移，产生空间感。可在设置页开关（默认开启）。

- 传感器：优先 `TYPE_GAME_ROTATION_VECTOR`；不可用时回落「加速度计 + 磁场」经 `getRotationMatrix` 求姿态；仿真器/无传感器恒返回零偏移（等于关闭，零退化）。
- 手感曲线：`tanh(角度/0.6)` 把角度收敛到 [-1,1]——**接近最大偏移时移动放缓**，不会撞到硬边界；再经「死区 0.03 → 边缘发硬曲线 → 逐事件低通 0.85」滤波，抑制手抖与静止漂移。
- 零位校准：首次姿态即零位；**角速度连续低于阈值满 1 秒**（＝安静下来了）或回到前台时，用 600ms 缓入缓出平滑滑回中心（不闪现）。切页面不会再立刻回正（`ON_STOP` 只停传感器，不清零偏移）。
- 视差幅度：L1 整层 X 方向 4% / Y 方向 1.5% 屏宽为 bleed，位移被 clamp 在该范围内 → 咖啡馆永远铺满屏幕不露背景。

### 1.2 下雨效果（`ui/device/Rain.kt`）

- 约 60 滴细雨，75° 向右下斜落，1dp 线宽、alpha 0.18~0.28，单 Canvas 一次 draw pass 画完。
- 固定粒子池 + 取模回绕，零对象分配（无 GC 抖动）；`withFrameNanos` 驱动，每帧仅一次重画。
- 设置页可开关（默认开启）；关闭时协程取消、零 CPU；切后台/息屏随 Composition 生命周期自动停。

### 1.3 Agent 猫咪动画（`ui/device/AgentAnimationController.kt`、`FrameAnimation.kt`）

四个 15fps 逐帧片段（帧数自动探测资源，改素材无需改代码）：

| 片段 | 素材 | 行为 |
| --- | --- | --- |
| IDLE | `cat_idle` | 循环；每播 2~4 次插一次 IDLE_2 |
| IDLE_2 | `cat_idle_2` | 单次，播完回 IDLE |
| TOUCHED | `cat_touched` | 点击猫咪触发，单次；再点可立刻重播 |
| THINKING | `cat_think` | 发送消息触发，单次；**点击可被打断**（点击总有反馈） |

- 切换不做「必播完才切」，任何状态间切换都用 150ms crossfade（旧帧垫底、新帧淡入，接力式叠化）过渡，配合果冻压扁与运动模糊掩蔽姿态跳变；AI 素材首尾帧不一致造成的循环接缝也由此抹平。
- 每帧仅 1 张 bitmap 存活（缓存到下一帧切换），坏帧解码失败渲染为空不崩溃；墙钟推导帧索引，掉帧后自动纠偏。
- 动画素材替换一律走 `scripts/convert_png_to_webp.py`（见 README）。

### 1.4 对话气泡区与输入栏

- **气泡**：Agent/用户分别左/右对齐；Agent 回复按 `\n` 自动拆成多个独立气泡段（每段参与各自的淡入淡出计算），首段上方显示「时间 + 颜文字」标签；气泡圆角 40、深棕底、最大宽度 700 设计px。
- **流式上屏**：后端每推一个 token 就立即贴进气泡（人肉延迟为零），配合 `animateScrollToItem` 自动滚动到最新段。
- **爪印按钮**：聊天窗顶部圆形爪印按钮切换 Conversation / Menu 两态（Crossfade 200ms）；Menu 态显示「设置 SETTING」「足迹 FOOT PRINT」两张玻璃入口卡片。
- **输入栏**：圆角胶囊输入框 + 发送键；空态显示「有什么想聊的吗？」占位；发送/同步中发送键降透明度并禁用点击；多行时输入栏可生长（上限 400 设计px），键盘弹出时上移（`imePadding`）。
- **果冻按压反馈**（`TapFeedback.kt`）：按下 60ms 快速压扁（横向微扩张），松手阻尼弹簧弹回；用于发送键、爪印按钮、场景物品等静态可点元素（避免与猫咪自身动画叠加）。

### 1.5 待处理状态与错误恢复（`ChatViewModel`）

- `Pending.Thinking`（猫猫思索中…）/ `Syncing`（猫猫同步中…）会在气泡区显示带循环省略号的占位气泡。
- 请求失败时显示 `Pending.Error`：错误码（HTTP 码 / `NETWORK` / `UNKNOWN`）+ 提示文案 + **刷新按钮**。
- **超时恢复策略**：若本次提问超时未应答，点刷新会先同步服务端历史——后端其实已生成答案则直接滚到答案开头；后端没处理则自动重发本题。
- 若回复已流出部分内容后连接中断，则保留已收到的部分并落库，不覆盖成错误占位。

---

## 2. 聊天与数据（`data/`）

### 2.1 SSE 流式对话

`POST /chat` 为 SSE（`text/event-stream`），不走 Retrofit，由 `ChatRepository.streamChat` 用 OkHttp 裸流实现：

- 请求头声明 `Accept: text/event-stream` + `Accept-Encoding: identity`（禁用压缩，避免代理攒批）+ `Cache-Control: no-cache`，保证逐字上屏；SSE 专用 OkHttpClient 不挂 BODY 日志拦截器（它会缓冲整个响应体，破坏流式）。
- 逐行解析 `event: token`（增量文本，JSON 字符串字面量）与 `event: final`（`risk_state`、`crisis` 风险标志）；流中可被协程取消（`ensureActive`）。

### 2.2 会话解析与历史

- 默认会话 id 进程内缓存：优先用缓存 → 列会话取 default/第一个 → 都没有则新建会话；解析失败则留空，由后端在 `/chat` 时隐式使用默认会话（因此**即使会话解析失败也照样能聊**）。
- 拉取历史（`GET /sessions/{id}/history`，page_size 100）后整体替换本地并反转为时间顺序；后端 ISO8601 带时区/微秒精度的时间串会被显式按时区解析成 epoch ms。
- **本地持久化**：`ChatHistoryStore`（DataStore 文件 `ea_chat_history`，单 key 存 JSON），最多保留最近 **200 条**；`clear()` 同时广播清除事件，各页面即时响应。

### 2.3 重置能力

| 操作 | 行为 |
| --- | --- |
| 重置对话 | 清本地历史 + `POST /sessions/{id}/reset`（会话不存在视为成功） |
| 重置形象 | 清本地历史 + 重置后端会话（连带清画像）+ 清空本地用户信息 + 用全空请求体覆盖后端用户信息 |

---

## 3. 个人画像页（`ProfileScreen.kt`）

入口：聊天窗爪印菜单的「足迹」，或点击场景中的黑板。背景为房间图 + 暗色遮罩，卡片为玻璃拟态风格。

**页面三态**：加载中 → 空态（后端尚未构建画像时展示引导卡，提示多聊几句，**不编造样例数据**）→ 错误态（提示 + 重试）。卡片区自上而下：

1. **综合评估报告 / 心语人格卡**
   - 九种人格（`PersonalityProfiles`）：初学者、夜巡者、守望者、风暴骑手、航海者、定锚者、织光者、独行者、风信使；每种含 emoji、中英文名、一句话标语、长段解读、**男/女两套插画**。
   - 人格来源：优先用后端 `portrait.personality` 枚举（`beginner`/`night_watcher`/…）；后端未就绪时回退到本地阈值判定（对话轮数、依恋风格、拒绝敏感度、六维 ACT 指标 + mock 兜底）。
   - 交互：点插画全屏放大；插画右上角 `···` 进「人格图鉴」（九宫格，当前人格高亮 + 错峰淡入，点任一格看详情）；右下角「重新生成报告」按钮（转圈态，走 `POST …/portrait/personality/regenerate`）。
2. **你的内心仪表盘**：七轴雷达图（走出情绪 / 接纳感受 / 扛住痛苦 / 适合深聊 / 想法绑架 / 躲避情绪 / 活出自己），每轴都可点「叹号」看通俗解释；ACT 六维生成中时显示待生成占位。
3. **成长轨迹**：陪伴时间、猫猫说了 N 句、一起经历 N 天（数据来自 `GET /users/{id}/companion`）。
4. **价值核心**：价值词拆成 5 列，各列以略有差异的周期（12s 起、每列 +0.9s）循环上下滚动形成波浪感，上下边缘渐隐（后端真词 > 旧接口 `personal_values` > 内置兜底词表）。
5. 页脚声明「数据仅存储在本地设备，不会上传至服务器」；滚动超过 300px 出现「回到顶部」浮标；设置页可切换人格形象的男/女形象，画像页即时生效。

**画像轮询**：接口返回的 `act_metrics` / `personality` / `value_words` 三个模块各自带 `status`（`ready`/`pending`/`none`，`ProfileViewModel` 统一归一化为 `Ready/Pending/None/Invalid` 四态）。存在 `pending` 模块时每 4 秒重拉一次，全部就绪 / 90 秒预算耗尽 / 网络失败即停；离开页面 ViewModel 销毁自动停止。

---

## 4. 设置页（`SettingsScreen.kt`）

玻璃卡片式分组设置，背景为房间图：

| 分组 | 项 | 说明 |
| --- | --- | --- |
| 会话 | 重置对话 | 确认弹窗后：清本地历史 + 重置后端会话（保留画像与设置） |
| 会话 | 重置形象 | 确认弹窗后：清对话 + 清用户信息 + 重置后端会话（连带清画像） |
| 外观 | 陀螺仪视觉 | 主界面视差开关（默认开） |
| 外观 | 下雨效果 | 主界面雨滴开关（默认开） |
| 布局 | 自定义布局 | 「编辑」进布局编辑器；「重置」恢复默认布局（确认弹窗） |
| 用户信息 | 昵称 / 性别 / 年龄 / 语气偏好 | 昵称 ≤12 字；年龄 1–120；性别男/女/未设置（影响画像页人格形象）；语气偏好温和/活泼 |
| 帮助 | 新手引导 | 「查看」清除引导标记并返回主界面重播引导 |

**用户信息的「草稿 + 退出确认」模式**：四项改动先落在内存草稿（不即时落盘），离开设置页（返回键或左上角返回）若存在未保存改动，弹「保存并退出 / 不保存」；保存时先写 DataStore（成功才退出）再尽力而为 `PUT /users/{id}/user-info`（失败静默记日志，本地为源）。

设置项均以 DataStore 为单一事实源，各页面的多个 ViewModel 实例通过响应式流共享同一文件，切换后立即全局生效。

---

## 5. 布局编辑器（`LayoutEditorScreen.kt`）

「所见即所得」地将主界面场景物品摆成自己喜欢的样子。入口：设置 → 布局 → 编辑。

- **可编辑对象**：5 个场景物品（黑板、猫、盆栽、笔记本、咖啡杯）。对话气泡窗口**不可编辑**；背景、雨层、输入栏同样不可动。
- **交互**
  - 点/按物品 → 选中，出现蓝色边框 + 四角白色矩形手柄 + 顶边白色旋转圆钮；
  - 拖本体 → 移动（拖动中隐藏控件，松手重现，PS 手感）；
  - 拖角 → 不等比缩放（对角锚点钉死不漂）；
  - 双指捏合 → 等比缩放（中心不动）；
  - 拖旋转圆钮 → 旋转（旋钮始终指向手指，绝对角度无累积误差）；
  - 点空白 → 取消选中；底部圆形按钮 → 撤销上一步（可连续撤销，仅在可撤销时出现）。
- **数学基础**（`data/Transform.kt`，纯函数、可 JVM 单测）：物品以**几何中心**存储（v2 布局格式），中心是旋转不动点；角缩放为「手势开始冻结对角锚点 + 每帧用手指绝对位置反解」，无增量累加、无反馈回路 → 旋转耦合/漂移/瞬移一族问题在数学层面消除。尺寸钳制在设计单位 140~1600（宽）/2200（高），中心可移出画布 120 设计单位容差，防止拖丢。
- **退出**：有改动时弹「保存并退出 / 不保存（回滚到进入时布局）」，系统返回键同效。
- **持久化**：整份布局 JSON 存 DataStore 文件 `ea_layout`；读取时以默认布局为底做逐项合并（缺失项自动补默认、未知 id 忽略，前向兼容）；v1 旧格式（左上角存储）解析时自动迁移为 v2 中心存储，零数据丢失。

---

## 6. 新手引导（`OnboardingOverlay.kt`、`OnboardingStore.kt`）

首次启动（或设置页「重看引导」后回主界面）播放的聚光灯分步引导，共 4 步：

0. 欢迎语（无挖孔）→ 1. 输入框「和猫猫聊天」→ 2. 爪印按钮「功能菜单」→ 3. 右侧黑板「你的个人画像」。

- 暖棕半透明遮罩上用 `BlendMode.DstOut` 抠出圆角矩形孔洞高亮目标，孔洞描青色光环带呼吸脉冲；挖孔外扩 16dp 以吸收陀螺仪视差带来的坐标漂移；步骤切换时孔洞从大孔收缩到目标孔（聚光灯「聚」的瞬间）。
- 点屏幕任意处 / 卡片按钮 = 下一步；「跳过」与系统返回键 = 立即结束。
- 完成标记存 DataStore 文件 `ea_onboarding`；主界面首帧先视为已完成（避免冷启动竞态闪一下），标记读出为未完成后才弹出；「重看引导」清除标记，返回主界面从第 0 步重新播放。

---

## 7. 后端接口清单

基址 `https://ea.eznju.com/`（Emotion Support Agent API v0.2.0「三线并行」架构）。除 `/chat` 走 SSE 外均为 Retrofit JSON 端点：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/chat` | 流式对话（SSE：`token` 增量、`final` 风险标志） |
| POST | `/sessions` | 新建会话 |
| GET | `/users/{user_id}/sessions` | 会话列表（取默认会话） |
| POST | `/sessions/{session_id}/reset` | 重置会话 |
| GET | `/sessions/{session_id}/history?page&page_size` | 历史消息 |
| GET | `/users/{user_id}/profile` | 画像数据（依恋风格、拒绝敏感度、核心恐惧、个人价值、痛苦耐受） |
| GET | `/users/{user_id}/companion` | 陪伴统计（陪伴时长、消息数、天数、初识时间） |
| GET | `/users/{user_id}/portrait` | 画像三模块（ACT 六维 / 人格枚举 / 价值词，各带 ready·pending·none 状态） |
| POST | `/users/{user_id}/portrait/personality/regenerate` | 重新生成人格报告（404 无画像可判 / 429 频控有专门提示文案） |
| PUT | `/users/{user_id}/user-info` | 用户信息整份覆盖写（昵称/性别/年龄/语气偏好；空值＝未设置） |

容错约定：画像端点失败即整体失败（区分不出「画像为空」与「网络错误」，空态判断会失真）；companion / sessions / portrait 属次要点，失败时尽力而为（置零、留空或沿用上一次数据）。契约细节见 [ProfileScreen-api-contract.md](ProfileScreen-api-contract.md)。

---

## 8. 本地存储一览

| DataStore 文件 | 内容 | 读取失败时 |
| --- | --- | --- |
| `ea_settings` | 昵称/性别/年龄/语气偏好、视差开关、下雨开关 | 各取默认值 |
| `ea_chat_history` | 最近 200 条聊天消息（JSON） | 视作空历史 |
| `ea_layout` | 自定义场景布局（JSON，含 v1 迁移） | 回落默认布局 |
| `ea_onboarding` | 新手引导完成标记 | 视作未完成（会播放引导） |

---

## 9. 测试与质量现状

三层自动化测试与功能同 PR 生长（详见 [../.opencode/skills/android-frontend-testing/SKILL.md](../.opencode/skills/android-frontend-testing/SKILL.md)）：

- **JVM 单测**（`app/src/test`）：变换几何 `TransformTest`、视差曲线与零位状态机 `ParallaxCurveTest` / `ParallaxBiasTrackerTest`、画像三态与人格合并 `PortraitModelsTest` / `PortraitMergeTest` / `ProfileModelsTest`、用户信息请求体与脏标记 `UserInfoRequestTest` / `UserInfoDirtyTest`。
- **组件级 Compose 测试**（`app/src/androidTest`）：`ChatMenuContentTest`、`OnboardingOverlayTest`、`SettingsExitDialogTest`、`LayoutEditorEditTest`。
- **应用级 E2E**：`FullAppSmokeTest`、`ParallaxNavigationTest`、`ProfileEmptyStateTest`、`ProfilePendingStateTest`。

一条命令跑全量回归：`./gradlew.bat testDebugUnitTest connectedDebugAndroidTest`（假定 Pixel 8 / 1080×2400 / 420dpi / API 36 模拟器）。

---

## 10. 已知限制与预留

- **无主题切换**：主界面/设置/画像均为自定义深色玻璃风格，不随系统浅色模式变化。
- **用户体系**：以 `ANDROID_ID` 为 user_id，无登录/多设备同步。
- **对话窗与背景不可自定义**：布局编辑器只覆盖 5 个场景物品；`ItemLayout.visible` 字段已预留但编辑器暂无隐藏 UI。
- **单会话**：只使用用户的默认会话，多会话历史未在 UI 暴露。
- **画像生成是后端异步增量的**：对话轮数不足时画像页只能显示空态，需多轮对话后才有数据；`pending` 模块靠最长 90 秒轮询等待。
- **后端地址硬编码**在 `ChatRepository.BASE_URL`，切换后端需改代码重编译。
