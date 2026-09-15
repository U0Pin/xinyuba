# Emotion Support Agent（情绪疏导 Agent）

多 Agent 心理支持后端（新架构，2026-08 重构完成，2026-09 起 Host 单一对话）。
三条线并行、说话不阻塞：
**对话线**（流式生成用户可见文本，**唯一 Host Agent 永不交棒**；按调度状态
注入危机 / 疗法指导 / 情绪命名 / 日常四类互斥模板之一）+
**决策线**（安全决策 / 疗法决策 / 疗程编排；疗程中对应疗法 Agent 作为
**纯分析 subAgent** 每轮产出编排结果，不阻塞对话线，供 Host 下一轮组织语言）+
**沉淀线**（画像增量 + 会话摘要），主调度由纯代码完成。

支持 CBT、ACT、DBT、MI、SFBT 五类疗法，疗程内由对应疗法 Agent 按
基本流程推进（每轮：状态评估 → 步骤推进判断 → 执行当前步骤 skill）。
疗程的持续/停止由**定量边际效用公式**裁决（`core/marginal_utility.py`：
流程推进 + 状态标量环比 + 技术新颖性三成分记账，停滞/倒退两规则），不是 LLM 投票。

## 环境要求

- Python 3.10+
- 一个 OpenAI 兼容的 LLM 服务（如 DeepSeek，[申请地址](https://platform.deepseek.com)）

## 快速开始

> 本目录（`backend/`）即后端子项目根目录：**以下所有命令都在本目录下执行**
> （仓库根是全仓 monorepo，`android/` 为客户端）。从仓库根直接跑
> `python -m pytest backend/tests` 会因 `src` 不在导入路径上而报 `ModuleNotFoundError`。

### 1. 安装依赖

运行时（容器）：

```bash
pip install -r requirements.txt
```

开发/测试（额外含 pytest、httpx、ruff）：

```bash
pip install -r requirements-dev.txt
```

### 2. 配置

复制环境变量模板，填入你的 API Key：

```bash
cp .env.example .env
```

常用配置项：

```env
OPENAI_API_KEY=sk-你的密钥
OPENAI_BASE_URL=https://api.deepseek.com/v1
MODEL_NAME=deepseek-chat          # 主模型（对话/疗法机制）
CHEAP_MODEL_NAME=qwen-turbo       # 廉价模型（安全决策）
WINDOW_SIZE=5                     # 疗法决策滑动窗口（轮）
WINDOW_TOKEN_THRESHOLD=0          # 疗法决策 token 触发阈值（估算口径：字符数/2）；0=每条消息都评估（默认）。
                                  # 阈值只决定评估频率；是否触发由决策器按信号判断（闲聊不会触发）
THERAPY_MAX_ROUNDS=10             # 疗程工作轮数上限
RECENT_TURNS=3                    # 对话线读取的近 N 轮窗口
SUMMARY_TOKEN_THRESHOLD=1500      # 会话摘要重生成阈值（估算口径：字符数/2）
```

### 3. 启动

HTTP 服务（SSE 流式）：

```bash
python -m uvicorn server:app --host 0.0.0.0 --port 8000
```

命令行多轮对话：

```bash
python main.py
```

## 测试用 WebUI

启动 server 后访问 `http://localhost:8000/ui`：

- **user / session**：两个下拉框直接列出存盘中已有的全部 user 与该 user 的 session；
  可一键**新建 user**、**新建 session**（切换即生效）；
- **聊天**：流式输出（逐字显示），final 元数据（risk / crisis / planned_skill / therapy / 干预次数）附在回复下；
- **状态面板**（500ms 刷新）：调度状态（当前 owner / 疗法 / 风险 / 危机）、编排结果（步骤 / 技能 / 继续评估）、会话摘要与用户画像（沉淀层产物）、流水尾部；
- **指标面板**：session 总 tokens、prompt/completion 分布、缓存命中率（hit/miss tokens）、平均首 token 时间、平均总延迟、调用数 / 失败数、估算成本、按 agent 分布（由 `logs/llm_calls.jsonl` 增量聚合）；
- **日志面板**：`app.jsonl` + `llm_calls.jsonl` 实时尾部（北京时间），可按来源 / 级别 / session / trace 过滤，点击条目展开完整 JSON。

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sessions` | `{user_id}` → 新建 session（返回 session_id） |
| GET | `/users/{user_id}/sessions` | 列出该 user 全部 session 元数据 |
| POST | `/chat` | `{user_id, session_id?, message}` → SSE 流（token… / final） |
| GET | `/sessions/{session_id}/history` | 分页历史（倒序，最新在前） |
| GET | `/users/{user_id}/profile` | user 级画像 |
| POST | `/sessions/{session_id}/reset` | 清空该 session 上下文（保留画像） |

`POST /chat` 返回 `text/event-stream`：`token` 事件逐字推送回复文本；
`final` 事件带元数据（`risk_state`、`crisis`）；设置 `DEBUG_TALKS=1` 时
`final` 额外附 `planned_skill / current_therapy / intervention_count`。

## 前置反向代理必须关闭 SSE 缓冲（否则不是真流式）

`/chat` 的 token 是模型边生成边逐块推给客户端的**真流式**。如果服务跑在
nginx / 负载均衡后面，代理默认的缓冲会把整轮响应（含 `final`）**攒到请求
处理完再一次性下发给客户端**，客户端就不会实时看到逐字输出了。生产实测特征：
首 token 延迟 20~35s、所有 `token` + `final` 同一瞬时暴发、token 间隔 ≈0ms。

务必在代理层为 `/chat`（SSE）关闭缓冲，参考 `nginx.conf`（site/location 内）：
`proxy_buffering off;` + `chunked_transfer_encoding on;` + `proxy_http_version 1.1;`，
**并覆盖 http 层 `gzip on;`**：`proxy_set_header Accept-Encoding "";`
（压缩会强制缓冲整段）。

修复后验证特征：首 token 在 ~1-3s（模型 TTFT）到达、token 间隔 50ms~几百 ms 不均
（真实生成速度）、`final` 在最后一条 `token` 之后明显延迟到达（中间为决策线 +
沉淀线的耗时）。

## 项目结构

```
backend/
├── main.py                     # CLI 多轮对话入口（新架构）
├── server.py                   # FastAPI SSE 入口
├── src/
│   ├── bootstrap.py            # 装配入口：build_scheduler()（server 与 CLI 共用）
│   ├── core/                   # 调度与基础设施
│   │   ├── scheduler.py        # 纯代码调度器：三线编排、信号裁决、session 串行化
│   │   ├── signals.py          # 信号定义（切换/结束/危机）
│   │   ├── llm_client.py       # 统一 LLM 接口（记账/流式/provider 抽象）
│   │   ├── logging_utils.py    # 结构化日志 + trace_id
│   │   ├── skill.py            # Skill/LLMSkill/SkillRegistry
│   │   ├── act_state.py        # ACT 状态机（阈值 + 灵活性公式）
│   │   ├── marginal_utility.py # 定量边际效用：效用账三成分 + 停滞/倒退裁决（纯函数）
│   │   └── state.py            # 存盘契约 schema：SessionState/TherapyProgress/Orchestration/RiskState/owner 协议
│   ├── agents/
│   │   ├── host_agent.py       # 唯一对话 Agent（Host）：危机/疗法/日常模板选择与措辞
│   │   ├── safety_agent.py     # 安全决策（廉价模型，5 级风险阶梯）
│   │   ├── therapy/            # 五疗法 Agent（纯分析 subAgent：评估/判断/skill）
│   │   ├── therapy_decider.py  # 疗法决策（滑动窗口）+ 继续/结束评估
│   │   ├── affect_labeling.py  # Affect Labeling 决策引擎（非疗法情绪支持）
│   │   ├── settlement.py       # 沉淀线：画像增量 + 会话摘要
│   │   └── prompts/            # 全部新 prompt（黄金快照冻结）
│   ├── skills/                 # 26 技能 + 疗法元数据 + 技能 prompt（编排调用）
│   ├── store/                  # 存盘层：session JSONL 流水 / 画像 / 只读流水查询
│   └── utils/
│       ├── config.py           # 配置与参数
│       └── text.py             # 共享纯函数：转写格式化 / token 估算口径
├── data/                       # 运行时数据（gitignored）
│   ├── sessions/{sid}/flow.jsonl      # 对话流水（追加式）
│   ├── sessions/{sid}/{state,orchestration,summary}.json
│   └── users/{uid}/{profile,index}.json
├── logs/                       # app.jsonl + llm_calls.jsonl（gitignored）
├── evals/                      # 评测工具：seed 跑批 / 候选池筛选 / 问卷生成
├── outputs/                    # 评测产物：候选池 / seed 跑批结果 / 问卷与评测报告
├── pyproject.toml              # 项目元数据 + ruff/pytest 配置
└── tests/
    ├── unit/                 # 纯单元测试
    ├── integration/          # 调度器 / 存储 / API / WebUI / 跨模块集成测试
    ├── golden/               # Prompt 黄金快照
    ├── eval/                 # 离线评测数据集、schema、runner 与测试
    └── support/              # FakeProvider 等测试支撑件
```

## 测试

```bash
python -m pytest tests/ -q          # 全量（假件 provider，无需 API key）
python -m ruff check .              # lint（配置见 pyproject.toml，先宽后严）
python tests/support/golden_v2_capture.py   # 有意变更行为时重捕获 prompt 基线
```

目录约定：

- `tests/unit/`：不依赖完整调度器的纯单元测试；
- `tests/integration/`：Scheduler、store、FastAPI、WebUI 与跨模块优先级回归；
- `tests/golden/`：核心 prompt 字节级黄金快照；
- `tests/eval/`：150 条路由案例、176 条疗法案例，以及离线评测 runner；
- `tests/support/`：FakeProvider、FakeSink、黄金快照捕获脚本等共享支撑件。

`tests/golden/snapshots.json` 冻结了新架构的核心 LLM prompt 面；
修改这些 prompt 前先重捕获并人工审 diff。

## 文档

- `ARCHITECTURE.md`：后端三线并行架构说明
- `cbt-skill-system-design.md`：CBT 技能系统设计

## 免责声明

本系统仅供研究和学习用途，**不能替代专业心理咨询或治疗**。如有心理健康危机，请及时联系专业机构或拨打心理援助热线。
