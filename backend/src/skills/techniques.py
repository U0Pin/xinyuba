"""Technique enums for all therapy families.

Each therapy process has a fixed set of techniques. Skill implementations
MUST select from these enums for technique.name — free-form strings are
not allowed, ensuring naming consistency across the system.
"""

from enum import Enum

# ── ACT ─────────────────────────────────────────────

class DefusionTechnique(str, Enum):
    """Techniques for Cognitive Defusion."""
    THOUGHT_LABELING = "thought_labeling"
    COGNITIVE_DEFUSION_EXERCISE = "cognitive_defusion_exercise"
    LEAVES_ON_STREAM = "leaves_on_stream"
    THANKING_THE_MIND = "thanking_the_mind"

class AcceptanceTechnique(str, Enum):
    """Techniques for Acceptance / Willingness."""
    WILLINGNESS_INVITATION = "willingness_invitation"
    EXPANSION_EXERCISE = "expansion_exercise"
    TUG_OF_WAR_METAPHOR = "tug_of_war_metaphor"

class PresentMomentTechnique(str, Enum):
    """Techniques for Present Moment Awareness."""
    BREATH_ANCHOR = "breath_anchor"
    FIVE_SENSES = "five_senses"
    BODY_SCAN = "body_scan"
    DROPPING_ANCHOR = "dropping_anchor"

class SelfAsContextTechnique(str, Enum):
    """Techniques for Self-as-Context / Observer Self."""
    OBSERVER_METAPHOR = "observer_metaphor"
    CHESSBOARD_METAPHOR = "chessboard_metaphor"
    SKY_AND_CLOUDS = "sky_and_clouds"

class ValuesTechnique(str, Enum):
    """Techniques for Values Clarification."""
    VALUES_EXPLORATION = "values_exploration"
    LIFE_COMPASS = "life_compass"
    BULLSEYE = "bullseye"

class CommittedActionTechnique(str, Enum):
    """Techniques for Committed Action."""
    ACTION_PLANNING = "action_planning"
    SMALL_STEPS = "small_steps"
    VALUES_CONSISTENCY_CHECK = "values_consistency_check"


# ── DBT ─────────────────────────────────────────────

class DBTProcess(str, Enum):
    """DBT core processes — used in recommended_processes[].process."""
    MINDFULNESS = "mindfulness"
    DISTRESS_TOLERANCE = "distress_tolerance"
    EMOTION_REGULATION = "emotion_regulation"
    INTERPERSONAL_EFFECTIVENESS = "interpersonal_effectiveness"

class MindfulnessTechnique(str, Enum):
    """Techniques for Mindfulness (Core Mindfulness Skills)."""
    WISE_MIND = "wise_mind"
    OBSERVE_DESCRIBE = "observe_describe"

class DistressToleranceTechnique(str, Enum):
    """Techniques for Distress Tolerance."""
    RADICAL_ACCEPTANCE = "radical_acceptance"
    SELF_SOOTHE = "self_soothe"

class EmotionRegulationTechnique(str, Enum):
    """Techniques for Emotion Regulation."""
    CHECK_THE_FACTS = "check_the_facts"
    OPPOSITE_ACTION = "opposite_action"

class InterpersonalEffectivenessTechnique(str, Enum):
    """Techniques for Interpersonal Effectiveness."""
    DEAR_MAN = "dear_man"
    GIVE = "GIVE"


# ── MI ─────────────────────────────────────────────

class MIProcess(str, Enum):
    """MI core processes — used in recommended_processes[].process."""
    EXPLORE = "explore"
    EVOCATION = "evocation"
    COMMITMENT = "commitment"

class MIExploreTechnique(str, Enum):
    """Techniques for MI Explore (engaging and focusing)."""
    OARS = "oars"
    AGENDA_MAPPING = "agenda_mapping"

class MIEvocationTechnique(str, Enum):
    """Techniques for MI Evocation (evoking change talk)."""
    DARN_CAT = "darn_cat"
    IMPORTANCE_CONFIDENCE_RULER = "importance_confidence_ruler"

class MICommitmentTechnique(str, Enum):
    """Techniques for MI Commitment (planning)."""
    CHANGE_PLAN = "change_plan"
    COMMITMENT_LANGUAGE = "commitment_language"


# ── SFBT ─────────────────────────────────────────────

class SFBTProcess(str, Enum):
    """SFBT core processes — used in recommended_processes[].process."""
    RESOURCE_EXPLORATION = "resource_exploration"
    EXCEPTION_EXPLORATION = "exception_exploration"
    FUTURE_CONSTRUCTION = "future_construction"

class SFBTResourceTechnique(str, Enum):
    """Techniques for SFBT Resource Exploration."""
    COPING_QUESTIONS = "coping_questions"
    COMPLIMENTS_STRENGTHS = "compliments_strengths"

class SFBTExceptionTechnique(str, Enum):
    """Techniques for SFBT Exception Exploration."""
    EXCEPTION_FINDING = "exception_finding"
    SCALING_QUESTIONS = "scaling_questions"

class SFBTActionTechnique(str, Enum):
    """Techniques for SFBT Future Construction."""
    MIRACLE_QUESTION = "miracle_question"
    SMALL_STEP = "small_step"
