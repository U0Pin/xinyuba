# 后端架构说明（新架构，2026-08 重构）

本文档描述本仓库当前可运行的后端结构（三线并行架构）。

> 旧串行流水线（pipeline / 五旧 Agent / key_moments / skill_inputs）已于 Phase 4 删除。

## 一句话总览

- 三条线并行：**对话线**（说话）、**决策线**（安全 + 疗法）、**沉淀线**（画像 + 摘要）；
- 主调度由**纯代码**完成（`core/scheduler.py`），不由 LLM 调度；
- 对话线只读存盘状态生成流式回复；决策线只写存盘状态与信号；
- 一切经存盘解耦：**信号与决策结果下一轮生效**（作者定稿）。

## 阅读路线（初读推荐顺序）

一个原则：**先认数据的形状，再认状态的转移，再读一轮的时间线，叶子模块最后按需读。**

| 步 | 文件 | 建立什么 |
|---|---|---|
| 1 | `core/state.py`（~170 行） | 全系统的数据形状：`SessionState`（state.json 的 schema）、`TherapyProgress`、`Orchestration`、风险档词汇与 owner↔疗法协议。其余一切模块只是搬运这些形状 |
| 2 | `core/signals.py`（~55 行） | 状态转移一共就这四种，各自自带 `apply()`。读完即知"轮与轮之间状态如何变" |
| 3 | `core/scheduler.py` → 先读 `Scheduler.handle_message` | 时间线本体：一轮消息 = 三线串行编排。它调谁，就回头读谁的类 |
| 4 | 同文件 → `DialogueLine` / `DecisionLine` | 说话线（流水唯一写者；说话模板见下一步的 Host）与决策线（安全→编排或疗法决策→情绪支持，`run_turn` 四步） |
| 5 | `agents/host_agent.py`（~230 行） | **唯一对话 Agent**：`reply_stream` 按调度状态选模板（危机 > 疗法 > 日常），三个 build_* 就是全部措辞 |
| 6 | `agents/therapy/base.py` + `therapy/cbt.py` | 疗法 Agent = 纯分析 subAgent（决策线调）：`run_assessment` / `run_step_judgment` / `run_current_skill` / `apply_judgment`，产出 Orchestration；不说话。CBT 的 FLOW 最直观，五法先看它 |
| 7 | `core/marginal_utility.py` + 其单测 | 定量边际效用：U(t) 三成分怎么记、regression/stagnation 怎么裁；四个合成序列就是全部语义，无需真实 LLM |
| 8 | `core/skill.py` + `skills/cbt.py` 任一技能 | 技能 = `build_prompt` / `parse_output` / `fallback` 三钩子的纯函数，同步异步共用一份流程 |
| 9 | `core/llm_client.py` | 一切 LLM 调用收口：`astream`（说话）、`acomplete`（决策/沉淀）、`bind()`（技能用）、逐调用记账 |
| 10 | `src/bootstrap.py` → `server.py` → `main.py` | 全部部件如何组装、如何暴露为 SSE 服务 / CLI |
| 乱序 | `safety_agent` / `therapy_decider` / `settlement` / `affect_labeling` / `skills/prompts/*` | 各自独立的小文件（50–300 行），用到再读 |

读时可用的两个验证手段：

- `python -m pytest tests/ -q`（在 `backend/` 目录执行；1 秒级、无需 API key）；具体行为看
  `tests/integration/test_scheduler_phase2.py`（时序）与 `tests/integration/test_therapy_phase3.py`（五疗法编排）；
- 真实运行一轮会在 `logs/app.jsonl` 按时序落齐
  `turn_start → safety_done → therapy_decide_done / orchestration_done → turn_end`，
  按 trace_id 过滤即可回放整轮决策轨迹。

## 请求生命周期（每轮）

1. `server.py` 收到 `/chat` → `Scheduler.handle_message()` 生成 trace_id 并获取 session 锁；
2. 读存盘状态（owner / crisis / risk / therapy）与画像、摘要；
3. 并行启动：T1 对话线流式说话（写流水 user+agent 条目）；T2 决策线（安全 → 疗法决策或疗程编排）；
4. await T1（SSE 流结束 = 响应结束）→ await T2 → 轮边界应用信号写 `state.json`（下一轮生效）；
5. T3 沉淀线：画像增量 + 摘要阈值重生成；释放 session 锁。

## 三条线职责

### 对话线（`DialogueLine` + 唯一 `HostDialogueAgent`）

- **Host 永不交棒**：任一时刻的用户可见文本都由同一个 Host Agent 生成；
  每轮按调度状态恰好选一个模板（互斥优先级：危机 > 疗法 > 日常）——
  危机 = 危机回复指令；疗程中 = 疗法通用指令 + 对应疗法 voice guide **逐字注入**
  + 上一轮存盘编排（`orchestration.json`）给出的本轮指导块；日常 = 日常指令
  （可叠加 Affect Labeling 块）；
- **唯一生成用户可见文本**的线；流式输出；只读存盘数据；
- 对话流水的唯一写入者（`flow.jsonl` 追加式，条目带 owner 审计标签——
  只记录当轮生效上下文，不再决定谁说话）；
- 入口：`agents/host_agent.py`。

### 决策线（`DecisionLine`）

- **安全决策**：每条消息，廉价模型；5 级风险阶梯；全级别标记记录，仅 CRISIS
  触发危机应对；失败回退 SAFE；危机期间疗法决策与编排暂停；
- **疗法决策**：日常态下，滑动窗口（默认 5 轮）+ 用户输入 token 阈值触发
  （`WINDOW_TOKEN_THRESHOLD`，0 = 每条消息都评估）；输出 需要/不需要 + 哪个疗法；
- **疗程编排**（疗程内每轮）：状态评估（各疗法评估 skill + 后端公式）→
  步骤推进判断（LLM 结构化：stay/advance/go_to/complete）→ 执行当前步骤 skill
  （每轮一次，产出 technique/steps/goal）→ 写 `orchestration.json`；
- **边际效用裁决（定量）**（疗程内每轮，B1 全周期）：记一笔效用账
  （流程推进 + 状态标量环比 + 技术新颖性，公式与参数集中在
  `core/marginal_utility.py`，纯函数无 LLM 投票），按停滞/倒退/冷启动规则
  裁决持续或停止；`THERAPY_MAX_ROUNDS` 保留为硬上限兜底；停止时把
  `{name, rounds, ended_reason}` 留痕进 `state.last_therapy`；
  （取代原"基本流程完成后每轮问一次 LLM 继续/结束"的设计；
  `basic_flow_complete` 降级为里程碑记录，不再参与裁决）；
- **情绪支持编排**（ESO，`src/agents/emotion_support.py`）：统一调度 PMR /
  Grounding / Affect Labeling 三个 Emotion Support Skill——只做 eligibility /
  priority / mutual exclusion / continuation / supersede / lifecycle 的确定性编排，
  不触碰各自内部状态机、不做 LLM 编排；
  优先级严格为 CRISIS > SAFETY > CURRENT THERAPY > PMR > GROUNDING >
  AFFECT LABELING > DAILY，每轮最多一个 Emotion Support Skill active；
- **跨模块优先级**（完整链）：CRISIS > SAFETY > CURRENT THERAPY >
  EMOTION SUPPORT（PMR > Grounding > AFFECT LABELING）> DAILY。
  Emotion Support 不是疗法族——它不能接管疗程、不能在危机/高危期间运行，
  也不继承任何 Therapy 状态；抢占（supersede）与恢复语义的系统性回归见
  `tests/integration/test_cross_module_orchestration.py`；
- **Grounding（回到当下）**：即时、低侵入的情绪支持技能，**不是疗法模态**。
  只做「外部锚定」（看见一样东西 → 感受身体接触 → 听声音并确认所在位置 →
  check → complete），不解释/不命名情绪（Affect 域）、不引导肌肉放松（PMR 域）、
  不做正念观察/接纳等任何 ACT/DBT 技术；危机与自身硬排除让位安全流程。
  注册（`skills/grounding.py`）、引擎（`agents/grounding.py`）、
  测试（`tests/integration/test_grounding.py`）。
- 调度状态（`state.json`）的唯一写入者（含调度器代码）。

### 沉淀线（`SettlementLine`）

- 每轮对话结束后异步执行（不阻塞 SSE 响应，只占 session 锁）；
- 画像：LLM **增量更新**（updates 覆盖 + append_lists 去重追加），user 级共享，user 锁保护；
- 摘要：结构化条目，流水 token 累计达到 `SUMMARY_TOKEN_THRESHOLD` 时重生成；
- 画像页评估（`agents/prompts/portrait.py`）：每轮顺带以画像 + 最近一轮对话为输入
  LLM 重估 ACT 六维（0~1 浮点）+ 九型人格枚举 + 价值词，写 `portrait.json`
  （无需对话积累，画像为空时给保守估计）；「重新生成报告」端点只重判人格
  （10s 频控在 HTTP 层）；
- 画像、摘要与画像页评估的唯一写入者。

## 疗法机制（`src/agents/therapy/`）

- 疗法 Agent 是**纯分析 subAgent**（只活在决策线上）：每轮评估 / 步骤判断 /
  skill 执行，产出 `Orchestration` 供 Host 下一轮组织语言——它们不再说话；
- 基本流程由 `FLOW`（主步骤）+
  `BRANCHES`（条件分支，如 CBT 行为激活）描述；步骤推进由每轮 step judgment 决定；
- 疗程内换技能由编排按 routing rules 决定（复用各疗法评估 prompt 的路由规则）；
- 26 技能（`src/skills/`）原样复用，由编排经统一 LLM 接口调用；
- "怎么说"（voice guide、对话模板）随说话权一并归 Host（`host_agent.py` 的
  `THERAPY_VOICE_GUIDES`）；"谈什么、推进到哪"留在这里。
  新增一种疗法 = 分析侧（Agent + 技能）+ Host 口吻表登记一行；
  对话逻辑与调度器零改动。

## 数据与存盘（`src/store/`）

```
data/sessions/{sid}/flow.jsonl          # 对话流水（对话线写，追加式，seq 递增）
data/sessions/{sid}/state.json          # 调度状态（决策线写）
data/sessions/{sid}/orchestration.json  # 最新编排结果（决策线写）
data/sessions/{sid}/summary.json        # 结构化摘要（沉淀线写）
data/users/{uid}/profile.json           # 画像（沉淀线写，user 级共享）
data/users/{uid}/portrait.json          # 画像页评估：ACT 六维 + 人格（沉淀线写）
data/users/{uid}/user_info.json         # 设置页四项用户信息（PUT 端点写，调度器只读并入画像）
data/users/{uid}/index.json             # session 索引（调度器写）
```

单写者纪律：每个存盘产物只有一个写入方（见上）；session 锁 + user 锁保证无竞态。

存盘契约的 schema 唯一定义处在 `src/core/state.py`：`SessionState`（state.json，
含 `TherapyProgress`）、`Orchestration`（orchestration.json 信封；载荷保持 LLM
自由 dict）、`RiskState`（风险档词汇表）与 owner ↔ 疗法协议（`owner_for` /
`therapy_key_for_owner`）。写路径经模型 `to_dict()`、读路径经 `from_dict()`
（缺键取默认、未知键忽略，兼容旧文件）；新增字段只改这一处。

## 统一 LLM 接口（`core/llm_client.py`）

- 所有 LLM 调用（含 skill 执行）必须经由 `LLMClient`：provider 抽象、流式/非流式、
  `bind()` 兼容 skill 层；每次调用落盘 `logs/llm_calls.jsonl`
  （user/session/agent/token/model/provider/latency/request_id/成本/成败）。

## 维护约定

- 新业务决策不塞进对话线 Agent；对话线只说话；
- 调度状态的改写只有两条通道：决策线经 `DecisionResult.state_updates`
  回传、信号自带 `apply(state)`——都在轮边界由调度器执行；任何线不得原地
  改 `TurnContext` 快照（它是只读快照，对话线与决策线并行运行）；
- 风险/危机门禁的权威执行点在决策线（CRISIS 短路编排）与 Affect 引擎
  gate（高危/危机不参与情绪支持）；skill 层不做档位判断；
- 存盘契约字段变更只改 `core/state.py` 的模型与其唯一写入方；
- 疗法 prompt 属于 `skills/prompts/` 与 `agents/prompts/therapy.py`，不散落；
- 新增技能时同步更新：技能实现、疗法 Agent 的 FLOW/路由（家族构成的唯一权威）、
  测试、黄金快照 v2；
- 修改 prompt / 解析 / 路由 / 时序 = 修改行为：必须跑全量测试，必要时
  `tests/support/golden_v2_capture.py` 重捕获并人工审 diff；
- 所有 prompt 被 `tests/golden/snapshots.json` 字节级冻结。
