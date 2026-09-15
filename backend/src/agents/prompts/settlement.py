"""沉淀线的 LLM prompt（新架构，Phase 2）：画像增量更新 + 会话摘要。

**注意**：这些字符串将被 Phase 2 黄金快照字节级冻结，修改需重捕获并人工审 diff。
"""

PROFILE_UPDATE_PROMPT = """你是用户画像沉淀模块。

你的职责是从最新一轮对话中增量提取用户**长期稳定**的特征，更新用户画像。
只提取长期特质（依恋风格、核心恐惧、个人价值、拒绝敏感度、痛苦耐受、长期障碍、
重视的活动等）；单次的、情境性的情绪表达不写入。

画像常见字段：attachment_style / core_fears / personal_values /
rejection_sensitivity / distress_tolerance / valued_activities / known_barriers。

- updates：覆盖或新增标量/字符串字段；
- append_lists：向列表字段去重追加；
- 本轮没有新信息时 no_change 置 true。

Output only the JSON object. No other text."""

PROFILE_OUTPUT_SCHEMA = """
{
  "updates": {"attachment_style": "anxious"},
  "append_lists": {"core_fears": ["被拒绝"]},
  "no_change": false
}
"""

SUMMARY_PROMPT = """你是会话上下文摘要模块。

基于已有摘要和新增对话流水，维护一份结构化的会话摘要。
每条摘要条目含：theme（主题）、fact（关键事实）、emotion（情绪）、
seq_range（覆盖的流水序号区间，闭区间）。

合并原则：保留仍相关的旧条目，更新或新增条目，删除已解决或不再相关的条目；
保持摘要精炼，条目数不要无限制增长。

Output only the JSON object. No other text."""

SUMMARY_OUTPUT_SCHEMA = """
{
  "summary": [
    {"theme": "工作压力", "fact": "用户已连续数月感到工作压力大", "emotion": "焦虑", "seq_range": [1, 3]}
  ]
}
"""
