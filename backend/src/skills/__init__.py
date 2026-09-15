"""Therapy skills package.

Importing this package registers every therapy skill (all five families)
with the global skill_registry. Entry points therefore only need:

    import src.skills  # noqa: F401

Family modules:
    cbt.py / act.py / dbt.py / mi.py / sfbt.py

Supporting modules:
    affect_lexicon.py — 情绪表达词库（Affect Labeling gate 的纯代码线索表）
    techniques.py     — technique enums for all families

疗法的家族构成（技能归属、评估技能、流程）以各疗法 Agent 的
FLOW/BRANCHES/assessment_skill 为唯一权威（见 agents/therapy/，
一致性由 tests/test_therapy_selection.py 守护）。
"""

from src.skills import cbt    # noqa: F401 — registers CBT skills
from src.skills import act    # noqa: F401 — registers ACT skills
from src.skills import dbt    # noqa: F401 — registers DBT skills
from src.skills import mi     # noqa: F401 — registers MI skills
from src.skills import sfbt   # noqa: F401 — registers SFBT skills
from src.skills import affect_labeling  # noqa: F401 — registers emotion-support skills
from src.skills import pmr  # noqa: F401 — registers PMR emotion-support skills
from src.skills import grounding  # noqa: F401 — registers Grounding emotion-support skills
