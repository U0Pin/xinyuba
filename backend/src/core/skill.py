import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SkillType(str, Enum):
    ANALYSIS = "analysis"
    INTERVENTION = "intervention"
    EMOTION_SUPPORT = "emotion_support"
    MEMORY = "memory"
    SAFETY = "safety"
    META = "meta"


@dataclass
class SkillResult:
    """Structured output from a skill execution."""
    skill_name: str
    output: dict
    state_effects: dict = field(default_factory=dict)
    error: Optional[str] = None
    success: bool = True


class Skill(ABC):
    """Base class for all CBT skills.

    Skills are pure functions on structured state. They take structured inputs
    and produce structured outputs. They are stateless — state is held by the
    Agent orchestration layer.

    安全门禁不在本层：新架构中风险/危机短路由决策线与 Affect 引擎的 gate 执行
    （见 ARCHITECTURE.md「三条线职责」），skill 层不做档位判断。
    """

    name: str = ""
    skill_type: SkillType = SkillType.ANALYSIS
    description: str = ""

    @abstractmethod
    def execute(self, inputs: dict, context: dict) -> SkillResult:
        """Execute the skill with given inputs and context.

        Args:
            inputs: Skill-specific required inputs.
            context: Broader session context (state, profile, etc.).

        Returns:
            SkillResult with structured output and state effects.
        """
        ...

    async def aexecute(self, inputs: dict, context: dict) -> SkillResult:
        """Default async wrapper around execute().

        Subclasses that need to make LLM calls (or other async I/O) should
        override this with an async implementation that uses `await`. Skills
        whose execute() is purely synchronous (e.g. rule-based) inherit this
        default which runs execute() in a thread.
        """
        return await asyncio.to_thread(self.execute, inputs, context)

    def __repr__(self):
        return f"Skill({self.name}, type={self.skill_type.value})"


class SkillRegistry:
    """Registry for all CBT skills. Skills are looked up by name by the orchestration layer."""

    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        return list(self._skills.values())


skill_registry = SkillRegistry()


# ═══════════════════════════════════════════════════════════════════════════════
# Shared LLM-call helpers (used by LLMSkill and any module needing JSON from LLM)
# ═══════════════════════════════════════════════════════════════════════════════

def llm_json(llm, prompt: str) -> dict:
    """Call LLM synchronously, return parsed JSON dict.

    Returns {} when the response is not valid JSON. Other exceptions
    (e.g. connection errors) propagate to the caller — the async pipeline
    path uses llm_json_async, which swallows everything instead.
    """
    try:
        result = llm.invoke(prompt)
        return json.loads(result.content)
    except json.JSONDecodeError:
        return {}


async def llm_json_async(llm, prompt: str) -> dict:
    """Call LLM asynchronously, return parsed JSON dict ({} on any failure).

    This is the version used in the async pipeline. The sync variant is kept
    for callers that still use execute() directly (e.g. unit tests).
    """
    try:
        result = await llm.ainvoke(prompt)
        content = result.content if result else ""
        if not content:
            return {}
        return json.loads(content)
    except Exception:
        return {}


def format_history(history: list[dict]) -> str:
    """Format conversation history entries for LLM prompts."""
    if not history:
        return "(start of conversation)"
    lines = []
    for h in history:
        lines.append(f"User: {h.get('user', '')}")
        lines.append(f"Agent: {h.get('agent', '')}")
    return "\n".join(lines)


class LLMSkill(Skill):
    """Base class for LLM-driven skills.

    Subclasses implement three hooks; the base provides BOTH execute() and
    aexecute() as one shared flow, so sync/async behavior can never drift:

      build_prompt(inputs)        -> prompt string (exact LLM interface)
      parse_output(data, inputs)  -> SkillResult from parsed LLM JSON
      fallback(inputs)            -> SkillResult when the LLM call fails

    Default fallback() returns an error result (the behavior all
    *_state_assessment skills want); INTERVENTION skills override it with a
    safe default technique so a failed LLM call still yields usable guidance.
    """

    #: inputs key that must be non-empty for the skill to run
    user_text_key = "user_text"

    def _guard(self, inputs: dict, context: dict):
        """Return (llm, None) or (None, error SkillResult)."""
        llm = context.get("llm")
        if not llm or not inputs.get(self.user_text_key, ""):
            return None, SkillResult(
                skill_name=self.name,
                output={"error": "missing llm or user_text"},
                success=False,
            )
        return llm, None

    def fallback(self, inputs: dict) -> SkillResult:
        return SkillResult(
            skill_name=self.name,
            output={"error": "LLM call failed"},
            success=False,
        )

    def build_prompt(self, inputs: dict) -> str:
        raise NotImplementedError

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        raise NotImplementedError

    def execute(self, inputs: dict, context: dict) -> SkillResult:
        llm, error = self._guard(inputs, context)
        if error is not None:
            return error
        data = llm_json(llm, self.build_prompt(inputs))
        if not data:
            return self.fallback(inputs)
        return self.parse_output(data, inputs)

    async def aexecute(self, inputs: dict, context: dict) -> SkillResult:
        llm, error = self._guard(inputs, context)
        if error is not None:
            return error
        data = await llm_json_async(llm, self.build_prompt(inputs))
        if not data:
            return self.fallback(inputs)
        return self.parse_output(data, inputs)


def discrete_state(index: float, thresholds: list[tuple[float, str]], *, inclusive: bool = False) -> str:
    """Map a continuous index to a discrete label via ascending thresholds.

    DBT uses strict ``<`` (inclusive=False); MI/SFBT use ``<=`` (inclusive=True).
    """
    op = (lambda a, b: a <= b) if inclusive else (lambda a, b: a < b)
    for threshold, label in thresholds:
        if op(index, threshold):
            return label
    return thresholds[-1][1]


class TherapyInterventionSkill(LLMSkill):
    """Shared flow for all therapy INTERVENTION skills.

    All five families (CBT/ACT/DBT/MI/SFBT) use the same prompt envelope
    (context block + recent history + current message) and the same output
    shape (technique + conversation_goal + adaptation + contraindications).
    Subclasses supply:

    - ``prompt_template`` / ``output_schema``
    - ``default_technique`` / ``valid_techniques``
    - ``state_effects_value`` (per-family intervention log key)
    - ``interaction_style``
    - ``_context_block(inputs)``
    - ``fallback(inputs)``

    SFBT additionally overrides ``_adaptation()`` to pin a hopeful tone.
    """

    skill_type = SkillType.INTERVENTION
    state_effects_value: dict = {}

    prompt_template = ""
    output_schema = ""
    default_technique = ""
    valid_techniques: list[str] = []
    interaction_style = "exploratory"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        history_str = format_history(inputs.get("history", [])[-4:])
        context_block = self._context_block(inputs)
        return f"""{self.prompt_template}

## Context

{context_block}

Recent conversation:
{history_str}

Current user message:
{user_text}

## Output JSON
{self.output_schema}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        tech_name = data.get("technique", {}).get("name", self.default_technique)
        if tech_name not in self.valid_techniques:
            tech_name = self.default_technique

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech_name,
                    "description": data.get("technique", {}).get("description", ""),
                    "steps": data.get("technique", {}).get("steps", []),
                },
                "conversation_goal": data.get("conversation_goal", ""),
                "adaptation": self._adaptation(data),
                "contraindications": data.get("contraindications", []),
            },
            state_effects=dict(self.state_effects_value),
            success=True,
        )

    def _adaptation(self, data: dict) -> dict:
        return {
            "interaction_style": self.interaction_style,
            "tone_adjustment": data.get("adaptation", {}).get("tone_adjustment", ""),
            "pace_adjustment": data.get("adaptation", {}).get("pace_adjustment", ""),
            "culture_note": data.get("adaptation", {}).get("culture_note", ""),
        }
