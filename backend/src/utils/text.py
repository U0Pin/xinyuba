"""共享文本/估算工具（纯函数，无依赖）。

收敛此前散落在多条线的重复实现：

- `format_transcript`  中文对话转写（"用户：/AI："），对话线 / 决策线 /
  疗法决策 / 编排共用同一口径；
- `history_pairs`      流水条目 → skill 输入的 [{"user":…}|{"agent":…}] 形态；
- `est_tokens_half`    决策/沉淀侧 token 估算口径（字符数/2，中文 1 字≈0.6–0.7
  token 的折中；WINDOW_TOKEN_THRESHOLD 与 SUMMARY_TOKEN_THRESHOLD 用此口径，
  与 config 注释一致）；
- `est_tokens_quarter` 记账兜底口径（约 4 字符/token，provider 未回 usage 时
  对混合中英文文本的粗略估计；仅用于 llm_calls 记账）。

两个估算口径是**刻意不同**的：前者决定触发时机（对中文更贴近），后者只做
成本记账的缺省值。合并口径会改变触发/重生成时机 = 行为变更，故分名保留。
"""


def format_transcript(entries: list[dict]) -> str:
    """流水条目 → "用户：…/AI：…" 多行文本（时间序）。"""
    lines = []
    for e in entries:
        role = "用户" if e.get("role") == "user" else "AI"
        lines.append(f"{role}：{e.get('text', '')}")
    return "\n".join(lines)


def history_pairs(entries: list[dict]) -> list[dict]:
    """流水条目 → skill prompt 的对话历史形态（user/agent 键字典）。"""
    return [
        {"user": e.get("text", "")} if e.get("role") == "user" else {"agent": e.get("text", "")}
        for e in entries
    ]


def est_tokens_half(text: str) -> int:
    """粗略 token 估算：字符数/2（触发口径，见模块 docstring）。"""
    return max(0, len(text or "") // 2)


def est_tokens_quarter(text: str) -> int:
    """粗略 token 估算：约字符数/4（记账兜底口径，见模块 docstring）。"""
    return max(1, len(text) // 4)
