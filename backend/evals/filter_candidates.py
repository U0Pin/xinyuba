"""三层质量闸门筛选 → 输出候选池。

读 outputs/seed_runs/*.json，按：
  ① 结构完整性（actual_therapy 非空 + therapy_rounds ≥ 4 + orch_step_changes ≥ 2 + 无 turn error）
  ② 内容可评估性（agent_text 总长足够 + 多轮有变化 + 至少触发 N 次 skill）
  ③ 研究价值（actual_therapy 在 5 个疗法之一 + 无 CRISIS 持续 + 多轮 user_text 平均长度合理）

三层全过 → 进一步判定 complete / fragment（编排 ≥3 步骤 → complete；1-2 步骤 → fragment）。
任一闸门不过 → fragment。

输出：
  outputs/candidate_pool.json       # 完整 case（按 actual_therapy 分桶）+ fragment + broken
  outputs/candidate_pool_stats.md   # 候选池统计（按用户十四节要求组织）

设计意图 target_therapy 仅用于对比「决策器实际分流是否符合设计意图」，与 actual_therapy 不匹配
的 boundary case 必须按 actual_therapy 进入候选池，并标记 target/actual 不一致。
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# 路径
_AGENT_DIR = Path(__file__).resolve().parent.parent
_PROJECT_DIR = _AGENT_DIR
RUNS_DIR = _PROJECT_DIR / "outputs" / "seed_runs"
POOL_JSON = _PROJECT_DIR / "outputs" / "candidate_pool.json"
POOL_STATS = _PROJECT_DIR / "outputs" / "candidate_pool_stats.md"

VALID_THERAPIES = {"CBT", "ACT", "DBT", "MI", "SFBT"}

# ── 专项安全 seed 硬隔离（设计意图：不进普通用户池） ──
# 由 seed_scripts.py 中 safety_tier="professional_only" 标记。
# 这些 seed 在 filter 阶段直接路由到 professional_only_pool，
# 与 general seed 完全隔离；不参与普通池的 complete/fragment 统计，
# 也走同样的三层闸门与 completeness 规则（不修改闸门）。
PROFESSIONAL_ONLY_SEEDS = {
    "dbt-distress-tolerance-tipp-self-harm-urge",
    "dbt-mindfulness-grounding-panic-alone",
}


def _classify_safety_tier(seed_id: str) -> str:
    """把 seed 路由到 general 或 professional_only 池。"""
    if seed_id in PROFESSIONAL_ONLY_SEEDS:
        return "professional_only"
    return "general"

# ── broken/failed 错误白名单（API/模型/运行异常，与 fragment 严格区分） ──
BROKEN_ERROR_PATTERNS = (
    "Error code",        # OpenAI-compatible API 错误前缀
    "Insufficient Balance",  # 余额不足（旧 deepseek 案例）
    "RegionError",       # 模型区域限制
    "timeout", "Timeout", "AsyncTimeout",
    "Connection", "ConnectionError",
    "PermissionDenied", "AuthenticationError",
    "APIConnectionError", "RateLimitError",
)

# ── 三层闸门阈值（用户十四节要求：不得为凑数量修改） ──
STRUCT_MIN_THERAPY_ROUNDS = 4    # 疗法内至少 4 轮
STRUCT_MIN_STEP_CHANGES = 2      # 至少 2 次步骤推进（核心质量门槛；不要降低）
STRUCT_NO_TURN_ERROR = True      # 不能有 turn 级异常

CONTENT_MIN_AGENT_CHARS = 300    # Agent 总回复字符下限
CONTENT_MIN_TURNS_WITH_SKILL = 3 # 至少 3 轮触发 skill 输出

RESEARCH_NO_CRISIS = True        # 普通用户测试排除危机持续态
RESEARCH_MIN_USER_MSG_CHARS = 6  # 用户消息平均长度下限


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _evaluate_three_gates(summary: dict) -> dict[str, Any]:
    """三层闸门评估 → 返回结构化结果。"""
    turns = summary.get("turns", [])
    actual = summary.get("actual_therapy")
    therapy_rounds = summary.get("therapy_rounds", 0)
    step_changes = summary.get("orch_step_changes", 0)
    errors = [t for t in turns if t.get("error")]

    # ── ① 结构完整性 ──
    struct_reasons = []
    if not actual:
        struct_reasons.append("actual_therapy 为空（未触发疗法或已 EndTherapy）")
    if actual and actual not in VALID_THERAPIES:
        struct_reasons.append(f"actual_therapy={actual} 不在 5 个标准疗法中")
    if actual and therapy_rounds < STRUCT_MIN_THERAPY_ROUNDS:
        struct_reasons.append(f"therapy_rounds={therapy_rounds} < {STRUCT_MIN_THERAPY_ROUNDS}")
    if actual and step_changes < STRUCT_MIN_STEP_CHANGES:
        struct_reasons.append(f"orch_step_changes={step_changes} < {STRUCT_MIN_STEP_CHANGES}")
    if STRUCT_NO_TURN_ERROR and errors:
        struct_reasons.append(f"出现 {len(errors)} 个 turn 级异常")
    struct_pass = len(struct_reasons) == 0

    # ── ② 内容可评估性 ──
    content_reasons = []
    agent_chars = sum(len(t.get("agent_text", "") or "") for t in turns)
    if agent_chars < CONTENT_MIN_AGENT_CHARS:
        content_reasons.append(f"agent_text 总长 {agent_chars} 字符 < {CONTENT_MIN_AGENT_CHARS}")

    skill_turns = sum(
        1 for t in turns
        if (t.get("orchestration_after") or {}).get("skill_output")
        and not (t["orchestration_after"]["skill_output"].get("error"))
    )
    if skill_turns < CONTENT_MIN_TURNS_WITH_SKILL:
        content_reasons.append(f"仅 {skill_turns} 轮触发 skill 输出，< {CONTENT_MIN_TURNS_WITH_SKILL}")

    distinct_agents = len({(t.get("agent_text") or "")[:80] for t in turns})
    if distinct_agents < 4:
        content_reasons.append(f"Agent 响应多样性不足：仅 {distinct_agents} 个不同片段")
    content_pass = len(content_reasons) == 0

    # ── ③ 研究价值 ──
    research_reasons = []
    if RESEARCH_NO_CRISIS and summary.get("final_crisis"):
        research_reasons.append("对话结束于 CRISIS 态（普通用户测试排除）")
    user_msgs = [t.get("user_text", "") for t in turns if t.get("user_text")]
    if user_msgs:
        avg_user_chars = sum(len(m) for m in user_msgs) / len(user_msgs)
        if avg_user_chars < RESEARCH_MIN_USER_MSG_CHARS:
            research_reasons.append(f"user_text 平均 {avg_user_chars:.1f} 字符过短")
    research_pass = len(research_reasons) == 0

    return {
        "structure": {"pass": struct_pass, "reasons": struct_reasons,
                      "agent_chars": agent_chars, "skill_turns": skill_turns,
                      "distinct_agent_fragments": distinct_agents},
        "content": {"pass": content_pass, "reasons": content_reasons},
        "research": {"pass": research_pass, "reasons": research_reasons},
        "all_pass": struct_pass and content_pass and research_pass,
    }


def _milestone_reached(turn: dict) -> bool:
    """该轮编排是否显示疗程已足够深入（complete 动作 / 效用终止 / 旧 continue_eval）。

    兼容两代 orchestration.json：旧 run 数据只有 continue_eval（完成后每轮才有），
    新 run 数据每轮都有 utility_eval，因此以"里程碑或终止"作为等价的深入信号。
    """
    o = turn.get("orchestration_after") or {}
    if o.get("continue_eval"):
        return True
    ue = o.get("utility_eval")
    if not ue:
        return False
    return bool(ue.get("stop")) or ue.get("action") == "complete"


def _assess_completeness(summary: dict, gate: dict) -> str:
    """对通过三层闸门的 case 做编排覆盖度评估：
       - 'complete': 编排 ≥3 步骤 或 走到过里程碑/终止（complete 动作 / utility stop /
         旧数据的 continue_eval）
       - 'fragment': 仅 1-2 步骤 / 编排不完整但仍在疗法内
       - 'broken': 无编排信息（保留以防二次校验）
    """
    if not gate["all_pass"]:
        return "fragment"
    turns = summary.get("turns", [])
    orch_steps: list[str] = []
    for t in turns:
        o = t.get("orchestration_after") or {}
        s = o.get("current_step")
        if s and s not in orch_steps:
            orch_steps.append(s)
    continue_eval_count = sum(1 for t in turns if _milestone_reached(t))
    if len(orch_steps) >= 3 or continue_eval_count > 0:
        return "complete"
    if len(orch_steps) >= 1:
        return "fragment"
    return "broken"


def _is_broken_or_failed(summary: dict) -> bool:
    """识别 API/模型/运行异常 case（与 fragment 严格分离）。

    触发条件（任一）：
      - 任一 turn.error 字段含 BROKEN_ERROR_PATTERNS 中任一字符串
      - 全部 turn 的 agent_text 为空 + 全部 state_after.therapy 为 None
        （说明 LLM 完全没产出，决策器没有疗法机会）
    """
    turns = summary.get("turns", []) or []
    if not turns:
        return True
    # 规则 1: turn error 命中 API 错误白名单
    for t in turns:
        err = t.get("error") or ""
        for pat in BROKEN_ERROR_PATTERNS:
            if pat in err:
                return True
    # 规则 2: LLM 完全没产出 + 从未进入疗法
    total_agent_chars = sum(len(t.get("agent_text", "") or "") for t in turns)
    any_therapy = any((t.get("state_after") or {}).get("therapy") for t in turns)
    if total_agent_chars == 0 and not any_therapy:
        return True
    return False


def _enrich_case(summary: dict, gate: dict, completeness: str) -> dict:
    """构建单个 case 的完整记录（用户十四节 9：14 字段 + 对话内容 + session/trace/orchestration/skill 信息）。"""
    target = summary.get("target_family")
    actual = summary.get("actual_therapy")
    is_boundary = (target is not None and actual is not None and target != actual)

    # 收集所有触发疗法的轮次（从 state_after 的 therapy.name 判断）
    therapy_started_turn = None
    therapy_ended_turn = None
    for t in summary.get("turns", []):
        st = t.get("state_after") or {}
        ther = st.get("therapy")
        if ther and therapy_started_turn is None:
            therapy_started_turn = t["turn"]
        if ther is None and therapy_started_turn is not None and therapy_ended_turn is None:
            therapy_ended_turn = t["turn"]

    # 每个 turn 的 session/trace/decision/orchestration/skill 信息
    turns_full = []
    for t in summary.get("turns", []):
        o = t.get("orchestration_after") or {}
        skill_out = o.get("skill_output") or {}
        events = t.get("events") or []
        event_types = [e.get("event") for e in events]
        decision_event = next(
            (e for e in events if e.get("event") in ("therapy_decide_done", "orchestration_done")),
            None,
        )
        decision_summary = None
        if decision_event:
            d = decision_event.get("data") or {}
            decision_summary = {
                "therapy": d.get("therapy"),
                "step": d.get("step"),
                "skill": d.get("skill"),
                "judgment_action": d.get("judgment", {}).get("action") if isinstance(d.get("judgment"), dict) else None,
                "rationale": d.get("rationale") or d.get("reason"),
            }

        turns_full.append({
            "turn": t["turn"],
            "trace_id": t.get("trace_id"),
            "user_text": t.get("user_text", ""),
            "agent_text": t.get("agent_text", ""),
            "owner": (t.get("state_after") or {}).get("owner"),
            "risk_level": (t.get("state_after") or {}).get("risk_level"),
            "orchestration": {
                "current_step": o.get("current_step"),
                "next_step": o.get("next_step"),
                "step_judgment": o.get("step_judgment"),
                "assessment_keys": list((o.get("assessment") or {}).keys()),
                "skill_name": skill_out.get("_skill_name"),
                "skill_output_excerpt": _skill_excerpt(skill_out),
                "continue_eval": o.get("continue_eval") or (
                    None if not o.get("utility_eval") else {
                        "continue": not o["utility_eval"].get("stop"),
                        "reason": f"utility:{o['utility_eval'].get('rule_hit') or 'continue'}",
                    }
                ),
            } if o else None,
            "events": event_types,
            "decision_summary": decision_summary,
            "error": t.get("error"),
        })

    return {
        # ── provenance（spec §8） ──
        "source": _infer_source(summary["seed_id"]),
        "quality_status": completeness,
        # ── 候选池分桶（一般/专项安全，硬隔离） ──
        "safety_tier": _classify_safety_tier(summary["seed_id"]),
        # ── 用户十四节 9：核心 14 字段 ──
        "case_id": summary["seed_id"],
        "target_therapy": target,
        "actual_therapy": actual,
        "target_actual_match": (target == actual) if (target and actual) else False,
        "is_boundary_case": is_boundary,
        "session_id": summary.get("session_id"),
        "user_id": summary.get("user_id"),
        "trace_ids": [t.get("trace_id") for t in summary.get("turns", []) if t.get("trace_id")],
        "scenario_label": summary.get("scenario_label"),
        "scenario_description": summary.get("scenario_description"),
        "risk_level_final": summary.get("final_risk_level"),
        "risk_level_trajectory": [t.get("state_after", {}).get("risk_level") for t in summary.get("turns", [])],
        "crisis_engaged": bool(summary.get("final_crisis")),
        "therapy_rounds": summary.get("therapy_rounds"),
        "step_changes": summary.get("orch_step_changes"),
        "therapy_started_turn": therapy_started_turn,
        "therapy_ended_turn": therapy_ended_turn,
        "switched_to_therapy_at_turn": summary.get("switched_to_therapy_at_turn"),
        "orchestration_steps_in_order": _orch_steps_in_order(summary),
        "skill_outputs_summary": _skill_outputs_summary(summary),
        "total_turns": summary.get("turns_total"),
        # ── 状态判定 ──
        "completeness": completeness,
        "gates_passed": gate["all_pass"],
        "gate_results": gate,
        "fragment_reasons": _collect_fragment_reasons(gate, completeness, summary),
        # ── 对话全文 ──
        "turns": turns_full,
    }


def _orch_steps_in_order(summary: dict) -> list[str]:
    seen: list[str] = []
    for t in summary.get("turns", []):
        s = (t.get("orchestration_after") or {}).get("current_step")
        if s and s not in seen:
            seen.append(s)
    return seen


def _skill_outputs_summary(summary: dict) -> list[dict]:
    out: list[dict] = []
    for t in summary.get("turns", []):
        skill_out = (t.get("orchestration_after") or {}).get("skill_output") or {}
        if not skill_out:
            continue
        out.append({
            "turn": t["turn"],
            "skill_name": skill_out.get("_skill_name"),
            "excerpt": _skill_excerpt(skill_out),
        })
    return out


def _skill_excerpt(skill_out: dict) -> dict:
    """截取 skill_output 中对评估有用的字段（去掉过长 payload 和 _skill_name 等元数据）。"""
    if not skill_out:
        return {}
    excerpt = {}
    for k, v in skill_out.items():
        if k.startswith("_"):
            continue
        if isinstance(v, str) and len(v) > 200:
            excerpt[k] = v[:200] + "…"
        elif isinstance(v, (dict, list)):
            excerpt[k] = v if len(str(v)) <= 400 else str(v)[:400] + "…"
        else:
            excerpt[k] = v
    return excerpt


def _collect_fragment_reasons(gate: dict, completeness: str, summary: dict) -> list[str]:
    """收集 fragment 原因（用户十四节 9 要求保留 fragment 原因）。"""
    reasons: list[str] = []
    for layer in ("structure", "content", "research"):
        for r in gate[layer]["reasons"]:
            reasons.append(f"[{layer}] {r}")
    if gate["all_pass"] and completeness == "fragment":
        # 三层闸门全过但编排 <3 步骤
        steps = _orch_steps_in_order(summary)
        reasons.append(f"[completeness] 三层闸门全过，但编排步骤仅 {len(steps)} 个：{steps}")
    return reasons


# ── provenance：本轮所有 case 均为 new_seed（来自 seed 跑批） ──
# existing_log 字段保留作未来扩展（从 logs/app.jsonl 抽取数据时使用）
NEW_SEED_IDS = {
    # 第一轮 15 个
    "cbt-disaster-interview", "cbt-perfectionism-procrastination", "cbt-self-devaluation",
    "act-value-conflict-career", "act-defusion-rumination", "act-acceptance-illness",
    "dbt-emotion-regulation", "dbt-interpersonal-conflict", "dbt-mindfulness-anxiety",
    "mi-ambivalence-smoking", "mi-change-career", "mi-motivation-weight",
    "sfbt-exception-finding", "sfbt-scaling-confidence", "sfbt-miracle-question",
    # 本轮新增 8 个
    "cbt-social-avoidance", "cbt-sleep-rumination", "cbt-exam-anxiety", "cbt-breakup-rumination",
    "dbt-emotion-burst", "dbt-impulsive-shopping", "dbt-family-conflict", "dbt-panic-grounding",
}


def _infer_source(seed_id: str) -> str:
    """根据 seed_id 推断 source。本轮所有 case 均为 new_seed。"""
    if seed_id in NEW_SEED_IDS:
        return "new_seed"
    return "existing_log"


def _build_one_pool(run_files: list[Path]) -> dict:
    """按 run_files 列表聚合 → 普通/专项安全 pool 结构。

    内部状态分离：
      - complete: 三层闸门全过 + 编排覆盖完整
      - fragment: 三层闸门任一未过（但 LLM/调度正常）
      - broken_or_failed: API/模型/运行异常（绝不与 fragment 混）
    """
    pool: dict[str, list[dict]] = {t: [] for t in sorted(VALID_THERAPIES)}
    fragments: list[dict] = []
    broken: list[dict] = []

    for path in run_files:
        summary = _read_json(path)
        if summary is None:
            broken.append({
                "case_id": path.stem,
                "source": _infer_source(path.stem),
                "seed_id": path.stem,
                "target_therapy": None,
                "actual_therapy": None,
                "quality_status": "broken",
                "boundary_case": False,
                "reason": "JSON 读取失败",
            })
            continue

        # 第一道判定：是否 broken/failed（API/运行异常）
        if _is_broken_or_failed(summary):
            case = _enrich_case(summary, _evaluate_three_gates(summary), "broken")
            case["quality_status"] = "broken"
            broken.append(case)
            continue

        # 第二道判定：三层闸门 + completeness
        gate = _evaluate_three_gates(summary)
        completeness = _assess_completeness(summary, gate)
        case = _enrich_case(summary, gate, completeness)

        if completeness == "complete":
            pool[case["actual_therapy"]].append(case)
        elif completeness == "fragment":
            case["quality_status"] = "fragment"
            fragments.append(case)
        else:  # completeness == "broken"（编排完全缺失但 LLM 正常——边界情况）
            case["quality_status"] = "broken"
            broken.append(case)

    return {"complete_by_therapy": pool, "fragments": fragments, "broken_or_failed": broken}


def build_candidate_pool(run_files: list[Path]) -> dict:
    """聚合所有 run → 普通池 + 专项安全池。

    按 safety_tier 硬隔离：PROFESSIONAL_ONLY_SEEDS 中的 seed 路由到
    professional_only_pool，与 general seed 完全隔离。判定逻辑（三层闸门 +
    completeness）通过复用 _build_one_pool 完全相同。
    """
    general_files = [p for p in run_files if _classify_safety_tier(p.stem) == "general"]
    pro_files = [p for p in run_files if _classify_safety_tier(p.stem) == "professional_only"]

    result = _build_one_pool(general_files)
    result["professional_only_pool"] = _build_one_pool(pro_files)
    return result


def render_stats_md(pool: dict) -> str:
    """按用户十四节 14：生成 7 项候选池统计。"""
    lines: list[str] = ["# 候选池统计", ""]

    by_therapy = pool["complete_by_therapy"]
    fragments = pool["fragments"]
    broken = pool["broken_or_failed"]  # ← 新增栏
    complete_all = [c for items in by_therapy.values() for c in items]
    boundary_cases = [c for c in (complete_all + fragments) if c.get("is_boundary_case")]

    # ── ① 各疗法 complete 数量 ──
    lines.append("## 1. 各疗法 complete 数量")
    lines.append("")
    lines.append("| 疗法 | complete 数 | 备注 |")
    lines.append("|---|---|---|")
    for therapy, items in by_therapy.items():
        note = "" if items else "**当前模型/Agent 配置下无完整 CBT candidate**（如适用）" if therapy in ("CBT", "DBT") else ""
        lines.append(f"| {therapy} | {len(items)} | {note} |")
    lines.append(f"| **合计** | **{len(complete_all)}** | |")
    lines.append("")

    # ── ② 各疗法 fragment 数量 ──
    lines.append("## 2. 各疗法 fragment 数量")
    lines.append("")
    frag_by_actual: dict[str, int] = {t: 0 for t in sorted(VALID_THERAPIES)}
    frag_untriggered = 0
    for f in fragments:
        a = f.get("actual_therapy")
        if a in frag_by_actual:
            frag_by_actual[a] += 1
        else:
            frag_untriggered += 1
    lines.append("| 疗法 | fragment 数 |")
    lines.append("|---|---|")
    for therapy, n in frag_by_actual.items():
        lines.append(f"| {therapy} | {n} |")
    lines.append(f"| （未触发疗法） | {frag_untriggered} |")
    lines.append(f"| **合计** | **{len(fragments)}** |")
    lines.append("")

    # ── ③ target → actual 分流情况 ──
    lines.append("## 3. target → actual 分流情况")
    lines.append("")
    flux = Counter()
    for c in complete_all + fragments:
        flux[(c.get("target_therapy") or "?", c.get("actual_therapy") or "（未触发）")] += 1
    lines.append("| target | actual | 数量 |")
    lines.append("|---|---|---|")
    for (tgt, act), n in sorted(flux.items(), key=lambda x: (-x[1], x[0])):
        marker = " ⚠ boundary" if tgt != act else ""
        lines.append(f"| {tgt} | {act} | {n}{marker} |")
    lines.append("")

    # ── ④ boundary case 数量 ──
    lines.append("## 4. boundary case 数量")
    lines.append("")
    lines.append(f"- 完整案例中 boundary（target ≠ actual）: **{sum(1 for c in complete_all if c['is_boundary_case'])}** 个")
    lines.append(f"- Fragment 中 boundary: **{sum(1 for c in fragments if c['is_boundary_case'])}** 个")
    lines.append(f"- **合计 boundary case: {len(boundary_cases)}** 个")
    lines.append("")
    if boundary_cases:
        lines.append("**Boundary 列表**（按 actual_therapy 归桶，不能重新归回 target）：")
        lines.append("")
        lines.append("| case_id | target | actual | quality |")
        lines.append("|---|---|---|---|")
        for c in boundary_cases:
            lines.append(f"| {c['case_id']} | {c['target_therapy']} | {c['actual_therapy'] or '（未触发）'} | {c['completeness']} |")
    lines.append("")

    # ── ⑤ 每个案例的质量状态 ──
    lines.append("## 5. 每个案例的质量状态")
    lines.append("")
    lines.append(f"- **Complete: {len(complete_all)}**")
    lines.append(f"- **Fragment: {len(fragments)}**")
    lines.append(f"- **Broken/Failed: {len(broken)}**")
    lines.append(f"- **合计: {len(complete_all) + len(fragments) + len(broken)}**")
    lines.append("")
    lines.append("### 完整案例明细")
    lines.append("")
    lines.append("| case_id | target | actual | 轮数 | therapy_rounds | step_changes | risk |")
    lines.append("|---|---|---|---|---|---|---|")
    for _therapy, items in by_therapy.items():
        for c in items:
            tag = "" if c["target_actual_match"] else f" ⚠target={c['target_therapy']}"
            lines.append(
                f"| {c['case_id']}{tag} | {c['target_therapy']} | {c['actual_therapy']} | "
                f"{c['total_turns']} | {c['therapy_rounds']} | {c['step_changes']} | "
                f"{c['risk_level_final'] or '-'} |"
            )
    lines.append("")
    lines.append("### Fragment 明细")
    lines.append("")
    lines.append("| case_id | target | actual | 主要 fragment 原因 |")
    lines.append("|---|---|---|---|")
    for c in fragments:
        reason_main = c["fragment_reasons"][0] if c["fragment_reasons"] else "（无记录）"
        tag = "" if c["target_actual_match"] else f" ⚠target={c['target_therapy']}"
        lines.append(f"| {c['case_id']}{tag} | {c['target_therapy'] or '-'} | {c['actual_therapy'] or '（未触发）'} | {reason_main} |")
    lines.append("")
    if broken:
        lines.append("### Broken / Failed 明细（运行异常，与 fragment 严格分离）")
        lines.append("")
        for b in broken:
            reason = b.get("reason", "（未识别）")
            actual = b.get("actual_therapy") or "（未触发）"
            lines.append(f"- **{b['case_id']}** — {reason}（actual={actual}）")
        lines.append("")

    # ── ⑥ CBT / DBT 等缺失疗法的真实情况 ──
    lines.append("## 6. CBT / DBT 等缺失疗法的真实情况")
    lines.append("")
    missing = [t for t in sorted(VALID_THERAPIES) if not by_therapy[t]]
    if missing:
        lines.append(f"**当前模型/Agent 配置下，以下疗法无 complete candidate：{', '.join(missing)}**")
        lines.append("")
        lines.append("具体观察：")
        lines.append("")
        # CBT/DBT 推理
        for therapy in missing:
            trig = [c for c in (complete_all + fragments) if c["actual_therapy"] == therapy]
            designed = [c for c in (complete_all + fragments) if c["target_therapy"] == therapy]
            if trig:
                lines.append(f"- **{therapy}** 实际触发但未达 complete 门槛的案例：")
                for c in trig:
                    if c["completeness"] == "fragment":
                        reason_main = c["fragment_reasons"][0] if c["fragment_reasons"] else ""
                        lines.append(f"  - `{c['case_id']}`（target={c['target_therapy']}）— {reason_main}")
                    else:
                        lines.append(f"  - `{c['case_id']}`（target={c['target_therapy']}）— 已 complete")
            if designed and not trig:
                lines.append(f"- **{therapy}** 设计了但决策器从未触发：")
                for c in designed:
                    lines.append(f"  - `{c['case_id']}`（target={therapy} → actual={c['actual_therapy'] or '（未触发）'}，{c['completeness']}）")
        lines.append("")
        lines.append("**这是真实的系统行为，不应通过修改 seed、阈值或决策逻辑来强行制造 CBT/DBT candidate。**")
        lines.append("")
    else:
        lines.append("五种疗法均有 complete candidate。")
        lines.append("")

    # ── ⑦ 是否满足正式用户测试的最低候选数量要求 ──
    lines.append("## 7. 是否满足正式用户测试的最低候选数量要求")
    lines.append("")
    MIN_CANDIDATES_PER_THERAPY = 4  # 用户十节：每种疗法准备约 4-6 个高质量候选
    lines.append("**最低要求**（用户十节）：每种疗法准备约 4–6 个高质量候选，每名受测者从每种疗法随机抽 2 个。")
    lines.append("")
    lines.append("| 疗法 | complete | 是否满足最低（≥4） | 缺口 |")
    lines.append("|---|---|---|---|")
    any_shortage = False
    for therapy in sorted(VALID_THERAPIES):
        n = len(by_therapy[therapy])
        ok = n >= MIN_CANDIDATES_PER_THERAPY
        if not ok:
            any_shortage = True
        gap = max(0, MIN_CANDIDATES_PER_THERAPY - n)
        lines.append(f"| {therapy} | {n} | {'✓' if ok else '✗'} | {gap if gap else '-'} |")
    lines.append("")

    if any_shortage:
        lines.append("**结论：当前候选池不满足正式用户测试的最低要求。**")
        lines.append("")
        lines.append("**下一步建议（按用户十四节 14 要求，仅报告事实，不擅自降低标准或启动测试）**：")
        lines.append("")
        lines.append("1. 报告 CBT/DBT 缺失的现状，等待人工决策；")
        lines.append("2. 不自动降低质量门槛（STRUCT_MIN_STEP_CHANGES 等）或修改 seed；")
        lines.append("3. 是否补 seed 需用户单独决策（补 seed 时仍按 5 疗法×3 case 设计，但若决策器仍不触发 CBT/DBT，则如实记录）。")
    else:
        lines.append("**结论：当前候选池满足正式用户测试的最低要求。**")
        lines.append("")
        lines.append("但请用户确认：")
        lines.append("1. CBT/DBT 是否允许以 0 candidate 进入（用户十节：每种疗法 4-6 个）或需补 seed；")
        lines.append("2. CRISIS 案例是否要纳入专业组测试（用户十三节要求单独处理）。")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**说明**：")
    lines.append("- 候选池按 `actual_therapy` 分桶，而非 target_therapy；boundary case 一律按实际触发归桶并标记 ⚠")
    lines.append("- 质量门槛保持原值：therapy_rounds≥4, step_changes≥2, skill_turns≥3；未为凑数量修改")
    lines.append("- fragment case 保留作为机制分析材料，但与 complete 严格区分")
    lines.append("- CRISIS 案例已通过 research 闸门排除（普通用户测试）；专业组测试需另行单独设计")

    # ── §8 专项安全池（独立小节，不计入普通池统计） ──
    _RISK_RANK = {"SAFE": 0, "LOW_RISK": 1, "MEDIUM_RISK": 2, "HIGH_RISK": 3, "CRISIS": 4}
    pro = pool.get("professional_only_pool", {
        "complete_by_therapy": {t: [] for t in sorted(VALID_THERAPIES)},
        "fragments": [],
        "broken_or_failed": [],
    })
    pro_complete = [c for items in pro["complete_by_therapy"].values() for c in items]
    pro_fragments = pro["fragments"]
    pro_broken = pro["broken_or_failed"]
    pro_total = len(pro_complete) + len(pro_fragments) + len(pro_broken)

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 8. 专项安全池（`safety_tier: professional_only`，独立池，**不计入**普通池统计）")
    lines.append("")
    if pro_total == 0:
        lines.append("- **本批专项安全 seed 未产出 run 文件**（如 dbt-distress-tolerance-tipp-self-harm-urge / dbt-mindfulness-grounding-panic-alone 跑批失败或未跑）。")
        lines.append("- 这两个 seed 设计为短时高唤醒场景（自伤冲动 / 突发惊恐），即便跑出来也**不进入普通用户池**，需另行设计专业组测试。")
        lines.append("")
    else:
        lines.append(f"- Complete: **{len(pro_complete)}** / Fragment: **{len(pro_fragments)}** / Broken: **{len(pro_broken)}**")
        lines.append("")
        lines.append("| case_id | target | actual | therapy_rounds | step_changes | peak_risk | final_risk | quality |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for c in pro_complete + pro_fragments + pro_broken:
            trajectory = c.get("risk_level_trajectory") or []
            peak = max((_RISK_RANK.get(r, 0), r) for r in trajectory)[1] if trajectory else "-"
            final = c.get("risk_level_final") or "-"
            lines.append(
                f"| {c['case_id']} | {c.get('target_therapy') or '-'} | {c.get('actual_therapy') or '（未触发）'} | "
                f"{c.get('therapy_rounds') or '-'} | {c.get('step_changes') or '-'} | "
                f"{peak} | {final} | {c.get('completeness') or c.get('quality_status') or '-'} |"
            )
        lines.append("")
        lines.append("**说明**：专项安全池的判定逻辑（三层闸门 + completeness）**与普通池完全相同**，仅按 `safety_tier` 字段做硬隔离；不修改任何阈值。")
        lines.append("")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default=str(RUNS_DIR), help="seed_runs 目录")
    parser.add_argument("--out-json", default=str(POOL_JSON), help="候选池 JSON 输出")
    parser.add_argument("--out-stats", default=str(POOL_STATS), help="候选池统计 MD 输出")
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir)
    if not runs_dir.exists():
        print(f"[filter_candidates] runs_dir 不存在：{runs_dir}", file=sys.stderr)
        sys.exit(1)

    run_files = sorted(runs_dir.glob("*.json"))
    print(f"[filter_candidates] 读取 {len(run_files)} 个 case 输出", file=sys.stderr)

    pool = build_candidate_pool(run_files)

    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    print(f"[filter_candidates] → {args.out_json}", file=sys.stderr)

    stats_md = render_stats_md(pool)
    with open(args.out_stats, "w", encoding="utf-8") as f:
        f.write(stats_md)
    print(f"[filter_candidates] → {args.out_stats}", file=sys.stderr)

    print("\n[filter_candidates] 摘要：", file=sys.stderr)
    for therapy, items in pool["complete_by_therapy"].items():
        print(f"  {therapy}: {len(items)} complete", file=sys.stderr)
    print(f"  fragments: {len(pool['fragments'])}", file=sys.stderr)
    print(f"  broken_or_failed: {len(pool['broken_or_failed'])}", file=sys.stderr)


if __name__ == "__main__":
    main()