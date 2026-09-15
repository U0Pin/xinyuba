"""Integrity sanity checks for the therapy evaluation dataset.

Pure asserts, no printing, no production code touched. Mirrors
test_routing_dataset.py conventions.
"""

import collections
import json
import importlib
from pathlib import Path

from tests.eval.support.therapy_schema import (
    CaseCategory,
    PSEUDO_FAMILIES,
    load_therapy_cases,
)

DATASET = Path(__file__).parent / "eval_data" / "therapy_cases.json"

ALL_THERAPY_FAMILIES = {"cbt", "act", "dbt", "mi", "sfbt"}
FAMILY_COUNTS_GOAL = {"cbt": 20, "act": 20, "dbt": 20, "mi": 20, "sfbt": 20}
BOUNDARY_PAIRS_MIN = 3  # per unordered therapy pair


def _cases():
    return load_therapy_cases(DATASET)


def _raw():
    with open(DATASET, encoding="utf-8") as f:
        return json.load(f)["cases"]


def test_dataset_size_and_loads():
    cases = _cases()
    assert 140 <= len(cases) <= 180
    assert len(_raw()) == len(cases)


def test_every_case_validates():
    for c in _cases():
        assert c.validate() == [], (c.case_id, c.validate())


def test_case_ids_and_texts_unique():
    cases = _cases()
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case_id"
    texts = [json.dumps(c.text, ensure_ascii=False) for c in cases]
    assert len(texts) == len(set(texts)), "duplicate text payloads"


def test_rationale_present_nontrivial():
    for c in _cases():
        assert isinstance(c.rationale, str) and len(c.rationale.strip()) >= 20, c.case_id


def test_per_family_counts_meet_goal():
    counts = collections.Counter()
    for c in _cases():
        fams = c.fams
        if len(fams) == 1:
            counts[fams[0]] += 1
    for fam, goal in FAMILY_COUNTS_GOAL.items():
        assert counts[fam] >= goal, f"{fam}: {counts[fam]} < {goal}"


def test_boundary_pairs_cover_every_unordered_therapy_pair():
    """Every unordered pair of the five therapy families has >= BOUNDARY_PAIRS_MIN
    boundary cases (pseudo-family pairs like EMOTION_SUPPORT also counted, based
    on whichever families appear in boundary_pair)."""
    counts = collections.Counter()
    for c in _cases():
        if c.category != CaseCategory.BOUNDARY or not c.boundary_pair:
            continue
        norm = sorted(p.lower() for p in c.boundary_pair)
        counts[tuple(norm)] += 1
    fams = sorted(ALL_THERAPY_FAMILIES)
    for i in range(len(fams)):
        for j in range(i + 1, len(fams)):
            pair = (fams[i], fams[j])
            assert counts[pair] >= BOUNDARY_PAIRS_MIN, \
                f"boundary pair {pair} has {counts[pair]}"


def test_ambiguity_quota():
    amb = [c for c in _cases() if c.category == CaseCategory.AMBIGUOUS]
    assert len(amb) >= 8
    for c in amb:
        assert c.expected_family is None and len(c.fams) >= 2


def test_negative_quota_and_no_therapy_consistency():
    neg = [c for c in _cases()
           if c.expected_no_therapy or c.category == CaseCategory.NEGATIVE]
    assert len(neg) >= 10
    for c in _cases():
        if c.expected_no_therapy:
            assert c.expected_family is None
            assert c.acceptable_families is not None and c.acceptable_families == []
            assert c.skill_at(0) is None


def test_es_boundary_quota():
    """Therry-vs-Emotion-Support boundary cases use pseudo-family strings from
    the routing taxonomy; each must name at least two sides in boundary_pair."""
    es = [c for c in _cases()
          if c.boundary_pair and any(
              p.upper() in PSEUDO_FAMILIES for p in c.boundary_pair)]
    assert len(es) >= 8
    for c in es:
        fam_at = c.family_at(0)
        assert fam_at in PSEUDO_FAMILIES or c.expected_family in PSEUDO_FAMILIES \
            or fam_at in ALL_THERAPY_FAMILIES


def test_crisis_conflict_quota_and_expectations():
    cc = [c for c in _cases() if c.safety.value == "crisis_conflict"]
    assert len(cc) >= 10
    for c in cc:
        assert c.family_at(0) == "CRISIS"


def test_difficulty_mix_per_family():
    by_fam = collections.defaultdict(set)
    for c in _cases():
        fams = c.fams
        if len(fams) == 1 and fams[0] in ALL_THERAPY_FAMILIES:
            by_fam[fams[0]].add(c.difficulty.value)
    for fam, diffs in by_fam.items():
        assert {"easy", "medium", "hard"} <= diffs or \
               {"easy", "medium", "boundary"} <= diffs, f"{fam}: {diffs}"


def test_technique_names_valid_against_actual_enums():
    """expected_intervention values must be members of the real
    src/skills/techniques.py technique enums (imported fresh, not copied)."""
    import enum as enum_mod
    tech_mod = importlib.import_module("src.skills.techniques")
    import src.skills.cbt as cbt_mod
    valid = set()
    for name in dir(tech_mod):
        obj = getattr(tech_mod, name)
        if (isinstance(obj, type) and issubclass(obj, enum_mod.Enum)
                and name.endswith("Technique")):
            vals = {m.value for m in obj}
            valid |= vals
    assert valid, "technique module yielded no enums"
    # CBT has no Technique enum class; its techniques are per-skill
    # valid_techniques strings in src/skills/cbt.py.
    cbt_techniques = set()
    for name in dir(cbt_mod):
        obj = getattr(cbt_mod, name)
        vt = getattr(obj, "valid_techniques", None)
        if isinstance(vt, list) and obj is not cbt_mod:
            cbt_techniques |= set(vt)
    assert cbt_techniques
    valid |= cbt_techniques
    for c in _cases():
        if c.expected_intervention is not None:
            assert c.expected_intervention in valid, \
                (c.case_id, c.expected_intervention)
            fam = c.family_at(0)
            if fam == "cbt":
                assert c.expected_intervention in cbt_techniques
            elif fam in ALL_THERAPY_FAMILIES:
                for name in dir(tech_mod):
                    obj = getattr(tech_mod, name)
                    if (isinstance(obj, type)
                            and issubclass(obj, enum_mod.Enum)
                            and name.endswith("Technique")
                            and c.expected_intervention in {m.value for m in obj}):
                        from tests.eval.support.therapy_schema import technique_family_for_enum
                        assert technique_family_for_enum(name) == fam


def test_multiturn_structure():
    mt = [c for c in _cases() if c.is_multi_turn]
    assert len(mt) >= 6
    for c in mt:
        assert len(c.turns) >= 2 and len(c.turns) <= 5
        if c.expected_family_by_turn is not None:
            assert all(
                v is None or v in PSEUDO_FAMILIES
                or v in ALL_THERAPY_FAMILIES
                for v in c.expected_family_by_turn
            )
    # one case per scenario letter A-F (audit scenarios)
    letters = {c.case_id.split("-")[-1] for c in mt}
    for need in ("A", "B", "C", "D", "E", "F"):
        assert need in letters, f"missing scenario {need}"


def test_multiturn_family_switch_only_via_pseudo_or_consistent():
    """Within multi-turn therapy cases the family must not cross therapy
    families mid-session except via pseudo-families (crisis supersede)."""
    for c in _cases():
        if not c.is_multi_turn or c.expected_family_by_turn is None:
            continue
        fams = [v for v in c.expected_family_by_turn if v is not None]
        therapy_on_path = [v for v in fams if v in ALL_THERAPY_FAMILIES]
        if therapy_on_path and not any(v.upper() in PSEUDO_FAMILIES for v in fams):
            assert len(set(therapy_on_path)) <= 1, c.case_id
