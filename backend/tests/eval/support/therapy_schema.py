"""Therapy taxonomy and evaluation case schema.

Companion to ``routing_schema.py``. Pseudo-families "CRISIS" / "EMOTION_SUPPORT"
/ "DAILY" reuse the cached routing taxonomy strings verbatim (they denominate
the routing winner of the same scheduler, not a therapy family): a therapy-boundary
case like grounded-stabilisation-vs-ACT carries expected_family="EMOTION_SUPPORT".

Sizing per docs/THERAPY_EVALUATION.md: family decision = ONE TherapyDecider LLM
call; skill = code pick_skill; technique = LLM inside per-skill enum
(src/skills/techniques.py), so expected_intervention must use exact enum values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Union


class TherapyFamily(str, Enum):
    CBT = "cbt"
    ACT = "act"
    DBT = "dbt"
    MI = "mi"
    SFBT = "sfbt"


class CaseCategory(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    BOUNDARY = "boundary"
    NEGATIVE = "negative"
    AMBIGUOUS = "ambiguous"
    MULTI_TURN = "multi_turn"
    LIFECYCLE = "lifecycle"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    BOUNDARY = "boundary"


class SafetyLevel(str, Enum):
    NORMAL = "normal"
    SAFETY_RELEVANT = "safety_relevant"
    CRISIS_CONFLICT = "crisis_conflict"


PSEUDO_FAMILIES = {"CRISIS", "EMOTION_SUPPORT", "DAILY"}
# canonical values; callers comparing .name should upper() first
THERAPY_FAMILIES = {f.value for f in TherapyFamily}


def valid_psuedo_family(value: str) -> bool:
    return value in PSEUDO_FAMILIES or value.upper() in PSEUDO_FAMILIES


def valid_skills_for_family(family: str) -> set:
    """Same mapping as routing_schema.valid_skills_for_family; therapy
    families have one primary skill each (pick_skill is code, so family is
    the primary assertion)."""
    upper = family.upper()
    if upper in PSEUDO_FAMILIES:
        f = upper
    else:
        f = family.lower()
    if f in TherapyFamily._value2member_map_:
        return {f}
    if f == "CRISIS":
        return {"crisis"}
    if f == "EMOTION_SUPPORT":
        return {"affect_labeling", "pmr", "grounding"}
    if f == "DAILY":
        return {"daily"}
    return set()


def is_family_or_dummy(value: str) -> bool:
    try:
        TherapyFamily(value.lower())
        return True
    except ValueError:
        pass
    return value.upper() in PSEUDO_FAMILIES


@dataclass
class TherapyCase:
    """Gold case for therapy-family evaluation.

    Pass semantics: a case passes when at least one of the following holds,
    as documented per category —
    - easy/medium/hard/boundary: actual family ∈ acceptable_families (when
      acceptable_families is non-empty); explicitly no cost zone else.
    - ambiguous: actual family ∈ acceptable_families (expected_family is None);
      pass = family choice within the acceptable set.
    - negative: expected_no_therapy=True — pass = NO therapy started;
      over-intervention (SwitchToTherapy) fails, ESO/daily are fine.
    - multi_turn: per-turn expectations (backfill: a shorter per-turn list
      carries its last value to later turns).
    - lifecycle: same turn-level assertions, but with explicit state/
      orchestration expectations recorded in rationale (see audit doc §4).
    """

    case_id: str
    text: Union[str, List[str]]
    expected_family: Optional[str] = None
    acceptable_families: Optional[List[str]] = None
    expected_skill: Optional[str] = None
    expected_intervention: Optional[str] = None  # technique enum value
    category: CaseCategory = CaseCategory.MEDIUM
    difficulty: Difficulty = Difficulty.MEDIUM
    safety: SafetyLevel = SafetyLevel.NORMAL
    boundary_pair: Optional[List[str]] = None
    rationale: str = ""
    expected_no_therapy: bool = False
    expected_family_by_turn: Optional[List[str]] = None
    expected_skill_by_turn: Optional[List[Optional[str]]] = None

    @property
    def turns(self) -> List[str]:
        if isinstance(self.text, list):
            return list(self.text)
        return [self.text]

    @property
    def is_multi_turn(self) -> bool:
        return isinstance(self.text, list)

    @property
    def fams(self) -> List[str]:
        """Acceptable families honoring the default-derivation rule."""
        if self.acceptable_families is not None:
            return list(self.acceptable_families)
        if self.expected_family is not None:
            return [self.expected_family]
        return []

    def family_at(self, index: int) -> Optional[str]:
        """Turn-wise family with last-value-carry (copied semantics from
        routing_schema.RoutingCase.expected_family_at, Optional-aware)."""
        if self.expected_family_by_turn:
            if index < len(self.expected_family_by_turn):
                return self.expected_family_by_turn[index]
            return self.expected_family_by_turn[-1]
        return self.expected_family

    def family_at_all(self, index: int) -> List[str]:
        if self.expected_family_by_turn:
            v = self.family_at(index)
            return [] if v is None else [v]
        return self.fams

    def skill_at(self, index: int) -> Optional[str]:
        if self.expected_skill_by_turn:
            if index < len(self.expected_skill_by_turn):
                return self.expected_skill_by_turn[index]
            return self.expected_skill_by_turn[-1]
        return self.expected_skill

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.case_id:
            errors.append("empty case_id")
        turns = self.turns
        if not turns or any(not t.strip() for t in turns):
            errors.append("empty or missing text")
        try:
            CaseCategory(self.category.value)
        except Exception:
            try:
                CaseCategory(self.category)
            except ValueError:
                errors.append(f"invalid category {self.category!r}")
        try:
            Difficulty(self.difficulty.value)
        except Exception:
            try:
                Difficulty(self.difficulty)
            except ValueError:
                errors.append(f"invalid difficulty {self.difficulty!r}")
        try:
            SafetyLevel(self.safety.value)
        except Exception:
            try:
                SafetyLevel(self.safety)
            except ValueError:
                errors.append(f"invalid safety level {self.safety!r}")
        if not self.rationale.strip():
            errors.append("missing rationale")
        # family values all in taxonomy (therapy or pseudo)
        for v in self.fams + [self.expected_family]:
            if v is None:
                continue
            if v not in PSEUDO_FAMILIES:
                try:
                    TherapyFamily(v)
                except ValueError:
                    errors.append(f"invalid family value {v!r}")
        # skill validity
        for fam_val, sk in (
            (self.expected_family, self.expected_skill),
        ):
            if sk is not None and fam_val is not None:
                if sk not in valid_skills_for_family(fam_val):
                    errors.append(
                        f"skill {sk!r} not valid for family {fam_val!r}"
                    )
        # acceptable superset of expected
        if self.expected_family is not None:
            if self.expected_family not in self.fams:
                errors.append(
                    "acceptable_families must contain expected_family"
                )
        # ambiguous rules
        if self.category == CaseCategory.AMBIGUOUS:
            if self.expected_family is not None:
                errors.append("ambiguous case must have expected_family=None")
            if len(self.fams) < 2:
                errors.append("ambiguous case needs >=2 acceptable_families")
        # boundary rules
        if self.category == CaseCategory.BOUNDARY:
            if not self.boundary_pair or len(self.boundary_pair) < 2:
                errors.append("boundary case requires boundary_pair of >=2 families")
            else:
                for fam in self.boundary_pair:
                    if fam not in PSEUDO_FAMILIES:
                        try:
                            TherapyFamily(fam)
                        except ValueError:
                            errors.append(f"invalid boundary_pair member {fam!r}")
        # negative rules
        if self.expected_no_therapy:
            if self.expected_family is not None:
                errors.append(
                    "expected_no_therapy case must have expected_family=None"
                )
            if self.fams:
                errors.append(
                    "expected_no_therapy case must have empty acceptable_families"
                )
            if self.category == CaseCategory.AMBIGUOUS:
                errors.append("expected_no_therapy conflicts with ambiguous")
        # crisis_conflict safety expectation
        if self.safety == SafetyLevel.CRISIS_CONFLICT:
            if turns and self.expected_family != "CRISIS" and \
                    self.family_at(0) != "CRISIS":
                errors.append(
                    "crisis_conflict case must expect family CRISIS"
                )
        # multi-turn rules
        if self.category == CaseCategory.MULTI_TURN and len(turns) < 2:
            errors.append("multi_turn case needs >=2 texts")
        if self.is_multi_turn:
            if len(turns) > 5:
                errors.append("multi-turn cases limited to 5 turns")
            for name, raw in (
                ("expected_family_by_turn", self.expected_family_by_turn),
                ("expected_skill_by_turn", self.expected_skill_by_turn),
            ):
                if raw is not None and len(raw) > len(turns):
                    errors.append(f"{name} longer than turn count")
            if self.expected_family_by_turn is None and self.expected_family is None \
                    and self.fams == [] and not self.expected_no_therapy:
                errors.append(
                    "multi-turn case needs turn-wise or scalar family expectation "
                    "(or expected_no_therapy)"
                )
            if self.expected_family_by_turn is not None and not self.expected_no_therapy:
                for v in self.expected_family_by_turn:
                    if v is not None and v not in PSEUDO_FAMILIES:
                        try:
                            TherapyFamily(v)
                        except ValueError:
                            errors.append(f"invalid per-turn family {v!r}")
            if self.expected_skill_by_turn is not None:
                for i, sk in enumerate(self.expected_skill_by_turn):
                    if sk is None:
                        continue
                    f = self.family_at(i)
                    if f is not None and sk not in valid_skills_for_family(f):
                        errors.append(
                            f"turn {i}: skill {sk!r} invalid for family {f!r}"
                        )
        if self.expected_intervention is not None:
            fam0 = self.family_at(0)
            is_therapy = False
            if fam0 is not None and fam0 not in PSEUDO_FAMILIES:
                try:
                    TherapyFamily(fam0)
                    is_therapy = True
                except ValueError:
                    pass
            if not is_therapy:
                errors.append(
                    "expected_intervention requires a therapy-family case "
                    f"(got {fam0!r})"
                )
        return errors


def _safe_enum(cls, value, default):
    try:
        return cls(value)
    except (ValueError, KeyError, TypeError):
        return default


def case_from_dict(d: dict) -> TherapyCase:
    cat_raw = d.get("category", "medium")
    try:
        cat = CaseCategory(cat_raw)
    except ValueError:
        cat = CaseCategory.MEDIUM  # keep raw invalid in field? report via validate
        cat = cat_raw  # validate() re-checks raw strings
    diff_raw = d.get("difficulty", "medium")
    try:
        diff = Difficulty(diff_raw)
    except ValueError:
        diff = diff_raw
    safe_raw = d.get("safety", "normal")
    try:
        safe = SafetyLevel(safe_raw)
    except ValueError:
        safe = safe_raw
    return TherapyCase(
        case_id=d["case_id"],
        text=d["text"],
        expected_family=d.get("expected_family"),
        acceptable_families=d.get("acceptable_families"),
        expected_skill=d.get("expected_skill"),
        expected_intervention=d.get("expected_intervention"),
        category=cat,
        difficulty=diff,
        safety=safe,
        boundary_pair=d.get("boundary_pair"),
        rationale=d.get("rationale", ""),
        expected_no_therapy=bool(d.get("expected_no_therapy", False)),
        expected_family_by_turn=d.get("expected_family_by_turn"),
        expected_skill_by_turn=d.get("expected_skill_by_turn"),
    )


def load_therapy_cases(path: Union[str, Path]) -> List[TherapyCase]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    raw = data["cases"] if isinstance(data, dict) else data
    return [case_from_dict(d) for d in raw]


# ---------------------------------------------------------------- test helpers (not tests)

# Mapped enum-class names → family, for dataset sanity checks (kept in sync
# manually with src/skills/techniques.py).
TECHNIQUE_ENUM_FAMILIES = {
    "DefusionTechnique": "act",
    "AcceptanceTechnique": "act",
    "PresentMomentTechnique": "act",
    "SelfAsContextTechnique": "act",
    "ValuesTechnique": "act",
    "CommittedActionTechnique": "act",
    "MindfulnessTechnique": "dbt",
    "DistressToleranceTechnique": "dbt",
    "EmotionRegulationTechnique": "dbt",
    "InterpersonalEffectivenessTechnique": "dbt",
    "MIExploreTechnique": "mi",
    "MIEvocationTechnique": "mi",
    "MICommitmentTechnique": "mi",
    "SFBTResourceTechnique": "sfbt",
    "SFBTExceptionTechnique": "sfbt",
    "SFBTActionTechnique": "sfbt",
}


def technique_family_for_enum(enum_name: str) -> Optional[str]:
    return TECHNIQUE_ENUM_FAMILIES.get(enum_name)


def schema_self_check() -> List[str]:
    """Taxonomy self-consistency checks shared by the validation test."""
    errors: List[str] = []
    assert len(TherapyFamily) == 5
    assert len(CaseCategory) == 7
    assert len(Difficulty) == 4
    assert len(SafetyLevel) == 3
    # pseudo-families disjoint from therapy families
    assert not (PSEUDO_FAMILIES & {f.value for f in TherapyFamily})
    # pseudo-families match the routing taxonomy strings
    assert PSEUDO_FAMILIES == {"CRISIS", "EMOTION_SUPPORT", "DAILY"}
    # skill partition mirrors the cached routing schema
    for fam in TherapyFamily:
        assert valid_skills_for_family(fam.value) == {fam.value}
    assert valid_skills_for_family("CRISIS") == {"crisis"}
    assert "grounding" in valid_skills_for_family("EMOTION_SUPPORT")
    return errors
