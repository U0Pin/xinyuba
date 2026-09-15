"""CBT 疗法 Agent（Phase 3）。

基本流程：
    识别自动思维 → 分类扭曲 → 挑战（按扭曲类型路由）→ 重构 → 评估效果
    →（不满意）回到挑战；（退缩/低动力时条件插入）行为激活。
"""

from src.agents.therapy.base import BranchStep, FlowStep, TherapyAgentBase


class CbtTherapyAgent(TherapyAgentBase):
    therapy_name = "CBT"
    assessment_skill = None  # CBT 无独立评估 skill，分析由步骤 1/2 承担

    FLOW = [
        FlowStep("identify_thought", skills=["extract_automatic_thought"]),
        FlowStep("classify_distortion", skills=["detect_cognitive_distortion"]),
        FlowStep("challenge", skills=["decatastrophize", "evaluate_evidence", "generate_reframe"]),
        FlowStep("reframe", skills=["generate_reframe", "evaluate_evidence"]),
        FlowStep("evaluate_effect", judgment_only=True),
    ]
    BRANCHES = {
        "behavioral_activation": BranchStep(
            "behavioral_activation", "plan_behavioral_activation", "evaluate_effect"
        )
    }

    # ── 路由 ──────────────────────────────────────────────────────

    def pick_skill(self, *, assessment, step, products):
        if isinstance(step, BranchStep):
            return step.skill
        name = step.name if step else ""
        if name == "identify_thought":
            return "extract_automatic_thought"
        if name == "classify_distortion":
            return "detect_cognitive_distortion"
        if name == "challenge":
            types = set()
            for c in products.get("distortions", []):
                for d in c.get("distortions", []):
                    types.add(d.get("type"))
            if types & {"catastrophizing", "fortune_telling"}:
                return "decatastrophize"
            if types & {"mind_reading", "personalization", "self_blame",
                        "overgeneralization", "black_white_thinking"}:
                return "generate_reframe"
            return "evaluate_evidence"
        if name == "reframe":
            return "generate_reframe"
        return None

    def assessment_block(self, assessment, products):
        return {"distortions": products.get("distortions", [])}

    def intervention_extra_inputs(self, products):
        return {
            "target_thought": products.get("primary_thought") or "",
            "automatic_thoughts": products.get("automatic_thoughts", []),
        }

    def products_update(self, *, step_name, skill_name, skill_output, products):
        # CBT products 持久化语义（避免单轮 skill 空输出覆盖已识别状态）：
        # - identify_thought：本轮空输出不覆盖已有 automatic_thoughts / primary_thought；
        #   本轮有有效输出时合并去重。
        # - classify_distortion：同上，distortions / distortion_summary 不被空输出清空。
        if step_name == "identify_thought":
            new_thoughts = skill_output.get("automatic_thoughts") or []
            if new_thoughts:
                existing = products.get("automatic_thoughts") or []
                products["automatic_thoughts"] = self._merge_thoughts(existing, new_thoughts)
            new_primary = (skill_output.get("primary_thought") or "").strip()
            if new_primary:
                products["primary_thought"] = new_primary
            # no_automatic_thought_detected：仅在没有任何累积 thoughts 时才允许为 True
            if products.get("automatic_thoughts"):
                products["no_automatic_thought_detected"] = False
            else:
                products["no_automatic_thought_detected"] = skill_output.get(
                    "no_automatic_thought_detected", False
                )
        elif step_name == "classify_distortion":
            new_classifications = skill_output.get("classifications") or []
            if new_classifications:
                existing = products.get("distortions") or []
                products["distortions"] = self._merge_classifications(existing, new_classifications)
            new_summary = skill_output.get("distortion_summary") or {}
            # distortion_summary 仅在累积 distortions 非空或本轮 summary 有内容时更新
            if new_summary.get("distortion_count", 0) > 0:
                products["distortion_summary"] = new_summary
            elif products.get("distortions") and not products.get("distortion_summary"):
                # 已有 distortions 但 summary 缺失：用已有 distortions 推导一个 fallback summary
                products["distortion_summary"] = {
                    "dominant_pattern": products["distortions"][0].get("primary_distortion") if products["distortions"] else None,
                    "distortion_count": sum(
                        len(c.get("distortions") or []) for c in products["distortions"]
                    ),
                    "severity": "moderate",
                }
        return products

    # ── 去重合并辅助（products persistence 用） ────────────────────────

    @staticmethod
    def _thought_key(t):
        """自动思维去重 key：按 thought 文本。"""
        if isinstance(t, str):
            return t.strip()
        if isinstance(t, dict):
            return (t.get("thought") or t.get("text") or str(t)).strip()
        return str(t).strip()

    @staticmethod
    def _merge_thoughts(existing, new):
        """合并两轮 automatic_thoughts，按 thought 文本去重，保留首次出现顺序。"""
        seen = {CbtTherapyAgent._thought_key(t) for t in existing}
        merged = list(existing)
        for t in new:
            k = CbtTherapyAgent._thought_key(t)
            if k and k not in seen:
                seen.add(k)
                merged.append(t)
        return merged

    @staticmethod
    def _classification_key(c):
        """扭曲分类去重 key：按 (thought 文本, distortion_type)。"""
        if not isinstance(c, dict):
            return ("", "")
        thought = (c.get("thought") or "").strip()
        types = c.get("distortions") or []
        if isinstance(types, list) and types:
            for d in types:
                if isinstance(d, dict) and d.get("type"):
                    return (thought, d.get("type"))
        return (thought, "")

    @staticmethod
    def _merge_classifications(existing, new):
        """合并两轮 classifications，按 (thought, type) 去重。"""
        seen = {CbtTherapyAgent._classification_key(c) for c in existing}
        merged = list(existing)
        for c in new:
            k = CbtTherapyAgent._classification_key(c)
            if k != ("", "") and k not in seen:
                seen.add(k)
                merged.append(c)
        return merged
