# 测试现状报告

> 本报告基于当前工作区可直接运行、可复核的文件与命令生成，描述仓库**现有自动化测试和离线评测**的真实情况。
>
> 报告不把脚本化离线评测解释为真实大模型准确率，也不把部分核心 prompt 快照描述为“全部 prompt 已冻结”。

## 1. 总体结论

项目已经具备一套规模较大、运行速度快、完全离线可重复的自动化回归测试体系。

当前核验结果：

| 项目 | 结果 |
|---|---:|
| pytest 收集用例数 | **782** |
| pytest 通过数 | **782 passed** |
| 失败 / 错误 | **0 / 0** |
| 本地运行耗时 | 约 **16.6 秒** |
| 测试文件数 | **32 个** `test_*.py` |
| 断言数 | 约 **1,406 条** |
| 生产 Python 代码量 | 约 **11,709 行** |
| `tests/` Python 代码量 | 约 **12,072 行**（含 fixture、假件、评测 runner） |
| Ruff 检查 | **All checks passed** |
| 是否需要真实 API Key | **不需要** |
| 是否访问真实大模型网络 | **不访问** |
| 是否配置覆盖率统计 | **没有** |

测试的主要价值是保护系统中的**确定性机制**：调度顺序、状态转移、危机优先级、疗法编排、情绪支持互斥、存盘恢复、HTTP/SSE 基本行为，以及异常 JSON / LLM 调用失败时的兜底逻辑。

它不能单独证明真实大模型的分类准确率、干预选择质量或生成文本的临床效果。真实模型效果需要另跑 live evaluation，并结合人工评审。

## 2. 本次核验命令

在 `backend/` 目录（后端子项目根目录）执行：

```bash
python -m pytest tests/ -q
python -m ruff check .
python -m pytest tests/ --collect-only -q
```

当前结果：

```text
782 passed, 1 warning
All checks passed!
782 tests collected
```

唯一 warning 来自当前本地依赖中的 Starlette / AnyIO 兼容提示：

```text
DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated
```

这不是项目业务测试失败。CI 使用 Python 3.11；本地虚拟环境为 Python 3.12，依赖版本未完全锁定，因此可能出现这类依赖内部 warning。

## 3. 测试目录结构

测试已按职责整理为以下结构：

```text
tests/
├── conftest.py
├── unit/                  # 单元测试
├── integration/           # 调度器、存储、API、WebUI、跨模块集成测试
├── golden/                # Prompt 黄金快照
├── eval/                  # 离线评测数据集、schema、runner 及其测试
│   ├── eval_data/
│   └── support/
└── support/               # FakeProvider、FakeSink、快照捕获脚本等共享支撑件
```

### 3.1 单元测试：282 个

| 文件 | 用例数 | 主要内容 |
|---|---:|---|
| `unit/test_act_skills.py` | 91 | ACT 技能注册、JSON 解析、fallback、交互风格、状态效果 |
| `unit/test_act_state.py` | 90 | ACT 枚举、阈值、状态向量、灵活性公式、序列化 |
| `unit/test_marginal_utility.py` | 21 | 边际效用、技术新颖性、停滞、倒退、硬轮数上限相关公式 |
| `unit/test_therapy_selection.py` | 22 | 五疗法 owner 协议、FLOW/分支/技能注册一致性 |
| `unit/test_llm_client.py` | 12 | LLM provider 抽象、流式/非流式调用、usage、cache、TTFT、成本记账 |
| `unit/test_mi_skills.py` | 19 | MI 状态评估、阈值、三个 MI 技能与 fallback |
| `unit/test_sfbt_skills.py` | 18 | SFBT 状态评估、三个 SFBT 技能、公式与适配字段 |
| `unit/test_logging_utils.py` | 5 | trace id、结构化日志、LLM sink、北京时间 |
| `unit/test_therapy_decider.py` | 4 | 疗法决策器模型异常、非法疗法名、日志可见性 |
| **小计** | **282** |  |

### 3.2 集成测试：372 个

| 文件 | 用例数 | 主要内容 |
|---|---:|---|
| `integration/test_pmr.py` | 100 | PMR gate、offer/accept/decline、练习推进、安全排除、冷却、优先级 |
| `integration/test_affect_labeling.py` | 69 | 情绪命名 gate、候选过滤、用户确认/纠正/拒绝、词库完整性、跨模块行为 |
| `integration/test_therapy_phase3.py` | 40 | 五疗法编排、CBT 流程、步骤推进、边际效用、疗法决策、产品累积 |
| `integration/test_cross_module_orchestration.py` | 32 | Crisis / Therapy / PMR / Grounding / Affect 之间的抢占、互斥、恢复 |
| `integration/test_grounding.py` | 34 | Grounding gate、生命周期、安全门禁、步骤推进、与 PMR/Affect 互斥 |
| `integration/test_emotion_support.py` | 22 | ESO eligibility、优先级、continuation、supersede、集成计划 |
| `integration/test_portrait.py` | 23 | 画像页、人格重估、价值词、用户信息同步、HTTP 频控 |
| `integration/test_store.py` | 16 | SessionStore、ProfileStore、JSONL/JSON 读写、查询、重置 |
| `integration/test_scheduler_phase2.py` | 12 | 三线时序、SSE 事件、危机滞后一轮、session 锁、断线持久化、沉淀线 |
| `integration/test_webui.py` | 11 | WebUI 页面、用户、状态、指标、日志等内部 API |
| `integration/test_server.py` | 7 | 主要 FastAPI 接口、SSE、history、reset、默认 session |
| `integration/test_session_history.py` | 6 | 重启后流水/画像恢复与 reset 语义 |
| **小计** | **372** |  |

### 3.3 Prompt 黄金快照：1 个

| 文件 | 用例数 | 主要内容 |
|---|---:|---|
| `golden/test_snapshots.py` | 1 | 重新捕获核心 prompt，并与 `golden/snapshots.json` 做完全相等比较 |

当前快照包含 **18 个 key**：

- Host 日常 / 危机 prompt
- Safety prompt
- 疗法决策 prompt
- 五疗法 step judgment prompt
- 五疗法 Host 对话 prompt
- profile update 与 summary prompt
- 两个 CBT 分析技能 prompt

注意：系统当前注册了 **32 个 skill**，但 golden snapshot 只冻结了其中一部分核心 prompt。它不能被描述为“所有系统提示词均已字节级冻结”。PMR、Grounding、Affect Labeling、DBT/MI/SFBT/ACT 多数 intervention prompt、portrait eval prompt 等主要依赖专项测试中的关键字符串断言或行为测试，并未全部纳入 golden snapshot。

重新捕获快照的命令：

```bash
python tests/support/golden_v2_capture.py
```

只有在确认 prompt 变更有意为之后，才应重捕获并人工审查 diff。

### 3.4 评测数据与离线 runner 测试：127 个

| 文件 | 用例数 | 主要内容 |
|---|---:|---|
| `eval/test_filter_candidates.py` | 19 | 候选池筛选、安全等级、专业池拆分、统计报告 |
| `eval/test_routing_dataset.py` | 17 | 150 条路由数据集 schema、类别、难度、冲突矩阵、多轮结构 |
| `eval/test_therapy_dataset.py` | 14 | 176 条疗法数据集 schema、边界对、难度、技术名有效性 |
| `eval/test_therapy_schema.py` | 14 | Therapy case schema 自洽性、枚举、默认接受集合、多轮语义 |
| `eval/test_run_therapy_eval.py` | 14 | 疗法离线评测 runner、产物、可重复性、指标结构、已知问题记录 |
| `eval/test_run_routing_eval.py` | 11 | 路由离线评测 runner、产物、指标结构、ES 互斥、离线执行 |
| `eval/test_pick_samples.py` | 13 | 评测样本抽取、主样本/专业样本、事件顺序、脱敏 |
| `eval/test_hide_therapy_name.py` | 10 | 问卷中隐藏疗法名称、评估后 reveal，避免评审暗示 |
| `eval/test_build_questionnaire.py` | 9 | HTML 问卷生成、双组配置、占位符、样本注入 |
| `eval/test_routing_schema.py` | 6 | Routing taxonomy、skill/family 合法集合、schema 错误检测 |
| **小计** | **127** |  |

`tests/eval/support/` 中的文件不是普通业务代码，而是离线评测支撑模块：

- `routing_schema.py`
- `therapy_schema.py`
- `run_routing_eval.py`
- `run_therapy_eval.py`

## 4. 测试是如何工作的

### 4.1 使用脚本化 FakeProvider，不访问真实 LLM

核心假件位于：

```text
tests/support/fakes.py
```

它提供：

- `FakeProvider`
  - `script_complete(model, responses)`：按顺序返回非流式 LLM 响应；
  - `script_stream(model, chunk_lists)`：按顺序返回流式 token；
  - 可以安排字符串、JSON、坏 JSON 或异常。
- `FakeSink`
  - 收集每次 LLM 调用的 agent、model、token、latency、success、error 等记账字段。
- `make_fake_client()`
  - 构造使用 FakeProvider 的真实 `LLMClient`。

因此，测试中运行的仍然是生产系统的真实调度器、状态机、store、Host、Agent、Skill 和 FastAPI 路由，只是 LLM 判断结果由测试脚本提供。

### 4.2 大量使用临时文件目录

集成测试通常使用 pytest 的 `tmp_path`：

```python
SessionStore(data_root=str(tmp_path))
ProfileStore(data_root=str(tmp_path))
PortraitStore(data_root=str(tmp_path))
```

这保证测试不会污染仓库中的 `data/` 或 `logs/`，也让每个测试拥有独立存盘环境。

### 4.3 覆盖同步和异步路径，但没有使用 pytest-asyncio

项目没有配置 `pytest-asyncio`。异步生产代码通常在测试中通过：

```python
asyncio.run(...)
```

驱动。

FastAPI 测试则使用 Starlette/FastAPI 的同步 `TestClient`，包括 SSE stream 的消费测试。

### 4.4 通过直接构造状态模拟复杂上下文

危机、疗法进行中、PMR active、Affect proposing、Grounding active 等场景，会通过写入测试用 `state.json` 或构造 `SessionState` 的方式建立前置条件，然后再驱动真实的 `Scheduler.handle_message()`。

这使测试能够稳定覆盖：

- 危机下一轮生效；
- 疗法与情绪支持互斥；
- PMR / Grounding / Affect 的优先级和恢复；
- 疗法 round cap、stagnation、regression；
- 用户中途断开 SSE；
- session 并发串行化。

## 5. 已覆盖的核心行为

### 5.1 调度器与三线时序

`integration/test_scheduler_phase2.py` 验证：

- SSE 事件顺序为 `token ... → dialogue_done → final`；
- 用户消息和 Host 回复写入同一条会话流水；
- trace_id 在同一轮 user/agent 条目中一致；
- Safety 判定危机后，危机模板下一轮才生效；
- Safety LLM 失败时回退 SAFE；
- profile 增量更新和 append 去重；
- summary 阈值触发；
- 同一 session 的并发消息按锁串行执行；
- SSE 客户端提前断开时，已经收到的部分 Host token 仍落盘。

### 5.2 安全、疗法和情绪支持优先级

跨模块测试明确锁定优先级：

```text
CRISIS > SAFETY > CURRENT THERAPY > PMR > GROUNDING > AFFECT LABELING > DAILY
```

覆盖：

- Crisis 抢占 therapy、PMR、Grounding、Affect；
- HIGH_RISK 暂停情绪支持，恢复后可继续；
- SwitchToTherapy 清理情绪支持残留；
- 疗法结束后情绪支持能力恢复；
- PMR、Grounding、Affect 任意时刻最多一个 active；
- 危机期间不触发额外情绪支持 LLM 调用；
- 各技能冷却、拒绝、退出、完成等生命周期不串状态。

### 5.3 五疗法编排和边际效用

`integration/test_therapy_phase3.py` 与相关单元测试覆盖：

- TherapyDecider 输出后创建 `SwitchToTherapy`；
- owner 与 therapy key 的双向协议；
- 五疗法 FLOW、分支和注册技能一致性；
- CBT 自动思维提取、认知歪曲分类、reframe、evaluate effect；
- ACT / DBT / MI / SFBT 的初始步骤路由；
- step judgment 的 `stay / advance / go_to / complete`；
- 疗法产品 products 的累积、去重和跨轮保存；
- round cap；
- 边际效用中的流程推进、状态改善、技术新颖性、停滞和硬倒退；
- invalid therapy name、LLM 异常等失败路径。

各疗法测试深度并不完全相同：ACT 的状态和技能测试最细，CBT 有完整链路，MI/SFBT 有独立技能契约测试，DBT 更多通过疗法编排、家族一致性和评测 runner 间接覆盖。

### 5.4 三个 Emotion Support 技能

PMR、Grounding、Affect Labeling 都有 gate、offer、响应分类、生命周期、安全排除和互斥测试。

重点包括：

- PMR 不把胸痛、心脏不适误判为肌肉放松；
- Grounding 不把普通感知描述误判为解离/失控；
- Affect Labeling 不重复命名用户已经明确说出的情绪；
- 三个技能都必须让位危机、安全和当前疗法；
- 技能 LLM 返回非法 JSON 或异常时，系统保持保守、安全的 fallback。

### 5.5 LLM Client、存盘和 HTTP 接口

已覆盖：

- complete / stream 的成功和失败记账；
- prompt/completion token、estimated usage、cache hit/miss；
- TTFT 和 latency；
- OpenAI / DeepSeek 风格 usage 字段解析；
- flow、state、orchestration、summary、profile 的写入和恢复；
- session reset；
- `/sessions`、`/chat`、history、profile、reset；
- portrait、user-info、personality regenerate；
- WebUI overview、users、state、metrics、logs。

当前 `GET /users/{user_id}/companion` 没有看到专门测试；`main.py` 的 CLI 交互也没有自动化测试。

## 6. 离线评测：150 条路由案例与 176 条疗法案例

### 6.1 Routing evaluation

数据集：

```text
tests/eval/eval_data/routing_cases.json
```

共 **150 条 gold case**，家族分布：

| Family | 数量 |
|---|---:|
| crisis | 21 |
| daily | 28 |
| emotion_support | 47 |
| therapy | 54 |
| **合计** | **150** |

已入库的历史评测报告：

```text
outputs/routing_evaluation/report.md
```

报告中的脚本化离线结果为：

| 指标 | 结果 |
|---|---:|
| family_accuracy | 1.0 |
| skill_accuracy | 1.0 |
| boundary_accuracy | 1.0 |
| over_intervention_rate | 0.0 |
| under_intervention_rate | 0.0 |
| safety_override_accuracy | 1.0 |
| ES exclusivity violations | 0 |
| family comparisons | 134 |
| skill comparisons | 106 |
| boundary comparisons | 26 |

这些结果的前提是：Safety、TherapyDecider、ES opportunity 等 LLM 判断槽由 gold agenda 脚本提供。真实执行的是下游 Scheduler、signal、ESO、状态机、冷却、技能选择和停止规则。

因此，`1.0` 表示：

> 在判断答案已由脚本按 gold 给出的条件下，系统的路由和状态处理机制没有把结果处理错。

它不表示真实大模型对自然语言的家族分类准确率为 100%。

### 6.2 Therapy evaluation

数据集：

```text
tests/eval/eval_data/therapy_cases.json
```

共 **176 条 gold case**，家族分布：

| Family | 数量 |
|---|---:|
| CBT | 31 |
| ACT | 30 |
| DBT | 26 |
| MI | 28 |
| SFBT | 25 |
| CRISIS | 10 |
| EMOTION_SUPPORT | 5 |
| 不需要疗法 / None | 21 |
| **合计** | **176** |

类别分布：

| Category | 数量 |
|---|---:|
| medium | 86 |
| boundary | 40 |
| easy | 24 |
| negative | 12 |
| ambiguous | 8 |
| multi_turn | 5 |
| lifecycle | 1 |

已入库历史报告：

```text
outputs/therapy_evaluation/report.md
```

报告中的脚本化离线结果为：

| 指标 | 结果 |
|---|---:|
| family_accuracy | 1.0 |
| skill_accuracy | 1.0 |
| boundary_accuracy | 1.0 |
| over_intervention_rate | 0.0 |
| under_intervention_rate | 0.0 |
| crisis_override_accuracy | 1.0 |
| therapy_continuity | 1.0 |
| theory_mixing_rate | 0.0 |
| fidelity_rate | 1.0 |
| lifecycle_error_rate | 0.0 |
| family comparisons | 168 |
| skill comparisons | 149 |

Fidelity 检查数：

| 疗法 | 结果 |
|---|---:|
| CBT | 279/279 |
| ACT | 240/240 |
| DBT | 182/182 |
| MI | 196/196 |
| SFBT | 200/200 |

报告还记录了已知生产改进点，例如：

- CBT 没有独立 assessment skill，编排中的 assessment 为 `None`，状态分数依赖分类置信度；
- ACT assessment 的 emotion 输入当前为 `{}`，部分唤醒/升级维度保持基线；
- skill 产生的 state_effects 没有直接应用到 `SessionState`，主要通过 orchestration products 持久化。

这些被记录为 findings，并不被离线 runner 伪装成测试失败或模型能力证明。

### 6.3 评测 runner 的测试只保证机械正确性

`tests/eval/test_run_routing_eval.py` 和 `tests/eval/test_run_therapy_eval.py` 主要断言：

- runner 能离线跑完；
- 结果、失败、跳过条目结构完整；
- 必需产物存在；
- 指标字段存在且范围合理；
- 两次运行结果可重复；
- 不会访问网络；
- 报告明确披露 scripting limitation；
- 同一轮不会出现多个疗法 family tag；
- 同一时刻不会有多个 live ES skill。

它们**没有**把真实模型准确率硬编码为必须 100%。这是合理的：模型判断波动应在真实模型评测中报告，而不是由离线机制测试承担。

## 7. 真实模型评估现状

仓库中存在真实 Affect Labeling 候选评估脚本：

```text
evals/eval_affect_candidates.py
```

该脚本内置 **17 条半真实中文对话语料**，需要配置真实 API Key 后运行：

```bash
python evals/eval_affect_candidates.py
python evals/eval_affect_candidates.py --dry
```

它会真实调用 `affect_labeling_opportunity`，输出候选、过滤结果、gate 与人工期望的对照。

但是当前仓库中没有找到：

```text
outputs/affect_candidate_eval/cases.jsonl
outputs/affect_candidate_eval/report.md
```

因此，当前仓库无法直接复核某次真实模型运行的 `15/17` 结果。脚本和语料存在，但结果产物没有随仓库保存。报告中不应把 `15/17` 写成当前可由 CI 或仓库产物证明的事实；它只能作为历史实验结论，前提是能找到当时的原始运行记录。

当前自动化测试也没有 live LLM marker、真实 provider contract test 或固定模型版本的夜间评测。

## 8. Seed runs、候选池与问卷资产

仓库包含 41 段 seed run 语料及统计：

```text
outputs/seed_runs/
outputs/candidate_pool.json
outputs/candidate_pool_stats.md
```

统计结果：

- 11 个 complete；
- 30 个 fragment；
- 0 个 broken/failed；
- 合计 41 个；
- 其中 14 个被标记为 boundary。

相关测试覆盖候选池拆分、安全等级、样本抽取、脱敏和问卷 HTML 生成，但这些属于评测工具链与数据治理测试，不等同于真实临床有效性验证。

## 9. CI 与质量门禁

当前工作区中的 GitHub Actions workflow：

```text
.github/workflows/deploy.yml
```

包含两个 job：

1. `test` / `Lint and test`
   - 在 Python 3.11 上安装 `requirements-dev.txt`；
   - 执行 `ruff check .`；
   - 执行 `pytest tests/ -q`。
2. `deploy`
   - `needs: test`；
   - PR 不部署；
   - push 到 `main` 或手动触发时，测试通过后才 rsync 并部署。

这意味着 lint 或测试失败时，自动部署不应继续。

需要注意：当前 CI 只跑 Python 3.11，没有 3.10 / 3.12 矩阵；项目声明 `requires-python = ">=3.10"`。

## 10. 现有测试能证明什么

可以较有信心地证明：

- 调度器事件顺序和轮边界语义不轻易回归；
- 危机、疗法、PMR、Grounding、Affect 的优先级不会轻易错乱；
- 情绪支持技能的生命周期、互斥、冷却、恢复有回归保护；
- 五疗法的 owner 协议、步骤推进和停止规则有较系统的机制测试；
- CBT/ACT 关键路径较扎实；
- JSONL/JSON 存盘、重启恢复、reset、断线部分写入有测试；
- LLM 记账、异常 JSON、模型异常等路径有兜底测试；
- FastAPI/SSE/WebUI 主路径可用；
- 150/176 条离线评测数据和 runner 有 schema 与机械正确性保护；
- 核心 prompt 快照和关键 prompt 字符串能防止一部分无意漂移。

## 11. 现有测试不能证明什么

当前测试不能单独证明：

- 真实 Safety LLM 对所有危机表达都能识别正确；
- TherapyDecider 在真实用户语言中总能选对 CBT/ACT/DBT/MI/SFBT；
- PMR / Grounding / Affect 的 opportunity LLM 在真实对话中总能正确 offer；
- LLM 生成的用户可见文本始终温暖、专业、合规；
- 所有 32 个 skill 的真实模型输出质量相同；
- 不同模型供应商的 JSON mode、stream usage、异常行为完全兼容；
- 高并发、长连接、网络抖动、nginx 缓冲、多进程部署下没有性能或竞态问题；
- 临床干预有效性。

根本原因是：782 个 pytest 全部使用 FakeProvider 或纯函数输入，LLM 判断槽本身被脚本化。

## 12. 空白与风险

按优先级看，当前主要空白是：

1. **真实模型层缺少 CI 外的可重复 smoke / regression evaluation**
   - 建议增加单独的 live marker，默认不随普通 CI 跑，固定模型版本和输出目录。

2. **覆盖率没有量化**
   - 未配置 `pytest-cov` 或 coverage 阈值，无法给出行覆盖率、分支覆盖率和未覆盖文件列表。

3. **Golden snapshot 不是全量 prompt 覆盖**
   - 当前只有 18 个 key，而系统注册了 32 个 skill。

4. **疗法技能测试深度不均衡**
   - ACT 最细；CBT 有完整链路；MI/SFBT 有基础契约；DBT 缺少同等细粒度的独立技能测试。

5. **生产装配存在复制逻辑**
   - 多数测试和离线 runner 手动组装 Scheduler，而不是直接复用 `src.bootstrap.build_scheduler()`；未来新增 engine 时可能出现测试装配与生产装配漂移。

6. **部分接口和 CLI 缺少直接测试**
   - `/users/{user_id}/companion` 未见专项测试；
   - `main.py` CLI 未见自动化测试。

7. **没有性能 / 压力 / 真实 SSE 基础设施测试**
   - 有同 session 串行锁测试，但没有高并发、长连接、吞吐、延迟、断流重试或 nginx 集成测试。

## 13. 建议的下一步

建议按以下顺序补齐：

1. 为 `build_scheduler()` 增加装配 smoke test，确保生产 wiring 与测试 wiring 不漂移；
2. 补 `/users/{user_id}/companion` 和 CLI 最小回归测试；
3. 引入 coverage 报告，先建立基线，不急着设置高阈值；
4. 扩展 golden snapshot，至少自动覆盖所有已注册 skill 的代表性 `build_prompt()`；
5. 补齐 DBT skill 契约测试，并拉齐 CBT/DBT/MI/SFBT 的 fallback / enum clamp / schema 测试；
6. 增加默认跳过的 live LLM 测试/评测层，记录模型版本、prompt 版本、样本结果和失败样本；
7. 等真实模型评测运行完成后，将结果产物入库或在报告中附上可复核路径，再引用具体准确率。

## 14. 最终评价

当前测试体系不是“象征性写了几个单测”，而是一套围绕系统核心状态机建立的、规模可观的离线回归体系。它最大的优点是快、稳定、无需密钥，并且对调度时序、危机优先级、疗法编排和情绪支持互斥等复杂机制有明确保护。

但它的定位应当被准确表述为：

> **782 个自动化测试主要验证确定性后端机制；150 条路由案例和 176 条疗法案例是 gold-informed 的离线机制评测；真实大模型分类和生成质量仍需独立 live evaluation 与人工评审。**

这种“确定性机制回归 + 离线 gold 数据集 + 待补真实模型评估”的分层是合理的工程基础，但不应把脚本化评测的 100% 宣传成真实模型效果的 100%。
