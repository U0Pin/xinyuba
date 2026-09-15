"""Tests for MI skills."""
import pytest
from src.skills.techniques import (
    MIProcess, MIExploreTechnique, MIEvocationTechnique, MICommitmentTechnique,
)
from src.skills.mi import (
    MiStateAssessment, _compute_mi_intervention_need,
)
from src.core.skill import SkillType, skill_registry, discrete_state


class TestMiTechniques:
    """Test MI technique enums are complete and valid."""

    def test_process_enum_has_three_values(self):
        assert len(MIProcess) == 3
        assert MIProcess.EXPLORE.value == "explore"
        assert MIProcess.EVOCATION.value == "evocation"
        assert MIProcess.COMMITMENT.value == "commitment"

    def test_explore_techniques(self):
        assert MIExploreTechnique.OARS.value == "oars"
        assert MIExploreTechnique.AGENDA_MAPPING.value == "agenda_mapping"

    def test_evocation_techniques(self):
        assert MIEvocationTechnique.DARN_CAT.value == "darn_cat"
        assert MIEvocationTechnique.IMPORTANCE_CONFIDENCE_RULER.value == "importance_confidence_ruler"

    def test_commitment_techniques(self):
        assert MICommitmentTechnique.CHANGE_PLAN.value == "change_plan"
        assert MICommitmentTechnique.COMMITMENT_LANGUAGE.value == "commitment_language"


class TestMiStateAssessment:
    """Test mi_state_assessment skill."""

    def test_registered(self):
        skill = skill_registry.get("mi_state_assessment")
        assert skill is not None
        assert skill.skill_type == SkillType.ANALYSIS

    def test_no_llm_returns_error(self):
        skill = MiStateAssessment()
        result = skill.execute({"user_text": "hello"}, {})
        assert result.success is False
        assert "error" in result.output

    def test_no_user_text_returns_success_with_empty(self):
        skill = MiStateAssessment()
        result = skill.execute({}, {})
        assert result.success is False


class TestMiInterventionFormula:
    """Test backend formula for MI intervention need."""

    def test_high_ambivalence_drives_priority(self):
        # High ambivalence, everything else neutral
        priority = _compute_mi_intervention_need(
            ambivalence=0.8, change_readiness=0.5,
            self_efficacy=0.5, change_talk=0.3, sustain_talk=0.3
        )
        assert priority > 0.4  # Ambivalence weight 0.35 * 0.8 = 0.28 base

    def test_high_readiness_lowers_priority(self):
        priority_high = _compute_mi_intervention_need(
            ambivalence=0.3, change_readiness=0.8,
            self_efficacy=0.5, change_talk=0.3, sustain_talk=0.1
        )
        priority_low = _compute_mi_intervention_need(
            ambivalence=0.3, change_readiness=0.2,
            self_efficacy=0.5, change_talk=0.3, sustain_talk=0.1
        )
        assert priority_high < priority_low

    def test_sustain_talk_increases_priority(self):
        priority_with = _compute_mi_intervention_need(
            ambivalence=0.3, change_readiness=0.5,
            self_efficacy=0.5, change_talk=0.1, sustain_talk=0.8
        )
        priority_without = _compute_mi_intervention_need(
            ambivalence=0.3, change_readiness=0.5,
            self_efficacy=0.5, change_talk=0.1, sustain_talk=0.1
        )
        assert priority_with > priority_without


class TestMiDiscreteState:
    """Test discrete state mapping."""

    _TEST_THRESHOLDS = [(0.25, "low"), (0.50, "mid"), (0.75, "high"), (1.01, "max")]

    def test_below_first_threshold(self):
        assert discrete_state(0.1, self._TEST_THRESHOLDS, inclusive=True) == "low"

    def test_middle_threshold(self):
        assert discrete_state(0.5, self._TEST_THRESHOLDS, inclusive=True) == "mid"

    def test_above_last_threshold(self):
        assert discrete_state(0.9, self._TEST_THRESHOLDS, inclusive=True) == "max"


class TestMiInterventions:
    """Test MI intervention skills are correctly registered."""

    @pytest.mark.parametrize("name,expected_type", [
        ("mi_explore", SkillType.INTERVENTION),
        ("mi_evocation", SkillType.INTERVENTION),
        ("mi_commitment", SkillType.INTERVENTION),
    ])
    def test_skill_registered_and_type(self, name, expected_type):
        skill = skill_registry.get(name)
        assert skill is not None, f"Skill {name} not found"
        assert skill.skill_type == expected_type

    @pytest.mark.parametrize("skill_name", ["mi_explore", "mi_evocation", "mi_commitment"])
    def test_fallback_always_succeeds(self, skill_name):
        skill = skill_registry.get(skill_name)
        # Test with empty assessment — fallback should always work
        result = skill.fallback({"assessment": {"ambivalence": {"state": "moderate"}}})
        assert result.success is True
        assert result.output["technique"]["name"] is not None
