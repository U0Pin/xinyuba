"""Validation tests for the therapy case schema."""

from tests.eval.support.therapy_schema import (
    CaseCategory,
    Difficulty,
    SafetyLevel,
    TherapyCase,
    TherapyFamily,
    case_from_dict,
    load_therapy_cases,
    schema_self_check,
)


def _ok(**kw) -> TherapyCase:
    base = dict(
        case_id="TST-1",
        text="一些中文文本。",
        expected_family="cbt",
        rationale="A rationale long enough to pass validation checks.",
        category="medium",
        difficulty="medium",
        safety="normal",
    )
    base.update(kw)
    return case_from_dict(base)


def test_schema_self_check_passes():
    assert schema_self_check() == []


def test_valid_case_zero_errors():
    c = _ok(intervention_stub=None) if False else _ok()
    assert c.validate() == []


def test_acceptable_default_derived_from_expected():
    c = _ok()
    assert c.fams == ["cbt"]
    c2 = _ok(acceptable_families=["cbt", "act"])
    assert c2.fams == ["cbt", "act"]


def test_acceptable_must_contain_expected():
    c = _ok(acceptable_families=["act"])
    errs = c.validate()
    assert any("contain" in e for e in errs)


def test_ambiguous_requires_null_expected_and_two_fams():
    c = _ok(category="ambiguous", acceptable_families=["cbt", "act"],
            expected_family=None)
    assert c.validate() == []
    c = _ok(category="ambiguous", acceptable_families=["cbt", "act"],
            expected_family="cbt")
    assert any("must have expected_family=None" in e for e in c.validate())
    c = _ok(category="ambiguous", acceptable_families=["cbt"], expected_family=None)
    errs = c.validate()
    assert errs
    assert any(">=2" in e for e in errs)


def test_boundary_requires_pair():
    c = _ok(category="boundary", expected_family="cbt",
            boundary_pair=["cbt", "act"])
    assert c.validate() == []
    c = _ok(category="boundary", expected_family="cbt")
    assert any("boundary_pair" in e for e in c.validate())
    c = _ok(category="boundary", expected_family="cbt", boundary_pair=["cbt", "bogus!"])
    assert any("bogus!" in e for e in c.validate())


def test_expected_no_therapy_rules():
    c = _ok(expected_family=None, expected_no_therapy=True,
            acceptable_families=[])
    assert c.validate() == []
    c = _ok(expected_family=None, expected_no_therapy=True,
            acceptable_families=[], text=["今天食堂排队特别久。", "刚跑完步，有点累。"],
            category="negative")
    assert c.validate() == []
    c = _ok(expected_family="cbt", expected_no_therapy=True)
    assert any("expected_family=None" in e for e in c.validate())
    c = _ok(expected_family=None, expected_no_therapy=True,
            acceptable_families=["act"])
    assert any("empty acceptable" in e for e in c.validate())


def test_crisis_conflict_must_expect_crisis():
    c = _ok(safety="crisis_conflict", expected_family="CRISIS",
            difficulty="hard")
    assert c.validate() == []
    c = _ok(safety="crisis_conflict", expected_family="act")
    assert any("CRISIS" in e for e in c.validate())


def test_multi_turn_structure():
    c = _ok(
        text=["一。", "二。", "三。"],
        expected_family="cbt",
        expected_family_by_turn=["cbt", "act", "act"],
        expected_skill_by_turn=[None, "cbt", None],
        category="multi_turn",
    )
    # turn-1 skill 'cbt' invalid for family 'act'
    errs = c.validate()
    assert any("turn 1" in e for e in errs)
    c = _ok(
        text=["一。", "二。"],
        expected_family="cbt",
        category="multi_turn",
    )
    assert c.validate() == []
    c = _ok(text=["一。"], expected_family="cbt", category="multi_turn")
    assert any(">=2 texts" in e for e in c.validate())


def test_backfill_semantics():
    c = _ok(
        text=["一。", "二。", "三。"],
        expected_family="cbt",
        expected_family_by_turn=["cbt", "act"],
        category="multi_turn",
    )
    assert c.family_at(0) == "cbt"
    assert c.family_at(1) == "act"
    assert c.family_at(2) == "act"  # last-value carry
    assert c.skill_at(1) is None
    c2 = _ok(expected_skill="cbt")
    assert c2.skill_at(0) == "cbt"


def test_invalid_enumerations_flagged():
    c = _ok(difficulty="impossible")
    assert any("difficulty" in e for e in c.validate())
    c = _ok(safety="paranoid")
    assert any("safety" in e for e in c.validate())
    c = _ok(category="astral")
    assert any("category" in e for e in c.validate())


def test_rationale_and_enums_still_present():
    assert TherapyFamily("sfbt") is TherapyFamily.SFBT
    assert CaseCategory("lifecycle") == CaseCategory.LIFECYCLE
    assert Difficulty("boundary") == Difficulty.BOUNDARY
    assert SafetyLevel("safety_relevant") == SafetyLevel.SAFETY_RELEVANT


def test_intervention_requires_therapy_family():
    c = _ok(expected_family="CRISIS", expected_intervention="dear_man")
    assert any("expected_intervention" in e for e in c.validate())


def test_load_therapy_cases_roundtrip(tmp_path):
    import json
    p = tmp_path / "cases.json"
    p.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "X-1",
                        "text": "你好",
                        "expected_family": "act",
                        "rationale": "Because gold is act here.",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cases = load_therapy_cases(p)
    assert len(cases) == 1 and cases[0].case_id == "X-1"
    assert cases[0].fams == ["act"]
    assert cases[0].safety == SafetyLevel.NORMAL
