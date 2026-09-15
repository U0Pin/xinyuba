"""评测样本脱敏 — 删除 user_id / session_id / trace_id，保留 user_text / agent_text。

脱敏规则：
  - user_id    → "subject-{case_id}"
  - session_id → "sess-{case_id}-{n}"  (n 为整数序列)
  - trace_id   → "trace-{case_id}-{n}"
  - user_text / agent_text 保留（保留咨询真实性）
"""
import hashlib
import re
from pathlib import Path
from typing import Any

# 简易 PII 模式（保险起见）
_PII_PATTERNS = [
    re.compile(r"1[3-9]\d{9}"),  # 手机号
    re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),  # 邮箱
    re.compile(r"(?:\d{1,3}\.){3}\d{1,3}"),  # IPv4
]


def _hash(s: str, salt: str = "eval-2026-09") -> str:
    """简短哈希，避免明文泄漏。"""
    return hashlib.sha256(f"{salt}:{s}".encode()).hexdigest()[:10]


def anonymize_summary(summary: dict | Any, case_id: str = "") -> dict:
    """对一个 case summary 做脱敏。

    不修改原对象，返回新 dict。
    """
    if not isinstance(summary, dict):
        return summary

    case = case_id or summary.get("seed_id") or "unknown"

    out = dict(summary)

    # 顶层字段
    if "user_id" in out and out["user_id"] is not None:
        out["user_id"] = f"subject-{_hash(out['user_id'], case)}"
    if "session_id" in out and out["session_id"] is not None:
        out["session_id"] = f"sess-{case}-0"
    if "trace_id" in out and out["trace_id"] is not None:
        out["trace_id"] = f"trace-{case}-0"

    # turns 里的 trace_id / state_after.user_id 等
    new_turns = []
    for i, t in enumerate(out.get("turns", []), start=1):
        nt = dict(t)
        if "trace_id" in nt and nt["trace_id"] is not None:
            nt["trace_id"] = f"trace-{case}-{i}"
        # state_after 不应有 user_id/session_id，但保险起见也清一遍
        sa = nt.get("state_after")
        if isinstance(sa, dict):
            sa = dict(sa)
            sa.pop("user_id", None)
            sa.pop("session_id", None)
            nt["state_after"] = sa
        # PII 兜底：user_text / agent_text 内若出现手机/邮箱/IP，替换为 [REDACTED]
        for key in ("user_text", "agent_text"):
            v = nt.get(key)
            if isinstance(v, str):
                for pat in _PII_PATTERNS:
                    v = pat.sub("[REDACTED]", v)
                nt[key] = v
        new_turns.append(nt)
    out["turns"] = new_turns

    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python eval_anonymize.py <seed_run.json> [case_id]", file=sys.stderr)
        sys.exit(1)
    path = Path(sys.argv[1])
    case_id = sys.argv[2] if len(sys.argv) > 2 else path.stem
    import json
    summary = json.loads(path.read_text())
    out = anonymize_summary(summary, case_id)
    print(json.dumps(out, ensure_ascii=False, indent=2))
