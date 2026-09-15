# 心语吧 · Android 客户端

情绪陪伴 Agent 的安卓前端（Kotlin + Jetpack Compose，单模块 `:app`）。雨天咖啡馆场景 + AI 对话（SSE 流式）+ 可自定义的场景布局编辑器。

> 本目录是仓库的 `android/` 子项目（后端见 [../backend/](../backend/)）。面向 AI Agent 的项目架构与规范见 [AGENTS.md](AGENTS.md)；各模块的实现细节见对应文件的头注释；功能总览见 [docs/app-feature-overview.md](docs/app-feature-overview.md)。

## 环境准备

1. **Android Studio**：直接打开本 `android/` 目录（不是仓库根）。
2. **JDK**：构建工具链需要 JDK 21。AS 自带的 JBR 21 即可；命令行构建设置 `JAVA_HOME` 指向 JDK 21，或按 `gradle.properties` 里注释的写法显式指定路径。
3. **SDK**：`local.properties`（gitignored）由 AS 自动生成，勿提交。

## 模拟器配置

建议配置 Android Studio 自带模拟器和开发工具集成，方便进行调试，但使用时有点卡顿，如果嫌弃可以使用第三方模拟器。

自动化测试假定如下规格（Pixel 8 型 AVD），其他分辨率需同步修改测试里的换算系数与坐标：

| 项 | 值 |
| --- | --- |
| Device | Pixel 8 |
| Resolution | 1080 × 2400 |
| Density | 420dpi |
| System Image | API 36（Google APIs，x86_64） |

创建路径：Android Studio → Device Manager → Create Virtual Device → Pixel 8 → 选择 API 36 镜像。

## 运行

```powershell
.\gradlew.bat assembleDebug        # 构建 debug APK
.\gradlew.bat installDebug         # 安装到已连接设备/模拟器
```

或在 Android Studio 里直接 Run ▶。客户端默认连生产演示后端 `https://ea.eznju.com/`（硬编码在 `app/src/main/java/com/njuse/ea/data/ChatRepository.kt` 的 `BASE_URL`）；想连本地后端（见 [../backend/README.md](../backend/README.md)）改该常量后重编译即可。

## 自动化测试

### 前期准备（一次性）

按上表创建并启动 AVD（冷启动后等 `adb devices` 显示 `device` 状态）即可，无需其他配置——instrumented 测试自动装/卸 APK、进程内直接读写应用数据。

### 跑测试

```powershell
# 全量回归一条命令（JVM 单测 + instrumented，任一失败退出码非零）
.\gradlew.bat testDebugUnitTest connectedDebugAndroidTest
```

测试与功能同 PR 生长：每个新 UI 功能同时提交用例，断言必须是确定性判据（数值/落盘/bounds，禁"看起来对"）。
三层分工与实测坑（手势分段注入、testTag 位置、无限动画与 idle 死锁等）见
`.opencode/skills/android-frontend-testing/SKILL.md`。

## 动画素材替换

逐帧动画素材（`*_NNNNN.webp`）的替换/新增**一律走脚本**，不要手工转格式或直塞 PNG：

```powershell
pip install Pillow
python scripts/convert_png_to_webp.py                    # 全部任务
python scripts/convert_png_to_webp.py --only cat_idle    # 单个任务
```

任务文件 `scripts/convert_jobs.json`（gitignored）从 `scripts/convert_jobs.json.example` 复制配置。
