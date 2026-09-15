"""Tests for ACT Skill execution, fallback behavior, and registry.

Tests focus on interface contracts — return types, field completeness, fallback
gracefulness — NOT on LLM prompt quality or response content.
"""

import json
import pytest
from unittest.mock import MagicMock

from src.core.skill import Skill, SkillType, SkillResult, skill_registry
from src.core.skill import llm_json as _llm_call
from src.core.skill import format_history as _format_history
from src.skills.act import (
    ActStateAssessment,
    ActDefusion,
    ActAcceptance,
    ActPresentMoment,
    ActSelfAsContext,
    ActValues,
    ActCommittedAction,
)


# ═══════════════════════════════════════════════════════════════════════════════
# _llm_call helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestLlmCall:
    def test_returns_dict_on_valid_json(self):
        llm = MagicMock()
        llm.invoke.return_value.content = '{"key": "value"}'
        result = _llm_call(llm, "prompt")
        assert result == {"key": "value"}

    def test_returns_empty_dict_on_invalid_json(self):
        llm = MagicMock()
        llm.invoke.return_value.content = "not json at all"
        result = _llm_call(llm, "prompt")
        assert result == {}

    def test_returns_empty_dict_on_llm_error(self):
        """If LLM raises, the caller handles it (I1 fix: only catch JSONDecodeError)."""
        llm = MagicMock()
        llm.invoke.side_effect = ValueError("connection lost")
        with pytest.raises(ValueError):
            _llm_call(llm, "prompt")

    def test_returns_empty_dict_on_non_json_response(self):
        llm = MagicMock()
        llm.invoke.return_value.content = '```json\n{"key": "value"}\n```'  # markdown-wrapped
        result = _llm_call(llm, "prompt")
        assert result == {}


# ═══════════════════════════════════════════════════════════════════════════════
# _format_history helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatHistory:
    def test_empty_history(self):
        assert _format_history([]) == "(start of conversation)"

    def test_single_turn(self):
        history = [{"user": "hello", "agent": "hi there"}]
        result = _format_history(history)
        assert "User: hello" in result
        assert "Agent: hi there" in result

    def test_multiple_turns(self):
        history = [
            {"user": "msg1", "agent": "reply1"},
            {"user": "msg2", "agent": "reply2"},
        ]
        result = _format_history(history)
        lines = result.split("\n")
        assert len(lines) == 4
        assert "User: msg1" in result
        assert "Agent: reply2" in result

    def test_missing_keys_default_to_empty(self):
        history = [{"user": "only user"}]
        result = _format_history(history)
        assert "Agent: " in result

    def test_returns_string(self):
        assert isinstance(_format_history([]), str)


# ═══════════════════════════════════════════════════════════════════════════════
# ActStateAssessment — ANALYSIS skill
# ═══════════════════════════════════════════════════════════════════════════════

class TestActStateAssessment:
    def test_name_and_type(self):
        s = ActStateAssessment()
        assert s.name == "act_state_assessment"
        assert s.skill_type == SkillType.ANALYSIS

    def test_missing_llm_returns_error(self):
        s = ActStateAssessment()
        result = s.execute({"user_text": "hello"}, {})
        assert isinstance(result, SkillResult)
        assert result.success is False
        assert "error" in result.output

    def test_missing_user_text_returns_error(self):
        s = ActStateAssessment()
        result = s.execute({"user_text": ""}, {"llm": MagicMock()})
        assert isinstance(result, SkillResult)
        assert result.success is False

    def test_empty_input_returns_error(self):
        s = ActStateAssessment()
        result = s.execute({}, {})
        assert result.success is False

    def test_valid_llm_response_returns_success(self, mock_llm):
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 0.3, "state": "cf_hookable", "confidence": 0.7, "evidence": ["sample"]},
            "avoidance": {"index": 0.3, "state": "ea_hesitant", "confidence": 0.7, "evidence": ["sample"]},
            "openness": {"index": 0.5, "state": "eo_receptive", "confidence": 0.7, "evidence": ["sample"]},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.7, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.7},
            "overall_trend": "stable",
            "emotional_intensity": 0.4,
            "escalation_level": "activated",
            "primary_concern": "fusion",
            "should_intervene": False,
            "recommended_processes": [],
        })

        result = s.execute(
            {"user_text": "I feel stuck", "emotion": {"pad": {"A": 0.4}}},
            {"llm": mock_llm},
        )
        assert result.success is True
        assert "fusion" in result.output
        assert "avoidance" in result.output
        assert "openness" in result.output
        assert "alignment" in result.output
        assert "activation" in result.output

    def test_output_contains_psychological_flexibility(self, mock_llm):
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 0.3, "state": "cf_hookable", "confidence": 0.7, "evidence": []},
            "avoidance": {"index": 0.3, "state": "ea_hesitant", "confidence": 0.7, "evidence": []},
            "openness": {"index": 0.5, "state": "eo_receptive", "confidence": 0.7, "evidence": []},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.7, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.7},
            "overall_trend": "stable",
            "emotional_intensity": 0.0,
            "escalation_level": "baseline",
            "primary_concern": "",
            "should_intervene": False,
            "recommended_processes": [],
        })
        result = s.execute(
            {"user_text": "hello", "emotion": {"pad": {"A": 0.0}}},
            {"llm": mock_llm},
        )
        assert "psychological_flexibility" in result.output
        assert isinstance(result.output["psychological_flexibility"], float)
        assert 0.0 <= result.output["psychological_flexibility"] <= 1.0

    def test_output_contains_flexibility_level(self, mock_llm):
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 0.3, "state": "cf_hookable", "confidence": 0.7, "evidence": []},
            "avoidance": {"index": 0.3, "state": "ea_hesitant", "confidence": 0.7, "evidence": []},
            "openness": {"index": 0.5, "state": "eo_receptive", "confidence": 0.7, "evidence": []},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.7, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.7},
            "overall_trend": "stable",
            "emotional_intensity": 0.0,
            "escalation_level": "baseline",
            "primary_concern": "",
            "should_intervene": False,
            "recommended_processes": [],
        })
        result = s.execute(
            {"user_text": "hello", "emotion": {"pad": {"A": 0.0}}},
            {"llm": mock_llm},
        )
        assert "flexibility_level" in result.output
        assert isinstance(result.output["flexibility_level"], str)

    def test_output_contains_escalation_level_string(self, mock_llm):
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 0.3, "state": "cf_hookable", "confidence": 0.7, "evidence": []},
            "avoidance": {"index": 0.3, "state": "ea_hesitant", "confidence": 0.7, "evidence": []},
            "openness": {"index": 0.5, "state": "eo_receptive", "confidence": 0.7, "evidence": []},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.7, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.7},
            "overall_trend": "stable",
            "emotional_intensity": 0.0,
            "escalation_level": "baseline",
            "primary_concern": "",
            "should_intervene": False,
            "recommended_processes": [],
        })
        result = s.execute(
            {"user_text": "hello", "emotion": {"pad": {"A": 0.0}}},
            {"llm": mock_llm},
        )
        assert isinstance(result.output["escalation_level"], str)

    def test_indices_clamped_to_zero_one(self, mock_llm):
        """LLM outputs out-of-range indices are clamped."""
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 1.5, "state": "cf_fused", "confidence": 0.7, "evidence": []},
            "avoidance": {"index": -0.5, "state": "ea_willing", "confidence": 0.7, "evidence": []},
            "openness": {"index": 2.0, "state": "eo_open", "confidence": 0.7, "evidence": []},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.7, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.7},
            "overall_trend": "stable",
            "emotional_intensity": 0.0,
            "escalation_level": "baseline",
            "primary_concern": "",
            "should_intervene": False,
            "recommended_processes": [],
        })
        result = s.execute(
            {"user_text": "hello", "emotion": {"pad": {"A": 0.0}}},
            {"llm": mock_llm},
        )
        assert 0.0 <= result.output["fusion"]["index"] <= 1.0
        assert 0.0 <= result.output["avoidance"]["index"] <= 1.0
        assert 0.0 <= result.output["openness"]["index"] <= 1.0

    def test_dimensions_have_required_fields(self, mock_llm):
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 0.4, "state": "cf_hookable", "confidence": 0.8, "evidence": ["clue"]},
            "avoidance": {"index": 0.4, "state": "ea_hesitant", "confidence": 0.8, "evidence": ["clue"]},
            "openness": {"index": 0.5, "state": "eo_receptive", "confidence": 0.8, "evidence": ["clue"]},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.8, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.8},
            "overall_trend": "stable",
            "emotional_intensity": 0.0,
            "escalation_level": "baseline",
            "primary_concern": "",
            "should_intervene": False,
            "recommended_processes": [],
        })
        result = s.execute(
            {"user_text": "hello", "emotion": {"pad": {"A": 0.0}}},
            {"llm": mock_llm},
        )
        for dim in ["fusion", "avoidance", "openness", "alignment", "activation"]:
            assert "index" in result.output[dim], f"{dim} missing index"
            assert "state" in result.output[dim], f"{dim} missing state"
            assert "confidence" in result.output[dim], f"{dim} missing confidence"

    def test_bad_json_falls_back_to_error(self, mock_llm):
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = "not valid json!!!"
        result = s.execute(
            {"user_text": "hello", "emotion": {"pad": {"A": 0.0}}},
            {"llm": mock_llm},
        )
        assert result.success is False
        assert "error" in result.output

    def test_state_effects_set(self, mock_llm):
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 0.3, "state": "cf_hookable", "confidence": 0.7, "evidence": []},
            "avoidance": {"index": 0.3, "state": "ea_hesitant", "confidence": 0.7, "evidence": []},
            "openness": {"index": 0.5, "state": "eo_receptive", "confidence": 0.7, "evidence": []},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.7, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.7},
            "overall_trend": "stable",
            "emotional_intensity": 0.0,
            "escalation_level": "baseline",
            "primary_concern": "",
            "should_intervene": False,
            "recommended_processes": [],
        })
        result = s.execute(
            {"user_text": "hello", "emotion": {"pad": {"A": 0.0}}},
            {"llm": mock_llm},
        )
        assert "session.act_state" in result.state_effects


class TestActStateAssessmentHistory:
    """Test that history and previous assessment are correctly threaded."""

    def test_accepts_prev_act_state(self, mock_llm):
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 0.3, "state": "cf_hookable", "confidence": 0.7, "evidence": []},
            "avoidance": {"index": 0.3, "state": "ea_hesitant", "confidence": 0.7, "evidence": []},
            "openness": {"index": 0.5, "state": "eo_receptive", "confidence": 0.7, "evidence": []},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.7, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.7},
            "overall_trend": "worsening",
            "emotional_intensity": 0.0,
            "escalation_level": "baseline",
            "primary_concern": "",
            "should_intervene": False,
            "recommended_processes": [],
        })
        result = s.execute(
            {
                "user_text": "hello",
                "emotion": {"pad": {"A": 0.0}},
                "current_act_state": {"fusion": {"index": 0.5}},
            },
            {"llm": mock_llm},
        )
        assert result.success is True


# ═══════════════════════════════════════════════════════════════════════════════
# Shared INTERVENTION skill test patterns
# ═══════════════════════════════════════════════════════════════════════════════

FROZEN_INTERFACE_FIELDS = [
    "technique", "conversation_goal", "adaptation", "contraindications"
]
ADAPTATION_FIELDS = ["interaction_style", "tone_adjustment", "pace_adjustment", "culture_note"]
TECHNIQUE_FIELDS = ["name", "description"]


def _assert_frozen_interface(output: dict):
    """Verify the output conforms to the frozen intervention interface."""
    for field in FROZEN_INTERFACE_FIELDS:
        assert field in output, f"Missing frozen interface field: {field}"

    # adaptation sub-fields
    adaptation = output["adaptation"]
    for field in ADAPTATION_FIELDS:
        assert field in adaptation, f"Missing adaptation field: {field}"

    # technique sub-fields
    technique = output["technique"]
    for field in TECHNIQUE_FIELDS:
        assert field in technique, f"Missing technique field: {field}"

    # contraindications is a list of {condition, reason}
    contraindications = output["contraindications"]
    assert isinstance(contraindications, list)
    for c in contraindications:
        assert "condition" in c
        assert "reason" in c


def _assert_skill_result_valid(result: SkillResult, expected_name: str, expected_type: SkillType):
    """Common assertions for any skill result."""
    assert isinstance(result, SkillResult)
    assert result.skill_name == expected_name


# ═══════════════════════════════════════════════════════════════════════════════
# INTERVENTION skill tests — parametrized for all 6 skills
# ═══════════════════════════════════════════════════════════════════════════════

INTERVENTION_SKILLS = [
    ("act_defusion", ActDefusion, "defusion"),
    ("act_acceptance", ActAcceptance, "acceptance"),
    ("act_present_moment", ActPresentMoment, "present_moment"),
    ("act_self_as_context", ActSelfAsContext, "self_as_context"),
    ("act_values", ActValues, "values"),
    ("act_committed_action", ActCommittedAction, "committed_action"),
]


@pytest.mark.parametrize("name,cls,label", INTERVENTION_SKILLS)
class TestInterventionSkills:
    """Parametrized tests that run identically for all 6 intervention skills."""

    def test_name_and_type(self, name, cls, label):
        s = cls()
        assert s.name == name
        assert s.skill_type == SkillType.INTERVENTION

    def test_missing_llm_returns_error(self, name, cls, label):
        s = cls()
        result = s.execute({"user_text": "hello"}, {})
        assert result.success is False

    def test_missing_user_text_returns_error(self, name, cls, label):
        s = cls()
        result = s.execute({"user_text": ""}, {"llm": MagicMock()})
        assert result.success is False

    def test_valid_llm_returns_frozen_interface(
        self, name, cls, label, mock_llm, valid_assessment, request
    ):
        s = cls()
        fixture_name = f"valid_{label}_llm_output"
        llm_json = request.getfixturevalue(fixture_name)
        mock_llm.invoke.return_value.content = llm_json

        result = s.execute(
            {
                "user_text": "I feel terrible about myself",
                "assessment": valid_assessment,
                "emotion": {"pad": {"A": 0.4}},
                "profile": {"attachment_style": "secure"},
                "history": [{"user": "I'm sad", "agent": "Tell me more"}],
            },
            {"llm": mock_llm},
        )
        _assert_skill_result_valid(result, name, SkillType.INTERVENTION)
        assert result.success is True
        _assert_frozen_interface(result.output)
        assert "interaction_style" in result.output["adaptation"]

    def test_bad_json_falls_back_to_fallback(
        self, name, cls, label, mock_llm, valid_assessment
    ):
        s = cls()
        mock_llm.invoke.return_value.content = "garbage not json!!!"

        result = s.execute(
            {
                "user_text": "hello",
                "assessment": valid_assessment,
                "emotion": {},
                "profile": {},
                "history": [],
            },
            {"llm": mock_llm},
        )
        # Must still succeed (graceful degradation)
        assert result.success is True
        _assert_frozen_interface(result.output)

    def test_state_effects_set(self, name, cls, label, mock_llm, valid_assessment, request):
        s = cls()
        fixture_name = f"valid_{label}_llm_output"
        mock_llm.invoke.return_value.content = request.getfixturevalue(fixture_name)

        result = s.execute(
            {
                "user_text": "hello",
                "assessment": valid_assessment,
                "emotion": {},
                "profile": {},
                "history": [],
            },
            {"llm": mock_llm},
        )
        assert "session.act_intervention_log" in result.state_effects

    def test_technique_name_is_string(self, name, cls, label, mock_llm, valid_assessment, request):
        s = cls()
        fixture_name = f"valid_{label}_llm_output"
        mock_llm.invoke.return_value.content = request.getfixturevalue(fixture_name)

        result = s.execute(
            {
                "user_text": "hello",
                "assessment": valid_assessment,
                "emotion": {},
                "profile": {},
                "history": [],
            },
            {"llm": mock_llm},
        )
        assert isinstance(result.output["technique"]["name"], str)
        assert result.output["technique"]["name"] != ""

    def test_interaction_style_is_valid_enum(self, name, cls, label, mock_llm, valid_assessment, request):
        from src.core.act_state import InteractionStyle
        s = cls()
        fixture_name = f"valid_{label}_llm_output"
        mock_llm.invoke.return_value.content = request.getfixturevalue(fixture_name)

        result = s.execute(
            {
                "user_text": "hello",
                "assessment": valid_assessment,
                "emotion": {},
                "profile": {},
                "history": [],
            },
            {"llm": mock_llm},
        )
        style = result.output["adaptation"]["interaction_style"]
        valid_styles = [e.value for e in InteractionStyle]
        assert style in valid_styles, f"'{style}' not in {valid_styles}"


# ═══════════════════════════════════════════════════════════════════════════════
# Fallback behavior — per-skill fallback() produces valid frozen interface
# ═══════════════════════════════════════════════════════════════════════════════

class TestFallbackOutputs:
    def test_defusion_fallback(self):
        s = ActDefusion()
        result = s.fallback({"assessment": {"fusion": {"state": "cf_fused"}}})
        assert isinstance(result, SkillResult)
        assert result.success is True
        _assert_frozen_interface(result.output)
        assert result.output["technique"]["name"] == "thought_labeling"
        assert result.output["adaptation"]["interaction_style"] == "exploratory"

    def test_acceptance_fallback(self):
        s = ActAcceptance()
        result = s.fallback({})
        assert isinstance(result, SkillResult)
        _assert_frozen_interface(result.output)
        assert result.output["technique"]["name"] == "willingness_invitation"
        assert result.output["adaptation"]["interaction_style"] == "supportive"

    def test_present_moment_fallback(self):
        s = ActPresentMoment()
        result = s.fallback({"assessment": {"openness": {"state": "eo_guarded"}}})
        assert isinstance(result, SkillResult)
        _assert_frozen_interface(result.output)
        assert result.output["technique"]["name"] == "breath_anchor"
        assert result.output["adaptation"]["interaction_style"] == "anchoring"

    def test_present_moment_fallback_shutdown(self):
        """Shutdown state → still returns valid interface."""
        s = ActPresentMoment()
        result = s.fallback({"assessment": {"openness": {"state": "eo_shutdown"}}})
        _assert_frozen_interface(result.output)

    def test_self_as_context_fallback(self):
        s = ActSelfAsContext()
        result = s.fallback({})
        assert isinstance(result, SkillResult)
        _assert_frozen_interface(result.output)
        assert result.output["technique"]["name"] == "observer_metaphor"
        assert result.output["adaptation"]["interaction_style"] == "exploratory"

    def test_values_fallback(self):
        s = ActValues()
        result = s.fallback({})
        assert isinstance(result, SkillResult)
        _assert_frozen_interface(result.output)
        assert result.output["technique"]["name"] == "values_exploration"
        assert result.output["adaptation"]["interaction_style"] == "exploratory"

    def test_committed_action_fallback(self):
        s = ActCommittedAction()
        result = s.fallback({})
        assert isinstance(result, SkillResult)
        _assert_frozen_interface(result.output)
        assert result.output["technique"]["name"] == "action_planning"
        assert result.output["adaptation"]["interaction_style"] == "supportive"


# ═══════════════════════════════════════════════════════════════════════════════
# Interaction style → skill mapping (spec section 3.9)
# ═══════════════════════════════════════════════════════════════════════════════

class TestInteractionStyleMapping:
    """Verify each skill hardcodes the correct interaction_style per the design spec."""

    def test_defusion_is_exploratory(self, mock_llm, valid_assessment, valid_defusion_llm_output):
        mock_llm.invoke.return_value.content = valid_defusion_llm_output
        s = ActDefusion()
        result = s.execute(
            {"user_text": "hi", "assessment": valid_assessment, "emotion": {}, "profile": {}, "history": []},
            {"llm": mock_llm},
        )
        assert result.output["adaptation"]["interaction_style"] == "exploratory"

    def test_acceptance_is_supportive(self, mock_llm, valid_assessment, valid_acceptance_llm_output):
        mock_llm.invoke.return_value.content = valid_acceptance_llm_output
        s = ActAcceptance()
        result = s.execute(
            {"user_text": "hi", "assessment": valid_assessment, "emotion": {}, "profile": {}, "history": []},
            {"llm": mock_llm},
        )
        assert result.output["adaptation"]["interaction_style"] == "supportive"

    def test_present_moment_is_anchoring(self, mock_llm, valid_assessment, valid_present_moment_llm_output):
        mock_llm.invoke.return_value.content = valid_present_moment_llm_output
        s = ActPresentMoment()
        result = s.execute(
            {"user_text": "hi", "assessment": valid_assessment, "emotion": {}, "profile": {}, "history": []},
            {"llm": mock_llm},
        )
        assert result.output["adaptation"]["interaction_style"] == "anchoring"

    def test_self_as_context_is_exploratory(self, mock_llm, valid_assessment, valid_self_as_context_llm_output):
        mock_llm.invoke.return_value.content = valid_self_as_context_llm_output
        s = ActSelfAsContext()
        result = s.execute(
            {"user_text": "hi", "assessment": valid_assessment, "emotion": {}, "profile": {}, "history": []},
            {"llm": mock_llm},
        )
        assert result.output["adaptation"]["interaction_style"] == "exploratory"

    def test_values_is_exploratory(self, mock_llm, valid_assessment, valid_values_llm_output):
        mock_llm.invoke.return_value.content = valid_values_llm_output
        s = ActValues()
        result = s.execute(
            {"user_text": "hi", "assessment": valid_assessment, "emotion": {}, "profile": {}, "history": []},
            {"llm": mock_llm},
        )
        assert result.output["adaptation"]["interaction_style"] == "exploratory"

    def test_committed_action_is_supportive(self, mock_llm, valid_assessment, valid_committed_action_llm_output):
        mock_llm.invoke.return_value.content = valid_committed_action_llm_output
        s = ActCommittedAction()
        result = s.execute(
            {"user_text": "hi", "assessment": valid_assessment, "emotion": {}, "profile": {}, "history": []},
            {"llm": mock_llm},
        )
        assert result.output["adaptation"]["interaction_style"] == "supportive"


# ═══════════════════════════════════════════════════════════════════════════════
# LLM invalid technique name → default fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvalidTechniqueName:
    def test_invalid_name_corrected_to_default(self, mock_llm, valid_assessment):
        """LLM outputs an unknown technique name → corrected to safe default."""
        s = ActDefusion()
        mock_llm.invoke.return_value.content = json.dumps({
            "technique": {"name": "SOME_MADE_UP_TECHNIQUE", "description": "test", "steps": []},
            "conversation_goal": "test",
            "adaptation": {"tone_adjustment": "warm", "pace_adjustment": "slow", "culture_note": ""},
            "contraindications": [],
        })
        result = s.execute(
            {"user_text": "hi", "assessment": valid_assessment, "emotion": {}, "profile": {}, "history": []},
            {"llm": mock_llm},
        )
        assert result.success is True
        assert result.output["technique"]["name"] == "thought_labeling"


# ═══════════════════════════════════════════════════════════════════════════════
# Skill Registry
# ═══════════════════════════════════════════════════════════════════════════════

class TestSkillRegistry:
    def test_all_seven_registered(self):
        all_skills = skill_registry.list_all()
        assert len(all_skills) == 7, f"Expected 7, got {len(all_skills)}"

    def test_get_by_name(self):
        s = skill_registry.get("act_state_assessment")
        assert s is not None
        assert s.name == "act_state_assessment"
        assert s.skill_type == SkillType.ANALYSIS

    def test_get_by_name_intervention(self):
        for name in ["act_defusion", "act_acceptance", "act_present_moment",
                      "act_self_as_context", "act_values", "act_committed_action"]:
            s = skill_registry.get(name)
            assert s is not None, f"Skill '{name}' not found in registry"
            assert s.skill_type == SkillType.INTERVENTION, f"{name} should be INTERVENTION"

    def test_get_missing_returns_none(self):
        assert skill_registry.get("nonexistent_skill") is None

    def test_registry_registered_instances_not_classes(self):
        """Skills are registered as instances, not classes."""
        s = skill_registry.get("act_defusion")
        assert isinstance(s, Skill)


# ═══════════════════════════════════════════════════════════════════════════════
# Emotional intensity computation (I4 fix)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmotionalIntensity:
    def test_negative_arousal_not_treated_as_high(self, mock_llm):
        """Negative arousal (calmness) should not inflate emotional_intensity."""
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 0.3, "state": "cf_hookable", "confidence": 0.7, "evidence": []},
            "avoidance": {"index": 0.3, "state": "ea_hesitant", "confidence": 0.7, "evidence": []},
            "openness": {"index": 0.5, "state": "eo_receptive", "confidence": 0.7, "evidence": []},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.7, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.7},
            "overall_trend": "stable",
            "emotional_intensity": 0.0,
            "escalation_level": "baseline",
            "primary_concern": "",
            "should_intervene": False,
            "recommended_processes": [],
        })

        # Phase 3 修复：emotional_intensity 读扁平 key emotion["arousal"]
        # （旧实现读 emotion["pad"]["A"] 恒为 0，本测试同步更新输入形状）
        # Calm user: A = -0.9
        result_calm = s.execute(
            {"user_text": "hello", "emotion": {"arousal": -0.9}},
            {"llm": mock_llm},
        )
        # Escalated user: A = +0.9
        result_escalated = s.execute(
            {"user_text": "hello", "emotion": {"arousal": 0.9}},
            {"llm": mock_llm},
        )

        # Calm user should have lower emotional_intensity than escalated
        assert result_calm.output["emotional_intensity"] < result_escalated.output["emotional_intensity"], (
            f"Calm (A=-0.9): {result_calm.output['emotional_intensity']}, "
            f"Escalated (A=0.9): {result_escalated.output['emotional_intensity']}"
        )

    def test_calm_user_is_not_crisis(self, mock_llm):
        """Negative arousal should map to BASELINE, not crisis."""
        s = ActStateAssessment()
        mock_llm.invoke.return_value.content = json.dumps({
            "fusion": {"index": 0.3, "state": "cf_hookable", "confidence": 0.7, "evidence": []},
            "avoidance": {"index": 0.3, "state": "ea_hesitant", "confidence": 0.7, "evidence": []},
            "openness": {"index": 0.5, "state": "eo_receptive", "confidence": 0.7, "evidence": []},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.7, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.7},
            "overall_trend": "stable",
            "emotional_intensity": 0.0,
            "escalation_level": "baseline",
            "primary_concern": "",
            "should_intervene": False,
            "recommended_processes": [],
        })
        result = s.execute(
            {"user_text": "hello", "emotion": {"pad": {"A": -0.9}}},
            {"llm": mock_llm},
        )
        assert result.output["escalation_level"] == "baseline"
