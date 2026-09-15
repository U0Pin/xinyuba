---
name: android-frontend-testing
description: 安卓前端（Jetpack Compose）功能的通用测试方法论。当用户要求测试/验证某个 UI 功能、写自动化测试、修 bug 后需要回归验证、或讨论"这个改动怎么测/怎么验证"时使用。覆盖三条验证通道（纯 JVM 单测 / 组件级 Compose 测试套件 / 应用级 E2E）的选择原则、断言手段光谱、以及实测踩坑清单（手势分段注入、testTag 位置、无限动画与 idle 死锁、帧时钟控制、API 版本差异等）。新增 UI 功能、修改手势/布局/动画相关代码、或用户抱怨测试费劲时都应触发。
---

# Android 前端测试方法论

功能实现完毕 ≠ 完成。**先定"确定性判据"，再选验证通道**——判据是可量化、可重复执行的断言（位移 1:1、边界精确值、状态落盘一致），不是"看起来没问题"。

## 三条验证通道（都在 Kotlin 里，与代码同仓同语言）

| 通道 | 工具 | 擅长 | 局限 |
|---|---|---|---|
| 1. 纯 JVM 单测 | JUnit（`app/src/test`） | 纯函数数学、编解码、状态机 | 不触 UI |
| 2. 组件级 Compose 测试 | `createComposeRule`（`app/src/androidTest`） | 手势（含**多指**）、布局边界、语义树、状态落盘 | 不走真实启动/导航 |
| 3. 应用级 E2E | `createAndroidComposeRule<MainActivity>` + UiAutomator | 真实启动链路、跨页导航、持久化闭环 | 慢、受动画干扰（见下） |

**选择逻辑**：逻辑复杂 → 先抽纯函数走通道 1；有手势/布局 → 通道 2；要验证启动链路/跨页（"装包后能不能用"）→ 通道 3。修 bug 时：先用任一通道复现（判据变红），修完转绿，最后 `.\gradlew.bat testDebugUnitTest connectedDebugAndroidTest` 一条命令全量回归（任一失败退出码非零）。

**测试与功能同 PR 生长**：新 UI 功能的用例与功能代码同一次提交。用例函数名要能读出覆盖的场景/边界；名字表达不了时就加注释。归属判据——纯逻辑→1，手势/布局→2，跨页/启动→3。

## 通道 1：纯 JVM 单测

- 能写成 `f(x) → y` 的逻辑（几何换算、钳制、编解码迁移、状态机）一律抽成无 Android 依赖的纯函数，直接 JUnit 断言。UI 手势的数学核心都应沉淀在这层。
- 秒级反馈、零环境依赖——修 bug 前先让它红。

## 通道 2：组件级 Compose 测试

模板：`app/src/androidTest/java/com/njuse/ea/ui/LayoutEditorEditTest.kt`。

### 骨架
```kotlin
@RunWith(AndroidJUnit4::class)
class XxxTest {
    @get:Rule val compose = createComposeRule()

    private fun launch() {
        // 前置状态直接写持久层 seed，再 setContent，测试自证干净
        compose.setContent { XxxScreen(onBackClick = {}, viewModel = SettingsViewModel(app)) }
        compose.waitUntil(5000) { onAllNodesWithTag("...").fetchSemanticsNodes().isNotEmpty() }
        compose.waitForIdle()
    }
}
```
- **直接组合真实 Screen + 真实 ViewModel/DataStore**（`SettingsViewModel(app)` 手动构造），不 mock——测的就是真实接线。
- 状态断言：轮询持久层（DataStore/DB）读数值。落盘是异步的，用 deadline+sleep 循环，别靠 `waitForIdle` 猜。
- 布局断言：`onNodeWithTag(tag).fetchSemanticsNode().boundsInRoot`，期望值以**另一个节点的实际 bounds** 推导（如父容器/物品盒中心）——不要硬编码窗口绝对坐标（insets 会偏）。
- 测试前置 seed：直接写 DataStore 再 `setContent`，每个测试自含干净状态。

### 必须给被测节点加 testTag
`Modifier.testTag(...)`。**坑：testTag 要放在 `.offset{}`（及任何改变布局位置的 modifier）之后**——放前面语义 bounds 不含平移量，测试读到的位置错（视觉/命中不受影响，纯观测错位）。

### 手势注入规则（踩过的真坑）
1. **分段注入**：`down(); moveBy(d1); moveBy(d2)... up()`。单事件大位移是测试伪现象——跨 touch slop 的事件会把"事件位置"（而非按下位置）传给 `onDragStart`，依赖按下位置初始化的逻辑会拿错基准，断言恒 0/恒等。真实手指天然多事件。
2. **`positionChange()` 先读后 `consume()`**（生产代码同理）：它返回"位移−已消费部分"，consume 后再读恒为 0。
3. **多指**：`performTouchInput { pinch(s0, e0, s1, e1) }`（缩放比 = |e0−e1|/|s0−s1|）；手动多指用显式 pointerId：`down(0, p); down(1, p2); moveTo(0, p3); up(0); up(1)`。
4. 期望公式别重复计数（半宽/半径类系数），写前先列清单位（设计单位 vs px vs dp）。

### API 版本差异
BOM 2026.02.01 / ui 1.10.x 与旧教程不同。编译报 Unresolved/类型不符时**先查官方 API 参考**（`curl.exe -x http://127.0.0.1:10090 https://developer.android.com/reference/kotlin/<包>/<类>`，免代理镜像 developer.android.google.cn），别凭旧记忆猜。实例：`assertDoesNotExist()` 已从顶层扩展变为成员函数（import 反而报错）。

## 通道 3：应用级 E2E

模板：`app/src/androidTest/java/com/njuse/ea/ui/FullAppSmokeTest.kt`。覆盖「装包后能启动、真实跨页导航、持久化闭环」——组件级测不到的启动链路与页面接缝。

### 首要难题：无限动画 vs 测试 idle（标准配方）

有持续动画的界面（粒子/循环动画/`withFrameNanos` 驱动）会让 Compose 的 idle 机制**永不空闲**，一切 `waitForIdle`/`performClick`/finder 直接死锁。应用级测试的标准配方：

1. **`compose.mainClock.autoAdvance = false`**（进主界面阶段立刻设）——冻结帧时钟，compose 立即 idle。
2. **冻结的代价：重组也停在帧上**——导航/数据到达后 UI 不推帧不渲染。恢复渲染 = `mainClock.advanceTimeBy(ms)` **有界推帧**（无界推进遇无限动画死循环）。
3. **交互注入换系统通道**：compose 的触摸注入（手势协程等帧）在帧时钟冻结下会失败（"Failed to inject touch input"）——动画页交互一律 `device.executeShellCommand("input tap/swipe ...")`（系统注入，与 idle/帧时钟无关，坐标取自语义 bounds）。
4. **进入无动画的静态页后**恢复 `autoAdvance = true`，此后可正常用 compose 注入/`waitForIdle`。
5. UiAutomator 的 `findObject` 只读 a11y 树可随时用；**`Configurator.getInstance().waitForIdleTimeout = 0`** 免得它自己也等 idle。
6. 轮询等待自写：`while (deadline) { tick(); if (条件) return; sleep(60) }`——`waitUntil` 依赖的时钟语义在手动模式下不可靠。

### 其他要点
- **区分页面必须用页面特有特征**：多个页面共用的组件（如同名 tag）不能作为"到了哪页"的判据。
- 不用 `pm clear`（会杀 instrumentation 所在进程）；前置状态用进程内直接写持久层。
- 数据断言同样轮询持久层（进程内直接读，无需 root/adb）。

## 通用纪律

1. 判据先行：修 bug 前先把现象固化为一个会红的确定性断言；修完转绿。
2. 手势/动画类 bug 先问：**增量在哪个坐标系里算？节点自己动了吗？**（典型：用旧事件坐标系差分 → 节点移动混入增量 → 抖动/漂移；修法 = 用框架给的不含节点自移的位移 API）。
3. 收尾：`.\gradlew.bat testDebugUnitTest connectedDebugAndroidTest` 全绿才算完成。
4. 环境假设：模拟器 1080×2400 @420dpi（Pixel 8 型 AVD）；换分辨率同步改测试里的换算系数与坐标。
5. 视觉截图验证（instrumented）：`onRoot().captureToImage().asAndroidBitmap()` 写入 `targetContext.filesDir`，再用 `adb exec-out run-as <pkg> cat files/xx.png` 拉回——**PS 5.1 的 `>` 重定向会把二进制流搞坏**，必须 `cmd /c "... > build\xx.png"`。另：connected 任务结束 AGP 自动卸载 app，run-as 拉文件要在卸载前；需要留存就手动 `installDebug` + `adb install` test apk + `adb shell am instrument`。截图 ≥2.5MP，Read 前先 Pillow `thumbnail()` 缩图。
6. 测输入框/键盘相关布局的坑：模拟器默认硬件键盘模式（`show_ime_with_hard_keyboard=0`），聚焦后 **IME 窗口不显示但 insets 照报**——`imePadding` 会把布局顶起一大截且截图中"看不到键盘"，这是环境伪象不是 bug；`captureToImage` 只截 app 窗口、永远不含 IME 窗。断言要打在**不含 imePadding 的内层节点**上，别拿外层节点高度当判据。
7. 测前先验全屏：app 曾被留在 freeform 小窗（`am stack list` 可见 `lastNonFullscreenBounds`），窗口化状态下一切 bounds/坐标都被污染。守卫：root `boundsInRoot` 面积 ≈ 1080×2400 再开测；发现小窗先 `am force-stop` 清 task。
