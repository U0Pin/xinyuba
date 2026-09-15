"""技能 prompt 子包。

每个 family 文件（cbt / act / dbt / mi / sfbt）把 LLM 用的静态字符串
（`*_PROMPT` / `*_OUTPUT_SCHEMA` / 状态阈值列表）拆到这里，
让 family 文件只展示 skill 类的骨架与注册逻辑。

**注意**：所有字符串被 `tests/golden_v2/snapshots.json` 字节级冻结，
任何改动都会破坏黄金快照测试。
"""
