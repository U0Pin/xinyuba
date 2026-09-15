"""Validation tests for the routing taxonomy and case schema."""

from pathlib import Path

from tests.eval.support.routing_schema import (
    EmotionSupportSkill,
    LIFECYCLE_STAGES,
    RoutingCase,
    RoutingFamily,
    TherapySkill,
    load_cases,
    schema_self_check,
    valid_skills_for_family,
)

HERE = Path(__file__).parent


def test_taxonomy_self_consistency():
    assert schema_self_check() == []


def test_families_have_expected_skill_sets():
    assert {s.value for s in TherapySkill} == {"cbt", "act", "dbt", "mi", "sfbt"}
    assert {s.value for s in EmotionSupportSkill} == {
        "affect_labeling",
        "pmr",
        "grounding",
    }
    assert valid_skills_for_family(RoutingFamily.CRISIS) == {"crisis"}
    assert valid_skills_for_family(RoutingFamily.DAILY) == {"daily"}


def test_lifecycle_stages_covered():
    assert LIFECYCLE_STAGES == {
        "off",
        "offering",
        "accepted",
        "active",
        "complete",
        "declined",
        "exited",
        "superseded",
        "cooldown",
    }


def test_case_validate_catches_errors():
    case = RoutingCase(
        case_id="BAD-1",
        text="x",
        expected_family=RoutingFamily.EMOTION_SUPPORT,
        expected_skill="cbt",  # wrong family
        rationale="",
    )
    errors = case.validate()
    assert any("skill" in e for e in errors)
    assert any("rationale" in e for e in errors)


def test_multi_turn_last_value_carry():
    case = RoutingCase(
        case_id="MT-X",
        text=["a", "b", "c"],
        expected_family=RoutingFamily.THERAPY,
        expected_skill_by_turn=[None, "grounding"],
        rationale="test",
    )
    assert case.is_multi_turn
    assert case.expected_skill_at(0) is None
    assert case.expected_skill_at(1) == "grounding"
    assert case.expected_skill_at(2) == "grounding"  # last value carried


def test_load_cases_on_real_dataset():
    cases = load_cases(HERE / "eval_data" / "routing_cases.json")
    assert len(cases) >= 100
    for c in cases:
        assert c.validate() == [], f"{c.case_id}: {c.validate()}"
