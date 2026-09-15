"""疗法 Agent 基类（Phase 3）——纯分析 subAgent。

疗法 Agent 只活在决策线上（v2 沉淀稿第八节）：状态评估（每轮）、步骤推进判断
（每轮）、skill 执行（每轮），产出编排结果供**唯一对话 Agent（Host）**组织语言。
自 2026-09 起疗法 Agent 不再说话——"怎么说"归 Host（见 agents/host_agent.py），
本类只负责"谈什么、推进到哪"。

基本流程由 FLOW（主步骤序列）+
BRANCHES（条件分支步骤，如 CBT 行为激活）描述；步骤推进由每轮的 step judgment
LLM 决定（stay / advance / go_to / complete）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from src.agents.prompts.therapy import (
    STEP_JUDGMENT_OUTPUT_SCHEMA,
    STEP_JUDGMENT_PROMPT,
)
from src.core.llm_client import LLMClient
from src.core.logging_utils import app_log
from src.core.skill import skill_registry
from src.core.state import TherapyProgress
from src.store.session_store import SessionStore, current_therapy_transcript
from src.utils.text import format_transcript, history_pairs


@dataclass
class FlowStep:
    name: str
    skills: list = field(default_factory=list)  # 步骤内候选 skill（由 pick_skill 路由）
    judgment_only: bool = False                 # 纯判断步骤（无 skill，如 CBT 评估效果）


@dataclass
class BranchStep:
    name: str
    skill: str
    return_to: str


class TherapyAgentBase:
    """五疗法 Agent 共享基类。"""

    therapy_name: str = ""
    assessment_skill: Optional[str] = None
    FLOW: list[FlowStep] = []
    BRANCHES: dict[str, BranchStep] = {}

    def __init__(
        self,
        llm: LLMClient,
        session_store: SessionStore | None = None,
    ):
        self._llm = llm
        self.sessions = session_store or SessionStore()

    # ── 流程工具（进度读写全部经 TherapyProgress，见 core/state.py） ──

    def _flow_index(self, name: str) -> Optional[int]:
        for i, step in enumerate(self.FLOW):
            if step.name == name:
                return i
        return None

    def current_step(self, therapy: TherapyProgress) -> str:
        if therapy.branch:
            return therapy.branch
        return self.step_name(therapy.step_index)

    def step_name(self, index: int) -> str:
        if 0 <= index < len(self.FLOW):
            return self.FLOW[index].name
        return "unknown"

    def _step_obj(self, therapy: TherapyProgress):
        if therapy.branch:
            return self.BRANCHES.get(therapy.branch)
        idx = therapy.step_index
        return self.FLOW[idx] if 0 <= idx < len(self.FLOW) else None

    # ── 路由钩子（子类覆盖） ──────────────────────────────────────

    def initial_step_index(self, assessment: Optional[dict]) -> int:
        return 0

    def next_step_index(self, assessment: Optional[dict], current: int,
                        visited: list) -> Optional[int]:
        """advance 时进入哪个步骤；None = 流程完成。默认线性 +1。"""
        if current >= len(self.FLOW) - 1:
            return None
        return current + 1

    def pick_skill(self, *, assessment: Optional[dict], step, products: dict) -> Optional[str]:
        if step is None:
            return None
        if isinstance(step, BranchStep):
            return step.skill
        return step.skills[0] if step.skills else None

    def assessment_block(self, assessment: Optional[dict], products: dict) -> dict:
        """给干预 skill 的 assessment 子块（子类覆盖）。"""
        return {}

    def intervention_extra_inputs(self, products: dict) -> dict:
        """给干预 skill 的额外输入（如 CBT target_thought）。"""
        return {}

    def products_update(self, *, step_name: str, skill_name: str,
                        skill_output: dict, products: dict) -> dict:
        """把 skill 输出沉淀为疗程产品（子类覆盖）。"""
        return products

    # ── 决策线侧：编排 ────────────────────────────────────────────

    def _bound(self, ctx, agent: str):
        return self._llm.bind(
            agent=agent, user_id=ctx.user_id,
            session_id=ctx.session_id, trace_id=ctx.trace_id,
        )

    async def run_assessment(self, ctx) -> Optional[dict]:
        if not self.assessment_skill:
            return None
        skill = skill_registry.get(self.assessment_skill)
        if skill is None:
            app_log("warning", "therapy", "assessment_skill_missing",
                    trace_id=ctx.trace_id, skill=self.assessment_skill)
            return None
        transcript = current_therapy_transcript(self.sessions, ctx.session_id)
        prev = ctx.orchestration.assessment
        profile = ctx.profile or {}
        inputs = {
            "user_text": ctx.message,
            "history": history_pairs(transcript[-6:]),
            "emotion": {},
            "profile": {
                "attachment_style": profile.get("attachment_style"),
                "readiness": profile.get("readiness", profile.get("cognitive_readiness")),
            },
            "current_act_state": prev if self.therapy_name == "ACT" else None,
            "current_dbt_state": prev if self.therapy_name == "DBT" else None,
            "current_mi_state": prev if self.therapy_name == "MI" else None,
            "current_sfbt_state": prev if self.therapy_name == "SFBT" else None,
        }
        bound = self._bound(ctx, f"therapy_{self.therapy_name.lower()}_assessment")
        try:
            result = await skill.aexecute(inputs, {"llm": bound})
        except Exception as e:
            app_log("warning", "therapy", "assessment_call_failed",
                    trace_id=ctx.trace_id, error=str(e))
            return None
        if result.success and result.output:
            return dict(result.output)
        app_log("warning", "therapy", "assessment_failed", trace_id=ctx.trace_id,
                therapy=self.therapy_name, error=result.error)
        return None

    def build_step_judgment_prompt(self, *, step_name: str, assessment: Optional[dict],
                                   products: dict, transcript: list[dict],
                                   therapy_rounds: int) -> str:
        flow_names = [s.name for s in self.FLOW]
        branch_names = list(self.BRANCHES.keys())
        assessment_str = json.dumps(assessment, ensure_ascii=False) if assessment else "（无）"
        transcript_str = format_transcript(transcript[-6:])
        return f"""{STEP_JUDGMENT_PROMPT.format(therapy_name=self.therapy_name)}

## 流程信息
主步骤：{flow_names}
分支步骤：{branch_names or "（无）"}
当前步骤：{step_name}（疗程第 {therapy_rounds + 1} 轮）

## 最新状态评估
{assessment_str}

## 疗程产品（已沉淀的分析结果）
{json.dumps(products, ensure_ascii=False) if products else "（无）"}

## 当前疗法对话（最近部分）
{transcript_str if transcript_str else "（疗程刚开始）"}

## Output JSON
{STEP_JUDGMENT_OUTPUT_SCHEMA}"""

    def parse_step_judgment(self, data: dict) -> dict:
        action = data.get("action", "stay")
        if action not in ("stay", "advance", "go_to", "complete"):
            action = "stay"
        return {
            "action": action,
            "target_step": data.get("target_step"),
            "achieved": bool(data.get("achieved", False)),
            "reason": data.get("reason", ""),
        }

    async def run_step_judgment(self, ctx, assessment: Optional[dict],
                                products: dict, therapy: TherapyProgress) -> dict:
        step_name = self.current_step(therapy)
        prompt = self.build_step_judgment_prompt(
            step_name=step_name,
            assessment=assessment,
            products=products,
            transcript=current_therapy_transcript(self.sessions, ctx.session_id),
            therapy_rounds=therapy.rounds,
        )
        bound = self._bound(ctx, f"therapy_{self.therapy_name.lower()}_judgment")
        data = await bound.ajson(prompt, failure_log=("therapy", "judgment_failed"))
        return self.parse_step_judgment(data)

    def build_skill_inputs(self, ctx, assessment: Optional[dict], products: dict) -> dict:
        transcript = current_therapy_transcript(self.sessions, ctx.session_id)
        profile = ctx.profile or {}
        inputs = {
            "user_text": ctx.message,
            "user_utterance": ctx.message,
            "target_thought": ctx.message,
            "history": history_pairs(transcript[-4:]),
            "emotion": {},
            "profile": {
                "attachment_style": profile.get("attachment_style"),
                "readiness": profile.get("readiness", profile.get("cognitive_readiness")),
            },
            "assessment": self.assessment_block(assessment, products),
        }
        inputs.update(self.intervention_extra_inputs(products))
        return inputs

    async def run_current_skill(self, ctx, assessment: Optional[dict],
                                products: dict, therapy: TherapyProgress) -> Optional[dict]:
        step = self._step_obj(therapy)
        if step is None or (isinstance(step, FlowStep) and step.judgment_only):
            return None
        skill_name = self.pick_skill(assessment=assessment, step=step, products=products)
        if not skill_name:
            return None
        skill = skill_registry.get(skill_name)
        if skill is None:
            return None
        inputs = self.build_skill_inputs(ctx, assessment, products)
        bound = self._bound(ctx, f"therapy_{self.therapy_name.lower()}_skill")
        try:
            result = await skill.aexecute(inputs, {"llm": bound})
        except Exception as e:
            app_log("warning", "therapy", "skill_call_failed", trace_id=ctx.trace_id,
                    therapy=self.therapy_name, skill=skill_name, error=str(e))
            return None
        if not result.success or not result.output:
            app_log("warning", "therapy", "skill_failed", trace_id=ctx.trace_id,
                    therapy=self.therapy_name, skill=skill_name, error=result.error)
            return None
        out = dict(result.output)
        out["_skill_name"] = skill_name
        return out

    def apply_judgment(self, therapy: TherapyProgress, judgment: dict,
                       assessment: Optional[dict]) -> None:
        """按判断推进进度对象（就地改的是决策线的快照拷贝，轮边界才生效）。"""
        action = judgment.get("action", "stay")

        if action == "complete":
            therapy.basic_flow_complete = True
            return
        if action == "go_to":
            target = judgment.get("target_step")
            if target in self.BRANCHES:
                therapy.branch = target
                return
            t = self._flow_index(target)
            if t is not None:
                therapy.branch = None
                therapy.step_index = t
            return
        if action == "advance":
            if therapy.branch:
                b = self.BRANCHES.get(therapy.branch)
                ret = self._flow_index(b.return_to) if b else None
                therapy.branch = None
                if ret is not None:
                    therapy.step_index = ret
                return
            idx = therapy.step_index
            if 0 <= idx < len(self.FLOW) and self.FLOW[idx].name not in therapy.visited:
                therapy.visited.append(self.FLOW[idx].name)
            if idx >= len(self.FLOW) - 1:
                therapy.basic_flow_complete = True
                return
            nxt = self.next_step_index(assessment, idx, list(therapy.visited))
            if nxt is None:
                therapy.basic_flow_complete = True
                return
            therapy.step_index = min(nxt, len(self.FLOW) - 1)
            return
        # stay：不推进
