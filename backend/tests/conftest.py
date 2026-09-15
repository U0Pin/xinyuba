"""Shared test fixtures for ACT skill tests."""

import json
import pytest
from unittest.mock import MagicMock


class FakeLLMResponse:
    """Simulates an LLM response object with a .content attribute."""

    def __init__(self, content: str):
        self.content = content


@pytest.fixture
def mock_llm():
    """Returns a MagicMock that can be configured per-test via .return_value."""

    return MagicMock()


@pytest.fixture
def valid_assessment():
    """A complete, valid ACT assessment dict (as produced by ActStateAssessment)."""
    return {
        "fusion": {
            "index": 0.72,
            "state": "cf_fused",
            "confidence": 0.85,
            "evidence": ["I'm a failure"],
        },
        "avoidance": {
            "index": 0.55,
            "state": "ea_avoiding",
            "confidence": 0.70,
            "evidence": ["It's fine, I don't care"],
        },
        "openness": {
            "index": 0.30,
            "state": "eo_guarded",
            "confidence": 0.60,
            "evidence": ["Reluctant to elaborate"],
        },
        "alignment": {
            "index": 0.25,
            "state": "low",
            "confidence": 0.50,
            "values_mentioned": [],
        },
        "activation": {
            "index": 0.20,
            "state": "low",
            "confidence": 0.55,
        },
        "psychological_flexibility": 0.35,
        "flexibility_level": "pf_rigid",
        "overall_trend": "stable",
        "emotional_intensity": 0.45,
        "escalation_level": "activated",
        "primary_concern": "fusion",
        "should_intervene": True,
        "recommended_processes": [
            {"process": "defusion", "priority": 1, "rationale": "High fusion detected"},
        ],
    }


@pytest.fixture
def valid_defusion_llm_output():
    """LLM returns valid JSON for defusion."""
    return json.dumps({
        "technique": {
            "name": "thought_labeling",
            "description": "User shows high fusion with failure identity",
            "steps": ["Name the thought", "Label it as a mental event", "Create distance"],
        },
        "conversation_goal": "帮助用户看到想法只是想法",
        "adaptation": {
            "tone_adjustment": "curious",
            "pace_adjustment": "slow",
            "culture_note": "avoid confrontation",
        },
        "contraindications": [],
    })


@pytest.fixture
def valid_acceptance_llm_output():
    """LLM returns valid JSON for acceptance."""
    return json.dumps({
        "technique": {
            "name": "willingness_invitation",
            "description": "Invite user to allow emotion",
            "steps": ["Notice the feeling", "Allow it to be there"],
        },
        "conversation_goal": "帮助用户允许情绪存在",
        "adaptation": {
            "tone_adjustment": "warm",
            "pace_adjustment": "slow",
            "culture_note": "respect user's coping",
        },
        "contraindications": [
            {"condition": "high_escalation", "reason": "risk of overwhelm"},
        ],
    })


@pytest.fixture
def valid_present_moment_llm_output():
    """LLM returns valid JSON for present_moment."""
    return json.dumps({
        "technique": {
            "name": "breath_anchor",
            "description": "Guide user back to body awareness",
            "steps": ["Notice breath", "Feel contact points"],
        },
        "conversation_goal": "帮助用户回到当下身体感受",
        "adaptation": {
            "tone_adjustment": "steady",
            "pace_adjustment": "slow",
            "culture_note": "avoid meditation language",
        },
        "contraindications": [],
    })


@pytest.fixture
def valid_self_as_context_llm_output():
    """LLM returns valid JSON for self_as_context."""
    return json.dumps({
        "technique": {
            "name": "observer_metaphor",
            "description": "Use sky-and-clouds metaphor",
            "steps": ["Introduce metaphor", "Distinguish observer from observed"],
        },
        "conversation_goal": "帮助用户体验观察性自我",
        "adaptation": {
            "tone_adjustment": "gentle",
            "pace_adjustment": "very_slow",
            "culture_note": "use natural imagery",
        },
        "contraindications": [
            {"condition": "defended", "reason": "abstract metaphor won't land"},
        ],
    })


@pytest.fixture
def valid_values_llm_output():
    """LLM returns valid JSON for values."""
    return json.dumps({
        "technique": {
            "name": "values_exploration",
            "description": "Explore what matters behind the pain",
            "steps": ["Ask what matters", "Connect pain to values"],
        },
        "conversation_goal": "帮助用户发现痛苦指向了什么重要的东西",
        "adaptation": {
            "tone_adjustment": "curious",
            "pace_adjustment": "normal",
            "culture_note": "use 'what matters to you' instead of 'values'",
        },
        "contraindications": [],
    })


@pytest.fixture
def valid_committed_action_llm_output():
    """LLM returns valid JSON for committed_action."""
    return json.dumps({
        "technique": {
            "name": "action_planning",
            "description": "Plan one small values-based action",
            "steps": ["Review values", "Choose one small action", "Commit gently"],
        },
        "conversation_goal": "帮助用户制定一个微小的行动承诺",
        "adaptation": {
            "tone_adjustment": "encouraging",
            "pace_adjustment": "normal",
            "culture_note": "action must fit user's real life",
        },
        "contraindications": [],
    })


@pytest.fixture(autouse=True)
def _isolate_skill_registry(request):
    """Keep the global skill_registry hermetic between test modules.

    The registry is populated as a side effect of importing src.skills.
    Because modules are imported once per session (at collection), a registry-count
    test such as test_act_skills.py::TestSkillRegistry — which asserts the
    registry contains exactly the 7 ACT skills — would see every skill module's
    registration leak in and fail.

    This fixture snapshots the registry before each test and restores it after.
    For TestSkillRegistry specifically, it first re-populates the registry with
    ACT skills only (via a fresh reload of src.skills.act).
    """
    import importlib
    from src.core.skill import skill_registry

    snapshot = dict(skill_registry._skills)
    if request.cls is not None and request.cls.__name__ == "TestSkillRegistry":
        from src.skills import act
        skill_registry._skills.clear()
        importlib.reload(act)

    yield

    skill_registry._skills.clear()
    skill_registry._skills.update(snapshot)
