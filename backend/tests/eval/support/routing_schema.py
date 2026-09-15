"""Routing taxonomy and evaluation case schema.

Four routing families (matching the audited pipeline in docs/ROUTING_EVALUATION.md):

- CRISIS: safety short-circuits everything at DecisionLine.
- THERAPY: TherapyDecider -> one of cbt/act/dbt/mi/sfbt.
- EMOTION_SUPPORT: ESO -> one of affect_labeling / grounding / pmr
  (priority PMR(20) > Grounding(15) > Affect(10)).
- DAILY: implicit — no explicit daily record exists in the pipeline; the
  expected outcome is the absence of any intervention signal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional, Union


class RoutingFamily(str, Enum):
    CRISIS = "crisis"
    THERAPY = "therapy"
    EMOTION_SUPPORT = "emotion_support"
    DAILY = "daily"


class TherapySkill(str, Enum):
    CBT = "cbt"
    ACT = "act"
    DBT = "dbt"
    MI = "mi"
    SFBT = "sfbt"


class EmotionSupportSkill(str, Enum):
    AFFECT_LABELING = "affect_labeling"
    PMR = "pmr"
    GROUNDING = "grounding"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    BOUNDARY = "boundary"
    NEGATIVE = "negative"


LIFECYCLE_STAGES = {
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

OVERRIDE_SKILLS = {
    EmotionSupportSkill.PMR.value,
    EmotionSupportSkill.GROUNDING.value,
    EmotionSupportSkill.AFFECT_LABELING.value,
    TherapySkill.CBT.value,
    TherapySkill.ACT.value,
    TherapySkill.DBT.value,
    TherapySkill.MI.value,
    TherapySkill.SFBT.value,
}


def valid_skills_for_family(family: RoutingFamily) -> set:
    """Skills that are legal for a given family (None always allowed for
    family-level / ambiguous assertions)."""
    if family == RoutingFamily.THERAPY:
        return {s.value for s in TherapySkill}
    if family == RoutingFamily.EMOTION_SUPPORT:
        return {s.value for s in EmotionSupportSkill}
    if family == RoutingFamily.CRISIS:
        return {"crisis"}
    if family == RoutingFamily.DAILY:
        return {"daily"}
    return set()


@dataclass
class RoutingCase:
    case_id: str
    text: Union[str, List[str]]  # single turn or 2-5 turns
    expected_family: RoutingFamily  # scalar, or turn-0 family for multi-turn
    expected_skill: Optional[str] = None  # None = family-level assertion only
    difficulty: Difficulty = Difficulty.MEDIUM
    category: str = "general"
    rationale: str = ""
    safety_relevant: bool = False
    boundary_case: bool = False
    active_before: Optional[str] = None  # skill assumed active (override matrix)
    lifecycle_stage: Optional[str] = None  # lifecycle evaluation marker
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

    def expected_family_at(self, index: int) -> RoutingFamily:
        """Family expectation for turn index, honoring per-turn lists with
        last-value-carry semantics."""
        if self.expected_family_by_turn and index < len(self.expected_family_by_turn):
            return RoutingFamily(self.expected_family_by_turn[index])
        if self.expected_family_by_turn:
            return RoutingFamily(self.expected_family_by_turn[-1])
        if index == 0:
            return self.expected_family
        return self.expected_family

    def expected_skill_at(self, index: int) -> Optional[str]:
        """Skill expectation for turn index (last-value-carry)."""
        if self.expected_skill_by_turn and index < len(self.expected_skill_by_turn):
            return self.expected_skill_by_turn[index]
        if self.expected_skill_by_turn:
            return self.expected_skill_by_turn[-1]
        return self.expected_skill

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.case_id:
            errors.append("empty case_id")
        turns = self.turns
        if not turns or any(not t.strip() for t in turns):
            errors.append("empty or missing text")
        if self.is_multi_turn and len(turns) > 5:
            errors.append("multi-turn cases limited to 5 turns")
        try:
            RoutingFamily(self.expected_family)
        except ValueError:
            errors.append(f"invalid expected_family {self.expected_family!r}")
        fam = RoutingFamily(self.expected_family)
        skill = self.expected_skill
        if skill is not None and skill not in valid_skills_for_family(fam):
            errors.append(
                f"skill {skill!r} not valid for family {fam.value!r}"
            )
        try:
            Difficulty(self.difficulty)
        except ValueError:
            errors.append(f"invalid difficulty {self.difficulty!r}")
        if not self.rationale.strip():
            errors.append("missing rationale")
        for name, raw in (
            ("expected_family_by_turn", self.expected_family_by_turn),
            ("expected_skill_by_turn", self.expected_skill_by_turn),
        ):
            if raw is not None and self.is_multi_turn and len(raw) > len(turns):
                errors.append(f"{name} longer than turn count")
        if self.active_before is not None and self.active_before not in OVERRIDE_SKILLS:
            errors.append(f"invalid active_before {self.active_before!r}")
        if self.lifecycle_stage is not None and self.lifecycle_stage not in LIFECYCLE_STAGES:
            errors.append(f"invalid lifecycle_stage {self.lifecycle_stage!r}")
        if self.is_multi_turn and self.expected_skill_by_turn:
            for i, sk in enumerate(self.expected_skill_by_turn):
                if sk is None:
                    continue
                f = self.expected_family_at(i)
                if sk not in valid_skills_for_family(f):
                    errors.append(f"turn {i}: skill {sk!r} invalid for family {f.value!r}")
        return errors


def case_from_dict(d: dict) -> RoutingCase:
    return RoutingCase(
        case_id=d["case_id"],
        text=d["text"],
        expected_family=RoutingFamily(d["expected_family"]),
        expected_skill=d.get("expected_skill"),
        difficulty=Difficulty(d.get("difficulty", "medium")),
        category=d.get("category", "general"),
        rationale=d.get("rationale", ""),
        safety_relevant=bool(d.get("safety_relevant", False)),
        boundary_case=bool(d.get("boundary_case", False)),
        active_before=d.get("active_before"),
        lifecycle_stage=d.get("lifecycle_stage"),
        expected_family_by_turn=d.get("expected_family_by_turn"),
        expected_skill_by_turn=d.get("expected_skill_by_turn"),
    )


def load_cases(path: Union[str, Path]) -> List[RoutingCase]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [case_from_dict(d) for d in data["cases"]] if isinstance(data, dict) else [
        case_from_dict(d) for d in data
    ]


# ---------------------------------------------------------------- test helpers (not tests)

def schema_self_check() -> List[str]:
    """Taxonomy self-consistency checks shared by the validation test."""
    errors: List[str] = []
    # Every family must have a legal-skill set (possibly empty for daily's None default).
    for fam in RoutingFamily:
        valid_skills_for_family(fam)
    # ESO priority: pmr > grounding > affect (mirrors emotion_support SPECS).
    prio = {
        EmotionSupportSkill.PMR: 20,
        EmotionSupportSkill.GROUNDING: 15,
        EmotionSupportSkill.AFFECT_LABELING: 10,
    }
    assert prio[EmotionSupportSkill.PMR] > prio[EmotionSupportSkill.GROUNDING]
    assert prio[EmotionSupportSkill.GROUNDING] > prio[EmotionSupportSkill.AFFECT_LABELING]
    # DAILY's only prescribed skill label is `daily` (or None).
    assert valid_skills_for_family(RoutingFamily.DAILY) == {"daily"}
    assert valid_skills_for_family(RoutingFamily.CRISIS) == {"crisis"}
    # Therapy skills distinct from ES skills.
    assert not (set(TherapySkill) & set(EmotionSupportSkill))
    return errors
