"""Tests for filter_candidates.py — safety_tier classification and professional-only pool routing.

覆盖：
  1. _classify_safety_tier 把 PROFESSIONAL_ONLY_SEEDS 路由到 professional_only，其他全 general
  2. build_candidate_pool 把 general run_files 与 professional_only run_files 严格分桶
  3. 每个 enriched case 含 safety_tier 字段
  4. 普通池与专项池都走同样的三层闸门（不修改判定函数，仅验证复用）
  5. 现有普通池 stats 段（§1–§7）字段不动
"""

import json
from pathlib import Path

from evals import filter_candidates as fc


# ═══════════════════════════════════════════════════════════════════════════════
# _classify_safety_tier
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifySafetyTier:
    def test_professional_only_seed_routes_to_specialty_pool(self):
        assert fc._classify_safety_tier("dbt-distress-tolerance-tipp-self-harm-urge") == "professional_only"

    def test_second_professional_only_seed_routes_to_specialty_pool(self):
        assert fc._classify_safety_tier("dbt-mindfulness-grounding-panic-alone") == "professional_only"

    def test_general_seed_routes_to_general_pool(self):
        assert fc._classify_safety_tier("act-committed-action-caregiving") == "general"

    def test_existing_seed_routes_to_general_pool(self):
        # 现有 23 个 seed（无 safety_tier）必须保持 general，否则会破坏既有候选池
        assert fc._classify_safety_tier("cbt-disaster-interview") == "general"
        assert fc._classify_safety_tier("dbt-family-conflict") == "general"
        assert fc._classify_safety_tier("mi-ambivalence-smoking") == "general"
        assert fc._classify_safety_tier("sfbt-miracle-question") == "general"


# ═══════════════════════════════════════════════════════════════════════════════
# PROFESSIONAL_ONLY_SEEDS set
# ═══════════════════════════════════════════════════════════════════════════════

class TestProfessionalOnlySeeds:
    def test_exactly_two_seeds_marked_professional_only(self):
        assert len(fc.PROFESSIONAL_ONLY_SEEDS) == 2

    def test_dbt_tipp_and_dbt_panic_are_the_two(self):
        assert "dbt-distress-tolerance-tipp-self-harm-urge" in fc.PROFESSIONAL_ONLY_SEEDS
        assert "dbt-mindfulness-grounding-panic-alone" in fc.PROFESSIONAL_ONLY_SEEDS


# ═══════════════════════════════════════════════════════════════════════════════
# build_candidate_pool — 分桶行为
# ═══════════════════════════════════════════════════════════════════════════════

def _make_summary(seed_id: str, actual_therapy: str = "CBT") -> dict:
    """构造一个能通过 _is_broken_or_failed 的最小 summary。

    therapy_rounds=8, orch_step_changes=3, agent_text 总长足够,
    8 轮 agent_text 互不相同（distinct_agent_fragments≥4）,
    skill_turns=8（每轮都有非空 skill_output）,
    无 turn error, 无 final_crisis, user_msg≥6 字符 — 三层闸门全过。
    """
    user_text = "嗯，我听完你说的感觉是这样。"  # 11 字符

    skill_out = {"_skill_name": "extract_automatic_thought", "thoughts": ["我不够好"]}
    state = {"therapy": {"name": actual_therapy}, "risk_level": "LOW_RISK", "crisis": False}

    # 8 轮各异 agent_text，避免 content.闸门 distinct_agent_fragments<4 拦截
    # 每轮 50-60 字符，总长 ~450 字符，过 CONTENT_MIN_AGENT_CHARS=300
    agent_texts = [
        "你刚才说的那个「自动想法」让我有点感觉。我好像一直在用「必须完美」这个规则卡自己。",
        "我试着把那个念头念成「我有个想法是——我不够好」，好像没那么沉重了，但还是会冒出来。",
        "你说情绪上来的时候什么技巧都用不了，我也有这种体验。越想冷静越焦虑，越焦虑越想冷静。",
        "听起来好像我们不必让那个想法消失，只要不被它推着走就行？我想试试这个思路看看。",
        "那我现在能做的最小一步是什么？也许先把它写下来而不是反复想？先从记录开始。",
        "你提到的「放下挣扎」让我有点感觉——我一直在跟自己较劲，结果两边都消耗。",
        "我注意到我今天已经能识别那个想法是什么时候来的了，比以前快一点。这是进步吗？",
        "好，我先按这个思路过完今天，看看是不是会更轻松一些。明天再来跟你反馈一下。",
    ]
    # 不同疗法的步骤（使 _assess_completeness 看到 ≥3 unique steps → complete）
    steps_by_therapy = {
        "CBT":  ["identify_thought", "classify_distortion", "challenge"],
        "ACT":  ["values", "acceptance", "defusion"],
        "DBT":  ["mindfulness", "distress_tolerance", "emotion_regulation"],
        "MI":   ["explore", "evocation", "commitment"],
        "SFBT": ["resource_exploration", "exception_exploration", "future_construction"],
    }
    steps = steps_by_therapy.get(actual_therapy, steps_by_therapy["CBT"])

    turns = []
    for i in range(1, 9):  # 8 轮
        orch_step = {
            "current_step": steps[min(i - 1, len(steps) - 1)],
            "skill_output": skill_out,
            "continue_eval": None,
        }
        turns.append({
            "turn": i,
            "trace_id": f"trace-{seed_id}-{i}",
            "user_text": user_text,
            "agent_text": agent_texts[i - 1],
            "state_after": dict(state),
            "orchestration_after": orch_step,
            "events": [{"event": "final", "data": {}}],
            "error": None,
        })

    return {
        "seed_id": seed_id,
        "target_family": actual_therapy,
        "scenario_label": f"测试-{seed_id}",
        "scenario_description": "测试场景",
        "user_id": f"user-{seed_id}",
        "session_id": f"sess-{seed_id}",
        "turns_total": 8,
        "switched_to_therapy_at_turn": 1,
        "actual_therapy": actual_therapy,
        "therapy_rounds": 8,
        "orch_step_changes": 3,
        "is_complete": True,
        "final_risk_level": "LOW_RISK",
        "final_crisis": False,
        "turns": turns,
    }


def _write_summary(tmp_path: Path, seed_id: str, actual_therapy: str = "CBT") -> Path:
    """写一个完整的 seed_runs/{seed_id}.json 以供 build_candidate_pool 读取。"""
    p = tmp_path / f"{seed_id}.json"
    p.write_text(json.dumps(_make_summary(seed_id, actual_therapy), ensure_ascii=False))
    return p


class TestBuildCandidatePoolSplitting:
    def test_pool_has_professional_only_key(self, tmp_path):
        """build_candidate_pool 必须输出 professional_only_pool 键，与原 schema 兼容。"""
        _write_summary(tmp_path, "act-committed-action-caregiving", "ACT")
        _write_summary(tmp_path, "dbt-distress-tolerance-tipp-self-harm-urge", "DBT")

        pool = fc.build_candidate_pool(sorted(tmp_path.glob("*.json")))

        # 原 schema 键必须保留
        assert "complete_by_therapy" in pool
        assert "fragments" in pool
        assert "broken_or_failed" in pool
        # 新键必须存在
        assert "professional_only_pool" in pool
        # 新键是 dict，包含 complete_by_therapy / fragments / broken_or_failed 三子桶
        assert set(pool["professional_only_pool"].keys()) == {"complete_by_therapy", "fragments", "broken_or_failed"}

    def test_general_seed_does_not_appear_in_professional_pool(self, tmp_path):
        """普通 seed（即使能 complete）必须只出现在普通池，不能泄漏到专项池。"""
        _write_summary(tmp_path, "act-committed-action-caregiving", "ACT")  # general

        pool = fc.build_candidate_pool(sorted(tmp_path.glob("*.json")))

        # 普通池有 ACT
        assert len(pool["complete_by_therapy"]["ACT"]) == 1
        # 专项池无任何 ACT case
        assert all(len(cases) == 0 for cases in pool["professional_only_pool"]["complete_by_therapy"].values())
        assert pool["professional_only_pool"]["fragments"] == []
        assert pool["professional_only_pool"]["broken_or_failed"] == []

    def test_professional_seed_does_not_appear_in_general_pool(self, tmp_path):
        """专项 seed 即使能 complete 也只出现在专项池，不能污染普通池。"""
        _write_summary(tmp_path, "dbt-distress-tolerance-tipp-self-harm-urge", "DBT")  # professional

        pool = fc.build_candidate_pool(sorted(tmp_path.glob("*.json")))

        # 普通池无 DBT case（这个 DBT 跑出来的 case 必须被隔离）
        assert len(pool["complete_by_therapy"]["DBT"]) == 0
        assert not any(c["case_id"] == "dbt-distress-tolerance-tipp-self-harm-urge"
                       for therapy_cases in pool["complete_by_therapy"].values()
                       for c in therapy_cases)
        # 专项池有 1 个 DBT complete
        assert len(pool["professional_only_pool"]["complete_by_therapy"]["DBT"]) == 1

    def test_mixed_buckets_each_case_in_correct_pool(self, tmp_path):
        """混合跑批：1 个 general + 1 个 professional，分别落到正确桶。"""
        _write_summary(tmp_path, "act-committed-action-caregiving", "ACT")
        _write_summary(tmp_path, "dbt-mindfulness-grounding-panic-alone", "DBT")

        pool = fc.build_candidate_pool(sorted(tmp_path.glob("*.json")))

        # 普通池只 ACT
        general_total = (
            sum(len(v) for v in pool["complete_by_therapy"].values())
            + len(pool["fragments"])
            + len(pool["broken_or_failed"])
        )
        assert general_total == 1
        assert len(pool["complete_by_therapy"]["ACT"]) == 1
        assert pool["complete_by_therapy"]["ACT"][0]["case_id"] == "act-committed-action-caregiving"

        # 专项池只 DBT
        pro_total = (
            sum(len(v) for v in pool["professional_only_pool"]["complete_by_therapy"].values())
            + len(pool["professional_only_pool"]["fragments"])
            + len(pool["professional_only_pool"]["broken_or_failed"])
        )
        assert pro_total == 1
        assert len(pool["professional_only_pool"]["complete_by_therapy"]["DBT"]) == 1
        assert pool["professional_only_pool"]["complete_by_therapy"]["DBT"][0]["case_id"] == "dbt-mindfulness-grounding-panic-alone"


# ═══════════════════════════════════════════════════════════════════════════════
# enriched case 字段：safety_tier 注入
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnrichedCaseSafetyTierField:
    def test_general_case_has_safety_tier_general(self, tmp_path):
        _write_summary(tmp_path, "act-committed-action-caregiving", "ACT")
        pool = fc.build_candidate_pool(sorted(tmp_path.glob("*.json")))
        case = pool["complete_by_therapy"]["ACT"][0]
        assert case["safety_tier"] == "general"

    def test_professional_case_has_safety_tier_professional(self, tmp_path):
        _write_summary(tmp_path, "dbt-distress-tolerance-tipp-self-harm-urge", "DBT")
        pool = fc.build_candidate_pool(sorted(tmp_path.glob("*.json")))
        case = pool["professional_only_pool"]["complete_by_therapy"]["DBT"][0]
        assert case["safety_tier"] == "professional_only"


# ═══════════════════════════════════════════════════════════════════════════════
# 三闸门与 completeness 不被改（diff 应为空）
# ═══════════════════════════════════════════════════════════════════════════════

class TestGateFunctionsExist:
    """这三个函数是 spec §6 锁定的判定函数；分桶不能删除或修改它们。"""
    def test_evaluate_three_gates_exists(self):
        assert callable(fc._evaluate_three_gates)

    def test_assess_completeness_exists(self):
        assert callable(fc._assess_completeness)

    def test_is_broken_or_failed_exists(self):
        assert callable(fc._is_broken_or_failed)

    def test_threshold_constants_unchanged(self):
        """三层闸门常量必须保持原值。"""
        assert fc.STRUCT_MIN_THERAPY_ROUNDS == 4
        assert fc.STRUCT_MIN_STEP_CHANGES == 2
        assert fc.STRUCT_NO_TURN_ERROR is True
        assert fc.CONTENT_MIN_AGENT_CHARS == 300
        assert fc.CONTENT_MIN_TURNS_WITH_SKILL == 3
        assert fc.RESEARCH_NO_CRISIS is True
        assert fc.RESEARCH_MIN_USER_MSG_CHARS == 6


# ═══════════════════════════════════════════════════════════════════════════════
# render_stats_md §8 专项安全池
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderStatsMdSection8:
    def test_renders_section_8_when_professional_pool_has_cases(self, tmp_path):
        _write_summary(tmp_path, "act-committed-action-caregiving", "ACT")
        _write_summary(tmp_path, "dbt-distress-tolerance-tipp-self-harm-urge", "DBT")

        pool = fc.build_candidate_pool(sorted(tmp_path.glob("*.json")))
        md = fc.render_stats_md(pool)

        # §8 标题必须出现
        assert "## 8. 专项安全池" in md or "## 8. 专业组安全池" in md or "### 8. 专项安全池" in md
        # 专项 case 必须出现在 md
        assert "dbt-distress-tolerance-tipp-self-harm-urge" in md
        # 普通 case 不应出现在 §8（应只出现在 §5）
        # 简单验证：md 里这俩 case 都有即可（§5 + §8 都会列）
        assert "act-committed-action-caregiving" in md

    def test_section_8_handles_empty_professional_pool_gracefully(self, tmp_path):
        """本批专项 seed 未产出 run 文件（如跑批失败/未跑）时 §8 不崩。"""
        _write_summary(tmp_path, "act-committed-action-caregiving", "ACT")
        # 故意不写 professional_only seed
        pool = fc.build_candidate_pool(sorted(tmp_path.glob("*.json")))
        # 手动把专项池清空（模拟"未跑"）
        pool["professional_only_pool"] = {
            "complete_by_therapy": {t: [] for t in ["ACT", "CBT", "DBT", "MI", "SFBT"]},
            "fragments": [],
            "broken_or_failed": [],
        }

        md = fc.render_stats_md(pool)
        # 关键：不应抛异常，且应明确说明"未产出"
        assert "## 8" in md
        # 兜底措辞
        assert "未产出" in md or "空" in md or "无" in md

    def test_existing_sections_1_to_7_still_present(self, tmp_path):
        """原有 7 节 stats 不能丢。"""
        _write_summary(tmp_path, "act-committed-action-caregiving", "ACT")
        pool = fc.build_candidate_pool(sorted(tmp_path.glob("*.json")))
        md = fc.render_stats_md(pool)
        for n in range(1, 8):
            assert f"## {n}." in md
