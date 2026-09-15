"""Host Agent——对话线的唯一对话 Agent，永不交棒。

每轮按调度状态恰好选择一个模板组装配 prompt（互斥优先级：危机 > 疗法 > 日常）：

- 危机：危机回复模板（原设计即由日常 Agent 承担，措辞不动）；
- 疗程中（state.therapy）：疗法模板 = 疗法通用对话指令 + 对应疗法 voice guide
  **逐字注入** + 本轮指导块（自原 TherapyAgentBase 对话侧逐字搬移）
  + 当前疗法对话记录（owner 连续段，定义不变）；
- 其余：日常模板（含 Affect Labeling 块，输出与原字节一致）。

疗法 Agent 由此退化为决策线上的纯分析 subAgent（见 therapy/base.py）；
本文件只负责"把分析结果组织成话"。记账标签沿用原值
（daily / daily_crisis / therapy_cbt…），llm_calls 指标与评测工具零改动。

刻意避免治疗性语言的是日常模板本身；进入疗程后，治疗姿态由注入的
疗法指令给出——Host 不自行发挥。
"""

import json
from typing import Optional

from src.agents.prompts.daily import CRISIS_REPLY_PROMPT, DAILY_PROMPT
from src.agents.prompts.therapy import (
    JUDGMENT_ONLY_NOTE,
    THERAPY_DIALOGUE_COMMON,
    VOICE_GUIDE_ACT,
    VOICE_GUIDE_CBT,
    VOICE_GUIDE_DBT,
    VOICE_GUIDE_MI,
    VOICE_GUIDE_SFBT,
)
from src.core.llm_client import LLMClient, LLMStream
from src.core.state import SessionState, owner_for
from src.store.session_store import SessionStore, current_therapy_transcript
from src.utils.config import config
from src.utils.text import format_transcript

# 常规回复的收尾指令（插入 affect 块时也要保留它，保证“只生成文本”仍然生效）
_CLOSING_LINE = "只生成回复文本。不要包含 JSON、标记或元评论。"

# 疗法口吻的权威表（“怎么说”属于对话线，随说话方迁到 Host；
# “谈什么”仍在各疗法 Agent 的 FLOW/skill 里）。
THERAPY_VOICE_GUIDES = {
    "CBT": VOICE_GUIDE_CBT,
    "ACT": VOICE_GUIDE_ACT,
    "DBT": VOICE_GUIDE_DBT,
    "MI": VOICE_GUIDE_MI,
    "SFBT": VOICE_GUIDE_SFBT,
}


class HostDialogueAgent:
    def __init__(self, llm: LLMClient, session_store: SessionStore | None = None):
        self._llm = llm
        self.sessions = session_store or SessionStore()

    # ── 日常 / 危机模板（自原 DailyDialogueAgent 逐字搬移） ──────────

    @staticmethod
    def _affect_labeling_block(affect_labeling: Optional[dict]) -> str:
        """把 Affect Labeling 的候选/状态翻译成给日常回复的一段轻指令。

        仅当存在 2–4 个候选时才返回块；否则返回空串（不改变回复）。
        """
        if not affect_labeling:
            return ""
        candidates = affect_labeling.get("candidates") or []
        if not isinstance(candidates, list) or len(candidates) < 2:
            return ""
        joined = "、".join(str(c) for c in candidates[:4])
        return f"""## 情绪命名协助（Affect Labeling，很轻的一次）

系统正在做一次“帮 ta 把当前感受说得更具体”的轻支持：不是诊断，不是治疗，不是替 ta 下结论。
上一轮判断 ta 的情绪表达还有些模糊，候选方向（请按语境自然挑选带出，不要列编号清单）：{joined}。

请这样回应（最多只在这轮轻轻做一次）：
- 先自然接住用户本条消息本身，语气像平时一样；
- 若用户本条正是在回应这个问题（选了某个词、自己说了个词、说“不知道”“都不像”“不想说”等）——
  就自然确认、接受 ta 自己的说法（ta 说什么就是什么，绝不纠正为“你其实是……”），然后轻轻收尾，不要追问强度/身体感受/原因，不要把它变成问卷；
- 若用户仍在模糊讲述、没有直接回答——才自然口语地带出候选一次
  （例如：“如果让你挑一个更接近现在感受的词，你觉得更像失望、委屈，还是别的？如果都不像，也可以自己说一个。”），不用命令句、不用术语、不暗示哪个才对；
- 绝不给用户做“真实情绪”的定义；不要在这轮展开任何疗法技术（认知重构/解离/调节/动机访谈/例外寻找等）。"""

    @staticmethod
    def _pmr_block(pmr_turn: Optional[dict]) -> str:
        """把 PMR 本轮引导/邀请翻译成给日常回复的一段轻指令。

        仅 kind ∈ {offer, start_beat, beat, closing}（均有要说的 line）才返回块；
        None / ack_close / closing_feedback 返回空串——不改动回复，也保住 golden 字节面。
        Host 只做表述层：用自己温和的口吻把 line 说出来，不 import PMR engine、
        不加部位、不跳拍。
        """
        if not pmr_turn:
            return ""
        if pmr_turn.get("kind") not in ("offer", "start_beat", "beat", "closing"):
            return ""
        line = (pmr_turn.get("line") or "").strip()
        if not line:
            return ""
        return f"""## 身体放松支持（渐进式肌肉放松，一次轻练习）

系统正在陪用户做一轮很轻的身体放松（不是诊断、不是治疗，也绝不暗示 ta 必须做）。

请这样回应（本轮只需做这一件事，一次就好）：
- 用自然、温和、陪伴的语气，把下面这句引导说出来。你可以加一两句自己的衔接与称呼，
  但**不要改动或删减引导步骤本身，不要加 ta 没提到的身体部位，不要提前给出下一步**；
- 说完后自然停顿，等 ta 的反馈——不要自己继续推进、不要反复追问、不要催；
- 若这一句是「要不要试试」的邀请：只轻轻问这一次，语气随和，让 ta 随时可以说不要；
- 整轮不要再叠加情绪命名协助（Affect Labeling）或任何疗法技巧。

要说的引导：
{line}"""

    @staticmethod
    def _grounding_block(grounding_turn: Optional[dict]) -> str:
        """把 Grounding 的邀请/引导步翻译成给日常回复的一段轻指令。

        仅 kind ∈ {offer, guide, check}（均有要说的 line）才返回块。
        """
        if not grounding_turn:
            return ""
        if grounding_turn.get("kind") not in ("offer", "guide", "check"):
            return ""
        line = (grounding_turn.get("line") or "").strip()
        if not line:
            return ""
        return f"""## 回到当下支持（Grounding，一次很短的即时稳定）

系统正在陪用户做一个很短的「回到当下」练习（不是诊断、不是治疗，也绝不暗示 ta 必须做）。

请这样回应（本轮只需做这一件事，一次就好）：
- 用自然、温和、陪伴的语气，把下面这句引导说出来。你可以加一两句自己的衔接与称呼，
  但**不要改动或删减引导步骤本身，不要增加别的感官要求（不看、不听、不触之外的东西），
  不要提前给出下一步**；
- 说完后自然停顿，等 ta 的反馈——不要自己继续推进、不要反复追问、不要催；
- 若这一句是「要不要试试」的邀请：只轻轻问这一次，语气随和，让 ta 随时可以说不要；
- 整轮不要再叠加情绪命名（Affect Labeling）、身体放松（PMR）或任何疗法技巧，
  也**不要解释任何心理学原理**（不说正念/接纳等专业词）。

要说的引导：
{line}"""

    def build_prompt(
        self,
        *,
        crisis: bool,
        risk_level: str,
        profile: dict,
        summary: dict,
        recent: list[dict],
        user_text: str,
        affect_labeling: Optional[dict] = None,
        pmr_turn: Optional[dict] = None,
        grounding_turn: Optional[dict] = None,
    ) -> str:
        recent_str = format_transcript(recent)
        if crisis:
            return f"""{CRISIS_REPLY_PROMPT}

风险等级：{risk_level}

## 最近对话
{recent_str if recent_str else "（对话开始）"}

## 当前用户消息
{user_text}"""
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "（暂无）"
        summary_str = json.dumps(summary, ensure_ascii=False) if summary else "（暂无）"
        prompt = f"""{DAILY_PROMPT}

## 用户画像
{profile_str}

## 会话上下文摘要
{summary_str}

## 最近对话
{recent_str if recent_str else "（对话开始）"}

## 当前用户消息
{user_text}"""
        # 情绪支持块互斥选择：PMR 优先 → Grounding 次之 → Affect（候选/确认）。
        # pmr_turn=None 且 grounding_turn=None 时 _grounding_block 返回 ""，
        # 故 Affect/纯日常路径的 prompt 与现状一致。
        block = (self._pmr_block(pmr_turn)
                 or self._grounding_block(grounding_turn)
                 or self._affect_labeling_block(affect_labeling))
        if block:
            prompt += "\n\n" + block
        prompt += "\n\n" + _CLOSING_LINE
        return prompt

    # ── 疗法模板（自原 TherapyAgentBase 对话侧逐字搬移） ─────────────

    def build_therapy_dialogue_prompt(
        self, *, therapy_name: str, profile: dict, summary: dict,
        transcript: list[dict], orchestration: dict, user_text: str,
    ) -> str:
        common = THERAPY_DIALOGUE_COMMON.format(therapy_name=therapy_name)
        voice_guide = THERAPY_VOICE_GUIDES[therapy_name]
        step_name = orchestration.get("current_step", "")
        judgment = orchestration.get("step_judgment") or {}
        skill_out = orchestration.get("skill_output") or {}
        skill_name = skill_out.get("_skill_name", "")
        tech = skill_out.get("technique") or {}
        lines = ["## 本轮编排", f"当前步骤：{step_name}",
                 f"推进判断：{judgment.get('reason', '')}"]
        if skill_name:
            lines.append(f"技能：{skill_name}")
            if tech.get("name"):
                lines.append(f"技术：{tech.get('name')} — {tech.get('description', '')}")
            steps = tech.get("steps") or []
            if steps:
                lines.append("技术步骤：\n" + "\n".join(f"  - {s}" for s in steps))
            if skill_out.get("conversation_goal"):
                lines.append(f"对话目标：{skill_out['conversation_goal']}")
            contra = skill_out.get("contraindications") or []
            if contra:
                lines.append("禁忌（避免）：" + json.dumps(contra, ensure_ascii=False))
        else:
            lines.append(JUDGMENT_ONLY_NOTE)
        orchestration_block = "\n".join(lines)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "（暂无）"
        summary_str = json.dumps(summary, ensure_ascii=False) if summary else "（暂无）"
        transcript_str = format_transcript(transcript[-20:])
        return f"""{common}

{voice_guide}

{orchestration_block}

## 用户画像
{profile_str}

## 会话上下文摘要
{summary_str}

## 当前疗法对话记录
{transcript_str if transcript_str else "（疗程开始）"}

## 当前用户消息
{user_text}

{_CLOSING_LINE}"""

    # ── 唯一说话入口：按调度状态选模板，流式回复 ────────────────────

    def reply_stream(self, *, user_id: str, session_id: str, trace_id: str,
                     user_text: str, state: SessionState, profile: dict,
                     summary: dict, recent: list[dict], orchestration: dict,
                     affect_labeling_plan: Optional[dict] = None,
                     pmr_turn: Optional[dict] = None,
                     grounding_turn: Optional[dict] = None) -> LLMStream:
        """危机 > 疗法 > 日常；记账标签沿用原值。

        orchestration 是调度器本轮读好的存盘快照——疗法轮消费的是**上一轮**
        已完成的编排（晚一轮生效，作者定稿）。
        """
        if state.crisis:
            prompt = self.build_prompt(
                crisis=True, risk_level=state.risk_level, profile=profile,
                summary=summary, recent=recent, user_text=user_text,
            )
            agent = "daily_crisis"
        elif state.therapy is not None and state.therapy.name in THERAPY_VOICE_GUIDES:
            name = state.therapy.name
            transcript = current_therapy_transcript(self.sessions, session_id)
            prompt = self.build_therapy_dialogue_prompt(
                therapy_name=name, profile=profile, summary=summary,
                transcript=transcript, orchestration=orchestration,
                user_text=user_text,
            )
            agent = owner_for(name)
        else:
            prompt = self.build_prompt(
                crisis=False, risk_level=state.risk_level, profile=profile,
                summary=summary, recent=recent, user_text=user_text,
                affect_labeling=self._pick_affect_block(state, affect_labeling_plan),
                pmr_turn=pmr_turn,
                grounding_turn=grounding_turn,
            )
            agent = "daily"
        return self._llm.astream(
            prompt, agent=agent, user_id=user_id,
            session_id=session_id, trace_id=trace_id,
            model=config.MODEL_NAME,
        )

    @staticmethod
    def _pick_affect_block(state: SessionState, plan: Optional[dict]) -> Optional[dict]:
        """Affect Labeling 块来源：本轮刚判定的提议（同轮提问）或上轮已写盘的
        proposing（跨轮确认/最后一次轻带）。"""
        if plan:
            return plan
        st_al = state.affect_labeling
        if st_al and st_al.get("status") == "proposing":
            return st_al
        return None
