# 心语吧 · 情绪疏导 Agent

南京大学软件学院「情绪 Agent」项目 —— 一个可以在雨天咖啡馆里陪你聊聊的 AI 猫猫。

本仓库为**比赛提交用的公开快照**，同时包含安卓客户端与后端 Agent 服务：

| 目录 | 内容 | 技术栈 |
| --- | --- | --- |
| [`android/`](android/) | 安卓客户端：场景化主界面 + 流式对话 + 个人画像 + 场景布局编辑器 | Kotlin · Jetpack Compose · Retrofit/OkHttp · DataStore |
| [`backend/`](backend/) | 后端 Agent 服务：多 Agent 心理支持，三线并行 + 五类疗法 | Python · FastAPI · SSE · OpenAI 兼容 LLM |

> ⚠️ 本系统仅供研究与比赛演示，**不能替代专业心理咨询或治疗**。如果你或身边的人正处在危机中，请联系信任的人或当地心理援助热线。

---

## 它是什么

**对话**：客户端把用户消息发给后端，后端以 SSE 逐 token 流式返回，客户端一边收一边上屏，同时驱动猫猫的动画状态机（思考 / 被点击 / 待机）。

**陪伴**：场景由雨天咖啡馆、可自定义摆放的物品与一块写着成长足迹的黑板组成；用户可拖动、缩放、旋转场景物品，布局持久化在本地。

**画像**：后端异步增量地为用户构建画像（依恋风格、拒绝敏感度、核心恐惧、个人价值、痛苦耐受），并给出九种「心语人格」之一、ACT 七轴雷达、陪伴统计与价值核心词云。

### 后端架构（三线并行）

- **对话线**：流式生成用户可见文本，唯一 Host Agent 永不交棒；按调度状态注入危机 / 疗法指导 / 情绪命名 / 日常四类互斥模板之一。
- **决策线**：安全决策 / 疗法决策 / 疗程编排；疗程中对应疗法 Agent 作为纯分析 subAgent 每轮产出编排结果，不阻塞对话线。
- **沉淀线**：画像增量 + 会话摘要。

支持 CBT、ACT、DBT、MI、SFBT 五类疗法；疗程的持续与停止由**定量边际效用公式**裁决（流程推进 + 状态标量环比 + 技术新颖性三成分记账 + 停滞/倒退规则），而不是让 LLM 投票。详见 [`backend/ARCHITECTURE.md`](backend/ARCHITECTURE.md)。

---

## 快速开始

### 后端（`backend/`）

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # 填入你的 OpenAI 兼容 API Key（如 DeepSeek）
python -m uvicorn server:app --host 0.0.0.0 --port 8000
```

启动后可访问 `http://localhost:8000/ui` 使用内置测试用 WebUI，接口文档见 `http://localhost:8000/docs`。
详细的配置项、Docker 部署与测试说明见 [`backend/README.md`](backend/README.md)。

> 注意：所有后端命令都要**在 `backend/` 目录下执行**（它是后端子项目的根目录）。

### 客户端（`android/`）

1. 用 Android Studio 打开 `android/` 目录（不是仓库根）。
2. 需要 **JDK 21** 工具链（Android Studio 自带的 JBR 21 即可）。
3. 直接 Run ▶，或命令行构建：

```powershell
cd android
.\gradlew.bat assembleDebug     # 构建 debug APK
.\gradlew.bat installDebug      # 安装到已连接设备/模拟器
```

客户端默认连接生产演示后端 `https://ea.eznju.com/`（硬编码在 `android/app/src/main/java/com/njuse/ea/data/ChatRepository.kt` 的 `BASE_URL`）；连本地后端改该常量后重编译即可。

---

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [`android/docs/app-feature-overview.md`](android/docs/app-feature-overview.md) | 客户端功能总览（各页面、交互、动画、数据存储、接口清单） |
| [`android/README.md`](android/README.md) | 客户端环境准备、模拟器规格、构建与测试命令、动画素材替换流程 |
| [`android/AGENTS.md`](android/AGENTS.md) | 客户端开发规范与新功能实现流程 |
| [`backend/README.md`](backend/README.md) | 后端快速开始、配置项、部署与测试 |
| [`backend/ARCHITECTURE.md`](backend/ARCHITECTURE.md) | 后端架构、请求生命周期、模块阅读顺序 |
| [`backend/docs/test_report.md`](backend/docs/test_report.md) | 后端测试核验报告 |
| [`backend/docs/ROUTING_EVALUATION.md`](backend/docs/ROUTING_EVALUATION.md)、[`THERAPY_EVALUATION.md`](backend/docs/THERAPY_EVALUATION.md) | 路由与疗法链路审计报告 |

---

## 测试

后端（在 `backend/` 下执行，使用假件 provider，无需 API Key）：

```bash
cd backend
python -m pytest tests/ -q      # 全量测试
python -m ruff check .          # lint
```

客户端（需要 Pixel 8 型 AVD：1080×2400 / 420dpi / API 36）：

```powershell
cd android
.\gradlew.bat testDebugUnitTest connectedDebugAndroidTest
```

---

## License

[MIT](LICENSE)
