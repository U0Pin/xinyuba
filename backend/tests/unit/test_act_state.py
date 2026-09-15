"""Tests for ACTStateVector — serialization, enum mapping, boundary values, formulas."""

import pytest
from src.core.act_state import (
    ACTStateVector,
    FusionState,
    AvoidanceState,
    OpennessState,
    FlexibilityLevel,
    EscalationLevel,
    InteractionStyle,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Enum value checks
# ═══════════════════════════════════════════════════════════════════════════════

class TestFusionState:
    def test_all_values_are_lowercase_snake(self):
        for state in FusionState:
            assert state.value == state.value.lower()
            assert " " not in state.value
            assert state.value.startswith("cf_")

    def test_five_states_ordered(self):
        states = list(FusionState)
        assert len(states) == 5
        assert states == [
            FusionState.CF_OBSERVING,
            FusionState.CF_HOOKABLE,
            FusionState.CF_HOOKED,
            FusionState.CF_FUSED,
            FusionState.CF_IDENTITY_FUSION,
        ]


class TestAvoidanceState:
    def test_all_values_are_lowercase_snake(self):
        for state in AvoidanceState:
            assert state.value == state.value.lower()
            assert state.value.startswith("ea_")

    def test_five_states_ordered(self):
        states = list(AvoidanceState)
        assert len(states) == 5


class TestOpennessState:
    def test_all_values_are_lowercase_snake(self):
        for state in OpennessState:
            assert state.value == state.value.lower()
            assert state.value.startswith("eo_")

    def test_five_states_ordered(self):
        states = list(OpennessState)
        assert len(states) == 5


class TestFlexibilityLevel:
    def test_five_levels(self):
        assert len(list(FlexibilityLevel)) == 5

    def test_all_values_prefixed(self):
        for level in FlexibilityLevel:
            assert level.value.startswith("pf_")


class TestEscalationLevel:
    def test_int_enum_values(self):
        assert EscalationLevel.BASELINE.value == 0
        assert EscalationLevel.ACTIVATED.value == 1
        assert EscalationLevel.ESCALATING.value == 2
        assert EscalationLevel.HIGHLY_ESCALATED.value == 3
        assert EscalationLevel.CRISIS_BOUNDARY.value == 4

    def test_name_lower_matches_spec_strings(self):
        assert EscalationLevel.BASELINE.name.lower() == "baseline"
        assert EscalationLevel.ACTIVATED.name.lower() == "activated"
        assert EscalationLevel.ESCALATING.name.lower() == "escalating"
        assert EscalationLevel.HIGHLY_ESCALATED.name.lower() == "highly_escalated"
        assert EscalationLevel.CRISIS_BOUNDARY.name.lower() == "crisis_boundary"


class TestInteractionStyle:
    def test_four_styles(self):
        assert len(list(InteractionStyle)) == 4

    def test_expected_values(self):
        assert InteractionStyle.EXPLORATORY.value == "exploratory"
        assert InteractionStyle.SUPPORTIVE.value == "supportive"
        assert InteractionStyle.DIRECTIVE.value == "directive"
        assert InteractionStyle.ANCHORING.value == "anchoring"


# ═══════════════════════════════════════════════════════════════════════════════
# ACTStateVector defaults
# ═══════════════════════════════════════════════════════════════════════════════

class TestACTStateVectorDefaults:
    def test_default_construction(self):
        v = ACTStateVector()
        assert v.fusion_index == 0.0
        assert v.avoidance_index == 0.0
        assert v.openness_index == 0.5
        assert v.alignment_index == 0.5
        assert v.activation_index == 0.5
        assert v.psychological_flexibility == 0.5
        assert v.emotional_intensity == 0.0
        assert v.overall_trend == "stable"
        assert v.should_intervene is False

    def test_default_confidence_zero(self):
        v = ACTStateVector()
        assert v.fusion_confidence == 0.0
        assert v.avoidance_confidence == 0.0
        assert v.openness_confidence == 0.0
        assert v.alignment_confidence == 0.0
        assert v.activation_confidence == 0.0

    def test_default_evidence_empty(self):
        v = ACTStateVector()
        assert v.fusion_evidence == []
        assert v.avoidance_evidence == []
        assert v.openness_evidence == []
        assert v.values_mentioned == []

    def test_default_discrete_states(self):
        v = ACTStateVector()
        assert v.fusion_state == FusionState.CF_OBSERVING
        assert v.avoidance_state == AvoidanceState.EA_WILLING
        assert v.openness_state == OpennessState.EO_RECEPTIVE
        assert v.flexibility_level == FlexibilityLevel.PF_ADAPTIVE
        assert v.escalation_level == EscalationLevel.BASELINE

    def test_field_assignable(self):
        v = ACTStateVector(
            fusion_index=0.8,
            fusion_state=FusionState.CF_FUSED,
        )
        assert v.fusion_index == 0.8
        assert v.fusion_state == FusionState.CF_FUSED


# ═══════════════════════════════════════════════════════════════════════════════
# compute_fusion_state — boundary values
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeFusionState:
    @pytest.mark.parametrize("index, expected", [
        (0.0, FusionState.CF_OBSERVING),
        (0.24, FusionState.CF_OBSERVING),
        (0.25, FusionState.CF_HOOKABLE),
        (0.44, FusionState.CF_HOOKABLE),
        (0.45, FusionState.CF_HOOKED),
        (0.64, FusionState.CF_HOOKED),
        (0.65, FusionState.CF_FUSED),
        (0.84, FusionState.CF_FUSED),
        (0.85, FusionState.CF_IDENTITY_FUSION),
        (1.0, FusionState.CF_IDENTITY_FUSION),
    ])
    def test_boundaries(self, index, expected):
        assert ACTStateVector.compute_fusion_state(index) == expected

    def test_returns_fusion_state_enum(self):
        result = ACTStateVector.compute_fusion_state(0.5)
        assert isinstance(result, FusionState)


# ═══════════════════════════════════════════════════════════════════════════════
# compute_avoidance_state — boundary values
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeAvoidanceState:
    @pytest.mark.parametrize("index, expected", [
        (0.0, AvoidanceState.EA_WILLING),
        (0.19, AvoidanceState.EA_WILLING),
        (0.20, AvoidanceState.EA_HESITANT),
        (0.39, AvoidanceState.EA_HESITANT),
        (0.40, AvoidanceState.EA_AVOIDING),
        (0.64, AvoidanceState.EA_AVOIDING),
        (0.65, AvoidanceState.EA_RIGID_CONTROL),
        (0.84, AvoidanceState.EA_RIGID_CONTROL),
        (0.85, AvoidanceState.EA_DISSOCIATING),
        (1.0, AvoidanceState.EA_DISSOCIATING),
    ])
    def test_boundaries(self, index, expected):
        assert ACTStateVector.compute_avoidance_state(index) == expected

    def test_returns_avoidance_state_enum(self):
        result = ACTStateVector.compute_avoidance_state(0.3)
        assert isinstance(result, AvoidanceState)


# ═══════════════════════════════════════════════════════════════════════════════
# compute_openness_state — boundary values (uses >= not <)
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeOpennessState:
    @pytest.mark.parametrize("index, expected", [
        (0.0, OpennessState.EO_SHUTDOWN),
        (0.09, OpennessState.EO_SHUTDOWN),
        (0.10, OpennessState.EO_DEFENDED),
        (0.24, OpennessState.EO_DEFENDED),
        (0.25, OpennessState.EO_GUARDED),
        (0.44, OpennessState.EO_GUARDED),
        (0.45, OpennessState.EO_RECEPTIVE),
        (0.69, OpennessState.EO_RECEPTIVE),
        (0.70, OpennessState.EO_OPEN),
        (1.0, OpennessState.EO_OPEN),
    ])
    def test_boundaries(self, index, expected):
        assert ACTStateVector.compute_openness_state(index) == expected

    def test_returns_openness_state_enum(self):
        result = ACTStateVector.compute_openness_state(0.5)
        assert isinstance(result, OpennessState)


# ═══════════════════════════════════════════════════════════════════════════════
# compute_flexibility_level
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeFlexibilityLevel:
    @pytest.mark.parametrize("pf, expected", [
        (1.0, FlexibilityLevel.PF_FLEXIBLE),
        (0.75, FlexibilityLevel.PF_FLEXIBLE),
        (0.74, FlexibilityLevel.PF_ADAPTIVE),
        (0.50, FlexibilityLevel.PF_ADAPTIVE),
        (0.49, FlexibilityLevel.PF_RIGID),
        (0.30, FlexibilityLevel.PF_RIGID),
        (0.29, FlexibilityLevel.PF_BRITTLE),
        (0.15, FlexibilityLevel.PF_BRITTLE),
        (0.14, FlexibilityLevel.PF_FROZEN),
        (0.0, FlexibilityLevel.PF_FROZEN),
    ])
    def test_boundaries(self, pf, expected):
        assert ACTStateVector.compute_flexibility_level(pf) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# compute_escalation
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeEscalation:
    @pytest.mark.parametrize("intensity, expected", [
        (0.0, EscalationLevel.BASELINE),
        (0.29, EscalationLevel.BASELINE),
        (0.30, EscalationLevel.ACTIVATED),
        (0.54, EscalationLevel.ACTIVATED),
        (0.55, EscalationLevel.ESCALATING),
        (0.74, EscalationLevel.ESCALATING),
        (0.75, EscalationLevel.HIGHLY_ESCALATED),
        (0.89, EscalationLevel.HIGHLY_ESCALATED),
        (0.90, EscalationLevel.CRISIS_BOUNDARY),
        (1.0, EscalationLevel.CRISIS_BOUNDARY),
    ])
    def test_boundaries(self, intensity, expected):
        assert ACTStateVector.compute_escalation(intensity) == expected

    def test_returns_escalation_level_enum(self):
        result = ACTStateVector.compute_escalation(0.5)
        assert isinstance(result, EscalationLevel)


# ═══════════════════════════════════════════════════════════════════════════════
# compute_flexibility — formula correctness
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeFlexibility:
    def test_formula_extreme_low(self):
        """High fusion + high avoidance + low everything → very low flexibility."""
        pf = ACTStateVector.compute_flexibility(
            fusion=1.0, avoidance=1.0, openness=0.0, alignment=0.0, activation=0.0
        )
        assert pf == 0.0

    def test_formula_extreme_high(self):
        """No fusion + no avoidance + full openness/alignment/activation → max flexibility."""
        pf = ACTStateVector.compute_flexibility(
            fusion=0.0, avoidance=0.0, openness=1.0, alignment=1.0, activation=1.0
        )
        assert pf == 1.0

    def test_formula_mid_range(self):
        """All at 0.5 → predictable output."""
        pf = ACTStateVector.compute_flexibility(
            fusion=0.5, avoidance=0.5, openness=0.5, alignment=0.5, activation=0.5
        )
        expected = round(
            0.35 * 0.5 + 0.25 * 0.5 + 0.20 * 0.5 + 0.10 * 0.5 + 0.10 * 0.5, 3
        )
        assert pf == expected

    def test_formula_high_distress_low_flexibility(self):
        """High distress scenario produces low flexibility."""
        pf_high = ACTStateVector.compute_flexibility(0.80, 0.75, 0.20, 0.30, 0.20)
        pf_low = ACTStateVector.compute_flexibility(0.15, 0.10, 0.80, 0.70, 0.70)
        assert pf_high < pf_low, f"High distress pf={pf_high} should be < low distress pf={pf_low}"

    def test_formula_is_deterministic(self):
        pf1 = ACTStateVector.compute_flexibility(0.3, 0.4, 0.5, 0.6, 0.7)
        pf2 = ACTStateVector.compute_flexibility(0.3, 0.4, 0.5, 0.6, 0.7)
        assert pf1 == pf2

    def test_fusion_has_highest_weight(self):
        """Fusion (0.35) has the highest weight — changing it should have the biggest impact."""
        base = ACTStateVector.compute_flexibility(0.5, 0.5, 0.5, 0.5, 0.5)
        pf_change_fusion = ACTStateVector.compute_flexibility(0.0, 0.5, 0.5, 0.5, 0.5)
        pf_change_avoidance = ACTStateVector.compute_flexibility(0.5, 0.0, 0.5, 0.5, 0.5)
        # fusion change (0.35 weight) should have bigger impact than avoidance change (0.25)
        assert (pf_change_fusion - base) > (pf_change_avoidance - base)

    def test_output_is_float(self):
        pf = ACTStateVector.compute_flexibility(0.3, 0.4, 0.5, 0.6, 0.7)
        assert isinstance(pf, float)

    def test_output_in_range(self):
        """Any valid input should produce output in [0, 1]."""
        import random
        for _ in range(100):
            args = [random.random() for _ in range(5)]
            pf = ACTStateVector.compute_flexibility(*args)
            assert 0.0 <= pf <= 1.0, f"pf={pf} out of range for args={args}"


# ═══════════════════════════════════════════════════════════════════════════════
# to_dict
# ═══════════════════════════════════════════════════════════════════════════════

class TestToDict:
    def test_returns_dict(self):
        v = ACTStateVector()
        result = v.to_dict()
        assert isinstance(result, dict)

    def test_all_required_keys_present(self):
        v = ACTStateVector()
        d = v.to_dict()
        required = [
            "fusion_index", "avoidance_index", "openness_index",
            "alignment_index", "activation_index",
            "fusion_confidence", "avoidance_confidence", "openness_confidence",
            "alignment_confidence", "activation_confidence",
            "fusion_state", "avoidance_state", "openness_state",
            "flexibility_level", "psychological_flexibility",
            "emotional_intensity", "escalation_level",
            "overall_trend", "primary_concern", "should_intervene",
            "recommended_processes",
        ]
        for key in required:
            assert key in d, f"Missing key: {key}"

    def test_escalation_level_is_string_not_int(self):
        """I5 fix: to_dict() must use .name.lower() for escalation_level."""
        v = ACTStateVector(escalation_level=EscalationLevel.BASELINE)
        d = v.to_dict()
        assert isinstance(d["escalation_level"], str)
        assert d["escalation_level"] == "baseline"
        assert d["escalation_level"] != 0

    def test_escalation_level_all_values_string(self):
        for level in EscalationLevel:
            v = ACTStateVector(escalation_level=level)
            d = v.to_dict()
            assert isinstance(d["escalation_level"], str)
            assert d["escalation_level"] == level.name.lower()

    def test_discrete_states_use_enum_value(self):
        v = ACTStateVector(
            fusion_state=FusionState.CF_HOOKED,
            avoidance_state=AvoidanceState.EA_AVOIDING,
            openness_state=OpennessState.EO_GUARDED,
            flexibility_level=FlexibilityLevel.PF_RIGID,
        )
        d = v.to_dict()
        assert d["fusion_state"] == "cf_hooked"
        assert d["avoidance_state"] == "ea_avoiding"
        assert d["openness_state"] == "eo_guarded"
        assert d["flexibility_level"] == "pf_rigid"

    def test_roundtrip_consistent(self):
        """to_dict() should be idempotent for same state."""
        v = ACTStateVector(fusion_index=0.6)
        d1 = v.to_dict()
        d2 = v.to_dict()
        assert d1 == d2

    def test_recommended_processes_serialized(self):
        v = ACTStateVector(recommended_processes=[
            {"process": "defusion", "priority": 1, "rationale": "high fusion"},
        ])
        d = v.to_dict()
        assert d["recommended_processes"] == [{"process": "defusion", "priority": 1, "rationale": "high fusion"}]

    def test_should_intervene_serialized(self):
        v = ACTStateVector(should_intervene=True)
        assert v.to_dict()["should_intervene"] is True
        v2 = ACTStateVector(should_intervene=False)
        assert v2.to_dict()["should_intervene"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Data integrity
# ═══════════════════════════════════════════════════════════════════════════════

class TestACTStateVectorDataIntegrity:
    def test_indices_clamped_conceptually(self):
        """Indices should represent [0,1] range — enforced by callers (skills)."""
        v = ACTStateVector(fusion_index=0.5)
        assert 0.0 <= v.fusion_index <= 1.0

    def test_evidence_lists_are_independent(self):
        v1 = ACTStateVector(fusion_evidence=["I'm bad"])
        v2 = ACTStateVector(fusion_evidence=["I'm unlovable"])
        assert v1.fusion_evidence != v2.fusion_evidence
        v1.fusion_evidence.append("another")
        assert len(v2.fusion_evidence) == 1  # not mutated

    def test_to_dict_excludes_private_attributes(self):
        v = ACTStateVector()
        d = v.to_dict()
        for key in d:
            assert not key.startswith("_")
