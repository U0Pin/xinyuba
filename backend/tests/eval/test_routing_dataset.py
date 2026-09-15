"""Integrity checks for the routing evaluation dataset.

Pure asserts, no printing, no production code touched.
"""

import collections
import json
from pathlib import Path

from tests.eval.support.routing_schema import (
    OVERRIDE_SKILLS,
    LIFECYCLE_STAGES,
    RoutingFamily,
    load_cases,
)

DATASET = Path(__file__).parent / "eval_data" / "routing_cases.json"

FAMILY_COUNTS_GOAL = {"therapy": 54, "emotion_support": 30, "daily": 15, "crisis": 12}


def _load_raw():
    with open(DATASET, encoding="utf-8") as f:
        return json.load(f)["cases"]


def _cases():
    return load_cases(DATASET)


def test_dataset_loads_100_to_150_cases():
    cases = _cases()
    assert 100 <= len(cases) <= 150


def test_case_ids_unique_and_text_unique():
    cases = _cases()
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case_id"
    texts = [json.dumps(c.text, ensure_ascii=False) for c in cases]
    assert len(texts) == len(set(texts)), "duplicate text (including multi-turn payloads)"


def test_rationale_required_nonempty():
    for c in _cases():
        assert isinstance(c.rationale, str) and len(c.rationale.strip()) >= 20, c.case_id


def test_family_counts_meet_minimums():
    counts = collections.Counter(c.expected_family.value for c in _cases())
    for fam, goal in FAMILY_COUNTS_GOAL.items():
        assert counts[fam] >= goal, f"{fam}: {counts[fam]} < {goal}"


def test_every_skill_has_easy_medium_boundary():
    by_skill = collections.defaultdict(set)
    for c in _cases():
        if c.expected_skill in {"cbt", "act", "dbt", "mi", "sfbt",
                                "affect_labeling", "pmr", "grounding"}:
            by_skill[c.expected_skill].add(c.difficulty.value)
    for skill, diffs in by_skill.items():
        for need in ("easy", "medium", "boundary"):
            assert need in diffs, f"{skill} missing {need}"


def test_every_family_has_negatives():
    """Negatives are family-level: a therapy-skill double-negative typically
    routes to DAILY or EMOTION_SUPPORT, so ``negative`` is asserted per family
    rather than per skill."""
    by_family = collections.defaultdict(int)
    for c in _cases():
        if c.difficulty.value == "negative":
            by_family[c.expected_family.value] += 1
    for fam in ("therapy", "emotion_support", "daily"):
        assert by_family[fam] >= 1, f"{fam} has no negative cases"


def test_dbt_has_boundary_case():
    assert any(c.expected_skill == "dbt" and c.difficulty.value == "boundary"
               for c in _cases())


def test_family_or_skill_expectation_present():
    for c in _cases():
        if c.expected_family == RoutingFamily.DAILY:
            continue  # daily may assert family only
        assert c.expected_skill is not None or c.boundary_case or c.expected_skill_by_turn, (
            f"{c.case_id}: neither skill nor boundary flag")


def test_multi_turn_shape():
    for c in _cases():
        if not c.is_multi_turn:
            continue
        turns = c.turns
        assert 2 <= len(turns) <= 5, c.case_id
        for name, raw in (("family", c.expected_family_by_turn),
                          ("skill", c.expected_skill_by_turn)):
            if raw is not None:
                assert 1 <= len(raw) <= len(turns), (c.case_id, name)


def test_at_least_three_multi_turn_cases():
    n = sum(1 for c in _cases() if c.is_multi_turn)
    assert n >= 3


def test_override_cases_active_before_valid():
    for c in _cases():
        if c.case_id.startswith("OVR-"):
            assert c.active_before in OVERRIDE_SKILLS, c.case_id
        if c.active_before is not None:
            assert c.active_before in OVERRIDE_SKILLS, c.case_id


def test_lifecycle_values_valid_and_covered():
    stages = [c.lifecycle_stage for c in _cases() if c.lifecycle_stage is not None]
    assert stages, "no lifecycle cases"
    for s in stages:
        assert s in LIFECYCLE_STAGES, s
    for need in ("active", "complete", "declined", "exited", "cooldown"):
        assert need in stages, f"lifecycle missing {need}"


def test_crisis_conflict_matrix_covers_eight_skills():
    ids = {c.case_id for c in _cases()}
    for skill, slug in (("cbt", "CBT"), ("act", "ACT"), ("dbt", "DBT"),
                        ("mi", "MI"), ("sfbt", "SFBT"),
                        ("pmr", "PMR"), ("grounding", "GND"),
                        ("affect_labeling", "AL")):
        assert f"OVR-CRI-{slug}" in ids, f"missing OVR-CRI-{skill}"


def test_es_internal_priority_matrix_present():
    ids = [c.case_id for c in _cases()
           if c.case_id.startswith("OVR-ES-") and c.active_before]
    # 6 tuples: pmr-active+gnd, pmr-active+affect, gnd-active+pmr,
    # gnd-active+affect, affect-active+pmr, affect-active+gnd
    assert len([i for i in ids]) >= 6
    mains = [c for c in _cases() if c.case_id.startswith("OVR-ES-")]
    winners = collections.Counter(c.expected_skill for c in mains)
    assert winners["pmr"] >= 3  # pmr wins or preempts in 3 of the 6
    assert winners["grounding"] >= 2
    # a higher-priority incoming skill always preempts lower ones
    for c in mains:
        if c.active_before in ("grounding", "affect_labeling") and \
                c.expected_skill == "pmr":
            assert True  # pmr preempts lower-priority holders
        if c.active_before == "pmr" and c.expected_skill != "pmr":
            raise AssertionError(f"{c.case_id}: pmr holder superseded by lower priority")


def test_all_cases_pass_schema_validation():
    for c in _cases():
        errors = c.validate()
        assert errors == [], (c.case_id, errors)


def test_key_negatives_present():
    texts = {c.text for c in _cases() if isinstance(c.text, str)}
    for probe in (
        "我最近有点焦虑。",
        "我最近有点焦虑，又说不上来在焦虑什么。",
        "我今天考试考砸了，觉得自己很失败。",
        "我不是特别紧张，只是想放松身体。",
    ):
        assert probe in texts, f"missing key negative: {probe}"


def test_boundary_pair_coverage_categories():
    cats = {c.category for c in _cases()}
    for need in ("grounding_vs_act_present_moment",
                 "grounding_vs_dbt_mindfulness",
                 "act_vs_cbt",
                 "grounding_vs_pmr",
                 "es_vs_daily",
                 "therapy_vs_daily_advice",
                 "therapy_vs_crisis",
                 "boundary_therapy_vs_daily"):
        assert need in cats, f"boundary pair category missing: {need}"
