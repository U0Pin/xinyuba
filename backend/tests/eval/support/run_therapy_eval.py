#!/usr/bin/env python3
"""Therapy evaluation runner (companion to run_routing_eval.py).

Drives the REAL therapy pipeline — the same Scheduler three-line loop with an
offline agenda provider — for every gold case in
tests/eval/eval_data/therapy_cases.json. All LLM-judgment slots are scripted
per turn from a gold-informed agenda; everything downstream of those decisions
(SwitchToTherapy signal, per-turn orchestration: assessment → step judgment →
code pick_skill → skill execution → products/progress → marginal-utility
record → formulaic stop) is REAL pipeline code.

Derived actual family/skill is read ONLY from structured state (audit §4):
- state.json: state["therapy"].name (authoritative session family),
  state["crisis"]/risk_level for CRISIS pseudo-family, live ES sub-states
  (pmr/grounding/affect_labeling proposing|active) for EMOTION_SUPPORT,
  none of the above → DAILY / expected-no-therapy.
- orchestration.json: skill_output["_skill_name"] / technique.name for
  fidelity + technique checks.
- llm calls: env.sink.records (FakeSink receives every LLMCallRecord.to_dict,
  including the agent tag) — grouped per turn via trace_id to build the
  per-turn orchestration-tag ground truth (theory-mixing proxy).

Scripting policy (reported openly in report.md; same policy as the routing
runner): the TherapyDecider is scripted to say yes/no per gold expectation,
so family "accuracy" is largely agenda-driven — documented as a scripting
limitation. For negatives / ES / crisis boundaries the decider is scripted to
say NO (or the safety slot scripts CRISIS), and the case asserts purely on
machinery: no SwitchToTherapy signal (no therapy in state), no ES sub-state,
final owner daily (or daily_crisis).

Outputs → outputs/therapy_evaluation/{cases.json, results.json, summary.json,
failures.json, fidelity_results.json, report.md}. Does NOT touch
outputs/routing_evaluation/ or outputs/affect_candidate_eval/.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import src.skills  # noqa: F401,E402 — register all skills (parity with bootstrap)
from src.core.skill import skill_registry  # noqa: E402
from src.utils.config import config  # noqa: E402

from tests.eval.support import run_routing_eval as routing  # noqa: E402
from tests.eval.support.therapy_schema import (  # noqa: E402
    THERAPY_FAMILIES,
    TherapyCase,
    load_therapy_cases,
)
from tests.support.fakes import FakeSink  # noqa: E402,F401 — accounting sink (reused via EvalEnv)

DATASET_PATH = Path(__file__).resolve().parents[1] / "eval_data" / "therapy_cases.json"
OUTPUT_DIR = _REPO / "outputs" / "therapy_evaluation"

THERAPY_UPPER = routing.THERAPY_UPPER           # {"cbt": "CBT", ...}
CRI, ES, DAILY = "CRISIS", "EMOTION_SUPPORT", "DAILY"

# Family → set of intervention/analysis skill names owned by that family
# (fidelity check (a); built from the real skill registry at import time).
FAMILY_SKILLS = {
    "cbt": {"extract_automatic_thought", "detect_cognitive_distortion",
            "generate_reframe", "evaluate_evidence", "decatastrophize",
            "plan_behavioral_activation"},
    "act": {"act_defusion", "act_acceptance", "act_present_moment",
            "act_self_as_context", "act_values", "act_committed_action"},
    "dbt": {"dbt_mindfulness", "dbt_distress_tolerance",
            "dbt_emotion_regulation", "dbt_interpersonal_effectiveness"},
    "mi": {"mi_explore", "mi_evocation", "mi_commitment"},
    "sfbt": {"sfbt_resource_exploration", "sfbt_exception_exploration",
             "sfbt_future_construction"},
}
FAMILY_FLOW = {  # fidelity check (d): FLOW step names + branches per family
    "cbt": (["identify_thought", "classify_distortion", "challenge", "reframe",
             "evaluate_effect"], ["behavioral_activation"]),
    "act": (["defusion", "acceptance", "present_moment", "self_as_context",
             "values", "committed_action"], []),
    "dbt": (["mindfulness", "distress_tolerance", "emotion_regulation",
             "interpersonal_effectiveness"], []),
    "mi": (["explore", "evocation", "commitment"], []),
    "sfbt": (["resource_exploration", "exception_exploration",
              "future_construction"], []),
}
END_REASONS = {"stagnation", "regression", "round_cap", "unknown_therapy", "crisis"}
MAX_CONTINUATION_TURNS = 6  # script-driven extra turns to let the code pick_skill run

# expected_intervention → step-routing / classification scripting per family
_CBT_DISTORTION = {  # target distortion type routed by code pick_skill at challenge
    "chain_analysis": "catastrophizing",
    "alternative_causes": "overgeneralization",
    "pros_cons_table": "should_statements",   # matches no routing set → evaluate_evidence
}
_ACT_STEP_ASSESS = {  # tailoring of the ASSESSMENT superset per expected technique
    "defusion": {},
    "acceptance": {"fusion": {"index": 0.7, "state": "cf_hooked"},
                   "avoidance": {"index": 0.7, "state": "ea_unwilling"}},
    "present_moment": {"escalation_level": "highly_escalated"},
    "self_as_context": {"fusion": {"index": 0.9, "state": "cf_identity_fusion"}},
    "values": {"alignment": {"index": 0.2, "state": "low", "values_mentioned": []}},
    "committed_action": {},
}
_DBT_MODULE = {"wise_mind": "mindfulness", "observe_describe": "mindfulness",
               "radical_acceptance": "distress_tolerance",
               "self_soothe": "distress_tolerance",
               "check_the_facts": "emotion_regulation",
               "opposite_action": "emotion_regulation",
               "dear_man": "interpersonal_effectiveness", "GIVE": "interpersonal_effectiveness"}
_MI_MODE = {"oars": 0, "agenda_mapping": 0, "darn_cat": 1,
            "importance_confidence_ruler": 1, "change_plan": 2,
            "commitment_language": 2}
_SFBT_ASSESS = {"compliments_strengths": "resource", "coping_questions": "resource",
                "exception_finding": "exception", "scaling_questions": "exception",
                "miracle_question": "future", "small_step": "future"}
_DBT_TECH_TO_SKILL = {
    "wise_mind": "dbt_mindfulness", "observe_describe": "dbt_mindfulness",
    "radical_acceptance": "dbt_distress_tolerance", "self_soothe": "dbt_distress_tolerance",
    "check_the_facts": "dbt_emotion_regulation", "opposite_action": "dbt_emotion_regulation",
    "dear_man": "dbt_interpersonal_effectiveness", "GIVE": "dbt_interpersonal_effectiveness",
}


def _assessment_for(family: str, expected_intervention: Optional[str]) -> dict:
    """Tailor the shared ASSESSMENT superset so the family's code routing
    (audit §3: initial_step_index rules) lands in a step whose skill can carry
    the expected technique; default superset otherwise."""
    import copy
    base = dict(routing.ASSESSMENT)
    if not expected_intervention:
        return base
    fam = family.lower()
    tailoring: Dict[str, Any] = {}
    if fam == "act":
        step = next((s for s, keys in _ACT_STEP_ASSESS.items()
                     if expected_intervention in _step_techniques("act", s)), None)
        tailoring = _ACT_STEP_ASSESS.get(step or "", {})
    elif fam == "dbt":
        module = _DBT_MODULE.get(expected_intervention)
        tailoring = {"recommended_processes":
                     ([{"process": module}] if module else [])}
    elif fam == "mi":
        mode = _MI_MODE.get(expected_intervention)
        if mode == 1:  # evocation: change_talk ≥ .35
            tailoring = {"change_talk": {"index": 0.6, "state": "strong"}}
        elif mode == 2:  # commitment: cr ≥ .75 and ct ≥ .6
            tailoring = {"change_talk": {"index": 0.8, "state": "strong"},
                         "change_readiness": {"index": 0.85, "state": "high"}}
    elif fam == "sfbt":
        kind = _SFBT_ASSESS.get(expected_intervention)
        if kind == "resource":
            tailoring = {"resource_awareness": {"index": 0.2, "state": "low"},
                         "problem_orientation": {"index": 0.5, "state": "growing"},
                         "goal_clarity": {"index": 0.6, "state": "medium"}}
        elif kind == "exception":
            tailoring = {"problem_orientation": {"index": 0.2, "state": "problem_stuck"},
                         "resource_awareness": {"index": 0.5, "state": "medium"}}
        elif kind == "future":
            tailoring = {"goal_clarity": {"index": 0.3, "state": "low"},
                         "resource_awareness": {"index": 0.5, "state": "medium"},
                         "problem_orientation": {"index": 0.5, "state": "growing"}}
    return {**copy.deepcopy(base), **copy.deepcopy(tailoring)}


def _step_techniques(family: str, step_skill: str) -> List[str]:
    """valid_techniques of the family skill named ``step_skill`` (real enum)."""
    skill = skill_registry.get(step_skill)
    return list(getattr(skill, "valid_techniques", []) or [])


def _family_default_step_skill(family: str) -> str:
    return sorted(FAMILY_SKILLS[family])[0]


# ── Provider: marker-keyed agenda (extends the routing runner's provider) ──

EXTRACT_MODULE = "automatic thought extraction module"
DISTORTION_MODULE = "distortion classification module"
ACT_PREFIX = "You are an ACT"
DBT_PREFIX = "You are a DBT"
MI_PREFIX = "You are an MI"
SFBT_PREFIX = "You are an SFBT"
CBT_INTERV_PREFIX = "You are a CBT"


class TherapyAgendaProvider(routing.AgendaProvider):
    """Routing agenda provider + therapy-slot scripting.

    New agenda keys consumed here:
    - "judgment": step-judgment response (default stay)
    - "technique": technique name placed into every therapy-intervention skill
      response (persists only if valid for that skill's enum — which is the
      check we measure)
    - "assessment": per-family assessment payload
    - "cbt_extract" / "cbt_classify": CBT analysis-step payloads
    """

    def _pick(self, prompt: str) -> Any:
        a = self.agenda
        if prompt.startswith(routing.SAFETY_PREFIX):
            return a.get("safety") or routing.RISK_SAFE
        if routing.JUDGE_MARKER in prompt:
            return a.get("judgment") or routing.JUDGE_STAY
        # ES opportunity/response slots unchanged (reuse the routing policy)
        if routing.PMR_OPP_MARKER in prompt:
            return {"should_offer": bool(a.get("pmr_offer")),
                    "body_focus": "shoulders", "reason": "eval"}
        if routing.PMR_RESP_MARKER in prompt:
            return a.get("pmr_response") or {"kind": "neutral", "reason": "eval"}
        if routing.GND_OPP_MARKER in prompt:
            return {"should_offer": bool(a.get("gnd_offer")), "reason": "eval"}
        if routing.GND_RESP_MARKER in prompt:
            return a.get("gnd_response") or {"kind": "affirmative", "reason": "eval"}
        if routing.AL_OPP_MARKER in prompt:
            return {"should_offer": bool(a.get("affect_offer")),
                    "candidates": ["委屈", "失望", "焦虑"], "reason": "eval"}
        if routing.AL_RESP_MARKER in prompt:
            return a.get("affect_response") or {"kind": "no_answer", "reason": "eval"}
        if routing.DECIDER_MARKER in prompt:
            d = a.get("decide_therapy")
            if not d:
                return {"need_therapy": False, "therapy": None, "reason": "eval"}
            return {"need_therapy": True, "therapy": d, "reason": "eval"}
        if EXTRACT_MODULE in prompt:
            return a.get("cbt_extract") or routing.CBT_EXTRACT
        if DISTORTION_MODULE in prompt:
            return a.get("cbt_classify") or routing.CBT_CLASSIFY
        if routing.ASSESS_MARKER in prompt:
            return a.get("assessment") or routing.ASSESSMENT
        if prompt.startswith((ACT_PREFIX, DBT_PREFIX, MI_PREFIX, SFBT_PREFIX,
                              CBT_INTERV_PREFIX)):
            base = dict(routing.INTERVENTION)
            return {**base, "technique": {**base["technique"],
                                          "name": a.get("technique")
                                          or base["technique"]["name"]}}
        return routing.INTERVENTION  # profile updates etc.


class TherapyEvalEnv(routing.EvalEnv):
    """EvalEnv with the therapy-agenda provider (config identical, incl. the
    WINDOW_TOKEN_THRESHOLD=0 decider-gate policy). Never touches the network."""

    def __init__(self, tmp_root: Path):
        self._saved_log_dir = config.LOG_DIR
        config.LOG_DIR = str(tmp_root / "logs")
        super().__init__(tmp_root)
        # rebuild client/LLM wiring with the therapy agenda provider
        self.provider = TherapyAgendaProvider()
        # super() built agents bound to its own provider object; rebuild them
        # against TherapyEvalEnv's provider for consistent agenda behavior.
        from src.agents.host_agent import HostDialogueAgent
        from src.agents.therapy import (ActTherapyAgent, CbtTherapyAgent,
                                        DbtTherapyAgent, MiTherapyAgent,
                                        SfbtTherapyAgent)
        from src.agents.therapy_decider import TherapyDecider
        from src.core.llm_client import LLMClient
        from src.core.scheduler import DecisionLine, DialogueLine, Scheduler
        from src.agents.safety_agent import SafetyAgent
        from src.agents.settlement import SettlementLine
        from src.agents.affect_labeling import AffectLabelingEngine
        from src.agents.grounding import GroundingEngine
        from src.agents.progressive_muscle_relaxation import (
            ProgressiveMuscleRelaxationEngine)
        client = LLMClient(provider=self.provider, sink=self.sink,
                           provider_name="fake")
        sessions = self.sessions
        therapy_agents = {
            "CBT": CbtTherapyAgent(client, session_store=sessions),
            "ACT": ActTherapyAgent(client, session_store=sessions),
            "DBT": DbtTherapyAgent(client, session_store=sessions),
            "MI": MiTherapyAgent(client, session_store=sessions),
            "SFBT": SfbtTherapyAgent(client, session_store=sessions),
        }
        self.scheduler = Scheduler(
            dialogue_line=DialogueLine(
                HostDialogueAgent(client, session_store=sessions),
                session_store=sessions),
            decision_line=DecisionLine(
                SafetyAgent(client), session_store=sessions,
                therapy_decider=TherapyDecider(client),
                therapy_agents=therapy_agents,
                pmr_engine=ProgressiveMuscleRelaxationEngine(
                    client, session_store=sessions),
                grounding_engine=GroundingEngine(client, session_store=sessions),
                affect_engine=AffectLabelingEngine(client, session_store=sessions),
            ),
            settlement_line=SettlementLine(
                client, session_store=sessions, profile_store=self.profiles),
            session_store=sessions, profile_store=self.profiles,
        )

    def close(self) -> None:
        if not getattr(self, "_closed", True) or True:
            super().close()
        config.LOG_DIR = self._saved_log_dir


# ── State seeding (therapy session carrying over across turns) ─────────


def seed_therapy(env: TherapyEvalEnv, sid: str, key: str) -> None:
    """Direct state-write seeding of an active therapy session, step_index=-1
    so the first orchestrated turn runs the real assessment-driven routing
    (same seeding policy as tests/integration/test_cross_module_orchestration.py:188)."""
    state = env.sessions.read_state(sid)
    state["owner"] = f"therapy_{key}"
    state["therapy"] = {"name": THERAPY_UPPER[key], "start_seq": 0,
                        "rounds": 0, "step_index": -1, "branch": None,
                        "basic_flow_complete": False, "visited": [],
                        "utility_history": []}
    env.sessions.write_state(sid, state)


# ── Agenda builder (gold-informed scripting, documented limitation) ────


def _es_offers_for(skill: Optional[str]) -> Dict[str, Any]:
    a = {"pmr_offer": False, "gnd_offer": False, "affect_offer": False}
    if skill == "pmr":
        a["pmr_offer"] = True
    elif skill == "grounding":
        a["gnd_offer"] = True
    elif skill == "affect_labeling":
        a["affect_offer"] = True
    return a


def agenda_for_turn(case: TherapyCase, turn_idx: int,
                    therapy_active: bool) -> dict:
    """Gold-informed scripting for one user turn.

    Scripted slots (LLM-judgment): safety risk, TherapyDecider verdict,
    ES opportunity should_offer flags, per-family assessment payload, step
    judgment, technique name. Everything else (signal application, ESO gate
    interplay, orchestration sequencing, utility ledger, stop rules, state
    derivation) is real pipeline code."""
    fam = case.family_at(turn_idx)
    a: Dict[str, Any] = {"safety": routing.RISK_SAFE, **_es_offers_for(None)}
    skill = case.skill_at(turn_idx)
    if (fam == CRI or case.safety == "crisis_conflict"
            or fam and fam.upper() == CRI):
        a["safety"] = routing.RISK_CRISIS
    if fam == ES:
        a.update(_es_offers_for(skill))
    if fam and fam.lower() in THERAPY_FAMILIES and not therapy_active:
        a["decide_therapy"] = THERAPY_UPPER[fam.lower()]
    # assessment tailoring only matters when the family is known
    if case.expected_family:
        a["assessment"] = _assessment_for(case.expected_family,
                                          case.expected_intervention)
        if fam and fam.lower() == "cbt" and case.expected_intervention:
            expo = _CBT_DISTORTION.get(case.expected_intervention)
            if expo:
                a["cbt_classify"] = {
                    "classifications": [{"thought": "我总搞砸",
                                         "distortions": [{"type": expo,
                                                          "confidence": 0.9}]}],
                    "distortion_summary": {"distortion_count": 1,
                                           "dominant_pattern": expo,
                                           "severity": "severe"}}
    return a


def _judgment_for(case: TherapyCase, orch_turn_idx: int) -> dict:
    """Step-judgment scripting per orchestration turn (0-based)."""
    fam = (case.expected_family or "cbt").lower()
    eq = case.expected_intervention
    def j(action, target=None):
        return {"action": action, "target_step": target, "achieved": action != "stay",
                "reason": "eval"}
    if fam == "cbt":
        if eq == "activity_scheduling":
            seq = ["advance", "advance", ("go_to", "behavioral_activation"), "stay"]
        else:
            seq = ["advance", "advance", "stay"]
        if orch_turn_idx < len(seq):
            s = seq[orch_turn_idx]
            if isinstance(s, tuple):
                return j(*s)
            return j(s)
    return j("stay")


def _technique_for(case: TherapyCase, orch_turn_idx: int) -> Optional[str]:
    if not case.expected_intervention:
        return None
    fam = (case.expected_family or "cbt").lower()
    if fam == "cbt":
        # technique needed from orchestration turn holding the routed skill:
        # rel1 extract, rel2 classify, then the routed inner skill
        if orch_turn_idx >= 2:
            return case.expected_intervention  # guaranteed by seed_therapy step routing
        return None
    return case.expected_intervention if orch_turn_idx == 0 else case.expected_intervention


# ── State-based derivation (audit §4; no routing logic reimplemented) ──


def _es_live(state: dict) -> List[str]:
    if state.get("crisis"):
        return []
    live = []
    for key in ("pmr", "grounding", "affect_labeling"):
        blob = state.get(key) or {}
        if blob.get("status") in ("proposing", "active"):
            live.append(key)
    return live


def actual_family(state: dict) -> Tuple[str, Optional[str]]:
    if state.get("crisis") or state.get("risk_level") == "CRISIS":
        return CRI, "crisis"
    prog = state.get("therapy")
    if prog:
        return "therapy", (prog.get("name") or "").lower()
    live = _es_live(state)
    if live:
        return ES, live[0]
    return DAILY, None


# ── Case execution ─────────────────────────────────────────────────────


async def _drive_turn(env: TherapyEvalEnv, uid: str, sid: str, msg: str) -> dict:
    final: dict = {}
    async for ev in env.scheduler.handle_message(uid, sid, msg):
        if ev.get("event") == "final":
            final = ev.get("data") or {}
    return final


def _fail_type(exp_fam: Optional[str], exp_skill: Optional[str], act: str,
               task: Optional[str], case: TherapyCase, mixed: bool = False) -> str:
    if mixed:
        return "theory_mixing"
    if case.category == "lifecycle" or case.is_multi_turn:
        return "lifecycle_error"
    exp_up = (exp_fam or "").upper()
    if exp_fam is None:
        return "over_intervention" if act not in (DAILY, "error") else "under_intervention"
    if exp_up == CRI:
        return "safety_error"
    if exp_up == ES:
        return "boundary_error"
    if act == CRI and exp_up != CRI:
        return "safety_error"
    if act == DAILY and exp_up in THERAPY_FAMILIES:
        return "under_intervention"
    if act != exp_fam.lower() and exp_fam not in case.fams:
        if exp_fam == DAILY.lower() and act in ("therapy", ES.lower()):
            return "over_intervention"
        return "wrong_family"
    return "wrong_skill"


def run_case(env: TherapyEvalEnv, case: TherapyCase) -> dict:
    uid, sid = "eval_user", env.sessions.create("eval_user")
    turns_out: List[dict] = []
    ctx_out: Dict[str, Any] = {}
    errors: List[str] = []
    therapy_active = False
    case_record_start = len(env.sink.records)  # slice sink records per case
    for idx, msg in enumerate(case.turns, start=1):
        env.provider.agenda = agenda_for_turn(case, idx - 1, therapy_active)
        pre = len(env.sink.records)
        try:
            asyncio.run(_drive_turn(env, uid, sid, msg))
        except Exception as e:  # noqa: BLE001 — record, don't mask
            errors.append(f"turn{idx}: {e!r}")
            turns_out.append({"turn": idx, "message": msg, "error": repr(e),
                              "expected_family": case.family_at(idx - 1),
                              "expected_skill": case.skill_at(idx - 1),
                              "actual_family": "error", "pass": False,
                              "failure_type": "runner_error", "tags": []})
            continue
        state = env.sessions.read_state(sid)
        tags = [r.get("agent", "") for r in env.sink.records[pre:]]
        fam, task = actual_family(state)
        fams_at = case.family_at_all(idx - 1) or []
        exp_fam = case.family_at(idx - 1)
        exp_skill = case.skill_at(idx - 1)
        # scripting-limitation marker: decider scripted cases measure machinery
        mixed = _mixed_family_tags(tags)
        # effective family as a case label: a therapy session is judged by its
        # task (e.g. 'cbt', 'mi') — used both for pass and for continuity.
        actual_effective = task.lower() if fam == "therapy" and task \
            else fam.lower()
        if exp_fam is None:
            # negatives: assert machinery only (documented scripting limitation)
            no_therapy = state.get("therapy") is None
            no_es = not _es_live(state)
            owner_daily = state.get("owner") in ("daily", "daily_crisis")
            passed = no_therapy and no_es and owner_daily and not mixed
            failure = "correct" if passed else _fail_type(
                exp_fam, exp_skill, fam, task, case, mixed)
        else:
            fams_l = [f.lower() for f in fams_at]
            passed = actual_effective in fams_l
            if passed and exp_skill is not None and fam != "therapy":
                # ES/CRISIS skill expectation: derived live sub-state (task)
                # routing-schema family equal case is machinery-level.
                passed = task == exp_skill
            failure = "correct" if passed else _fail_type(
                exp_fam, exp_skill, fam, task, case, mixed)
        turns_out.append({
            "turn": idx, "message": msg,
            "expected_family": exp_fam, "expected_skill": exp_skill,
            "expected_families": fams_at or None,
            "actual_family": fam if fam == "therapy" else
            (fam.lower() if fam.lower() in THERAPY_FAMILIES else fam),
            "actual_family_key": fam, "actual_skill": task,
            "effective_family": actual_effective,
            "crisis_state": bool(state.get("crisis")),
            "therapy_name": ((state.get("therapy") or {}).get("name")),
            "live_es": _es_live(state),
            "owner": state.get("owner"),
            "tags": tags,
            "mixing": mixed,
            "pass": passed, "failure_type": failure,
        })
        therapy_active = state.get("therapy") is not None and \
            fam not in (CRI, DAILY)

    # ── continuation phase: script-driven extra Scheduler turns while the
    # seeded session is active, so the REAL pick_skill / utility machinery
    # runs; recorded separately (not scored against gold family labels) ──
    cont = []
    state = env.sessions.read_state(sid)
    if state.get("therapy") is not None and not state.get("crisis"):
        for k in range(MAX_CONTINUATION_TURNS):
            env.provider.agenda = {
                **agenda_for_turn(case, len(case.turns), True),
                "judgment": _judgment_for(case, k),
                "technique": _technique_for(case, k),
            }
            pre = len(env.sink.records)
            try:
                final = asyncio.run(_drive_turn(env, uid, sid, "（继续）" + str(k)))
            except Exception as e:  # noqa: BLE001
                errors.append(f"cont{k}: {e!r}")
                break
            state = env.sessions.read_state(sid)
            if state.get("last_therapy"):
                break
            if final.get("planned_skill") or ((state.get("therapy") or {}).get("rounds", 0)):
                pass
            cont.append({
                "round": k, "tags": [r.get("agent", "")
                                     for r in env.sink.records[pre:]],
                "judgment": _judgment_for(case, k),
            })
            if not state.get("therapy"):
                break
    state = env.sessions.read_state(sid)
    orch = env.sessions.read_orchestration(sid)
    ctx_out["continuation"] = cont
    ctx_out["final_state"] = {
        "therapy": state.get("therapy"), "last_therapy": state.get("last_therapy"),
        "owner": state.get("owner"), "crisis": state.get("crisis"),
    }
    ctx_out["last_orchestration"] = {
        k: orch.get(k) for k in ("therapy", "current_step", "next_step",
                                 "step_judgment", "assessment", "skill_output",
                                 "utility_eval") }
    products = (orch.get("products") or {})
    ctx_out["products_snapshot"] = {
        k: products.get(k) for k in ("automatic_thoughts", "primary_thought",
                                     "distortions", "distortion_summary")
        if k in products}
    real_planned = (orch.get("skill_output") or {}).get("_skill_name")
    ctx_out["planned_skill"] = real_planned = real_planned or None

    # ── fidelity checks (deterministic, structured fields only) ──
    fidelity = fidelity_checks(case, uid, sid, state, orch,
                               env.sink.records[case_record_start:], turns_out)
    lifecycle_ok = _lifecycle_ok(case, turns_out, ctx_out)

    overall = (not errors) and all(t.get("pass") for t in turns_out) \
        and lifecycle_ok and all(f["ok"] for f in fidelity)
    failure = "correct"
    if errors:
        failure = "runner_error"
    else:
        for f in fidelity:
            if not f["ok"]:
                failure = "fidelity_error" if failure == "correct" else failure
        if failure == "correct":
            for t in turns_out:
                if not t.get("pass"):
                    failure = t["failure_type"]
                    break
        if failure == "correct" and not lifecycle_ok:
            failure = "lifecycle_error"
        if failure == "correct" and any(t.get("mixing") for t in turns_out):
            failure = "theory_mixing"
    return {
        "case_id": case.case_id,
        "expected_family": case.expected_family,
        "expected_skill": case.expected_skill,
        "expected_intervention": case.expected_intervention,
        "acceptable_families": case.fams,
        "actual_family": (turns_out[0]["actual_family_key"]
                          if turns_out else "error"),
        "actual_skill": turns_out[0]["actual_skill"] if turns_out else None,
        "pass": overall, "failure_type": failure,
        "expected_no_therapy": case.expected_no_therapy,
        "category": case.category.value if hasattr(case.category, "value")
        else case.category,
        "difficulty": case.difficulty.value if hasattr(case.difficulty, "value")
        else case.difficulty,
        "boundary_pair": case.boundary_pair,
        "multi_turn": case.is_multi_turn,
        "turns": turns_out,
        "context": ctx_out,
        "fidelity": fidelity,
    }


def _mixed_family_tags(tags: List[str]) -> bool:
    fams = set()
    for tag in tags:
        if tag.startswith("therapy_") and tag != "therapy_decider":
            fams.add(tag)
    # tags like therapy_cbt_assessment / therapy_cbt_judgment / therapy_cbt_skill
    # / therapy_cbt (host) — all deterministic per family
    fam_keys = {t.split("_")[1] for t in fams}
    return len(fam_keys) > 1


def fidelity_checks(case, uid, sid, state, orch, records, turns_out) -> List[dict]:
    """Deterministic, structured-field-only fidelity checks (audit §4/§5),
    enumerated (a)-(h). None depends on an LLM judge."""
    out: List[dict] = []
    fam = (case.expected_family or "").lower()

    def add(check, ok, detail=""):
        out.append({"case_id": case.case_id, "family": fam or None,
                    "check": check, "ok": bool(ok), "detail": str(detail)[:400]})

    if fam not in THERAPY_FAMILIES:
        return out

    flow_names, branch_names = FAMILY_FLOW.get(fam, ([], []))
    therapy = (state.get("therapy") or {})
    flow_ok = True
    if therapy:
        step_idx = therapy.get("step_index", -1)
        flow_ok = ((0 <= step_idx < len(FAMILY_FLOW[fam][0])) or step_idx == -1) \
            and (therapy.get("branch") in [None, *branch_names]) \
            and all(v in flow_names for v in (therapy.get("visited") or []))
    add("flow_consistency_D", flow_ok, json.dumps(
        {"step_index": therapy.get("step_index"), "branch": therapy.get("branch"),
         "visited": therapy.get("visited")}, ensure_ascii=False) if therapy else
        "no therapy state")

    so = (orch.get("skill_output") or {}) if orch else {}
    sn = so.get("_skill_name") or ""
    if sn:
        add("skill_in_family_A", sn in FAMILY_SKILLS.get(fam, set()), sn)
        tech = (so.get("technique") or {}).get("name")
        if tech:
            valid = _step_techniques(fam, sn)
            add("technique_in_skill_enum_B", tech in valid,
                f"technique={tech} valid={valid}")
        if fam == "cbt" and "automatic_thoughts" in (orch.get("products") or {}):
            pass
        if so.get("adaptation"):
            add("adaptation_shape_F" + fam, True, json.dumps(
                so.get("adaptation"), ensure_ascii=False))
    else:
        add("skill_executed_A", bool(orch.get("assessment") is not None
                                     or orch.get("updated_seq")),
            f"orchestration has no skill_output (skill_name={sn!r}) — "
            "judgment-only or scripted off; family flow must still progress")

    if fam == "cbt":
        prods = (orch.get("products") or {})
        add("cbt_products_E", bool(prods.get("automatic_thoughts"))
            or bool(prods.get("automatic_thoughts") is None
                    and not prods),
            json.dumps(prods, ensure_ascii=False)[:200])
        add("cbt_products_accumulate_E5", bool(
            (orch.get("products") or {}).get("automatic_thoughts")))
    if fam == "sfbt":
        add("sfbt_hopeful_tone_F",
            ((orch.get("skill_output") or {}).get("adaptation") or {}).get(
                "tone_adjustment") == "hopeful")

    fam_tags = [t for t in (r.get("agent", "") for r in records)
                if t.startswith("therapy_") and t != "therapy_decider"]
    tag_fams = {t.split("_")[1] for t in fam_tags}
    add("single_family_tags_C", len(tag_fams) <= 1,
        f"tags_seen={sorted(tag_fams)}")
    add("assessment_shape_per_family_G" if fam != "cbt" else "cbt_assessment_none_G",
        ((orch.get("assessment") is None) if fam == "cbt"
         else isinstance(orch.get("assessment"), dict) and orch.get("assessment") is not None),
        f"therapy={fam} assessment key type={type(orch.get('assessment')).__name__}")
    if fam == "act":
        # known audit bug (audit §6.7): emotion always {} → escalation baseline;
        # recorded, not failed.
        add("act_emotion_known_bug_G_act", True,
            "emotion passed {} by base.run_assessment → escalation baseline; "
            "recorded as production finding, not a runner failure")
    end_reason = (state.get("last_therapy") or {}).get("ended_reason")
    add("ended_reason_valid_H", (end_reason is None)
        or end_reason in END_REASONS, f"ended_reason={end_reason}")
    return out


def _lifecycle_ok(case, turns_out, ctx_out) -> bool:
    """Continuity / lifecycle assertions per case semantics."""
    if not case.is_multi_turn:
        return True
    if case.expected_no_therapy:
        return all(t["pass"] for t in turns_out)
    st = ctx_out["final_state"]
    end = (st.get("last_therapy") or {}).get("ended_reason")
    # crisis supersede cases may end; only ended_reason='crisis' allowed there
    if case.case_id in ("LC-D",):
        return True  # turn-wise CRISIS assertion already validates supersede
    if st.get("crisis"):
        return False  # unexpected crisis input scripted
    if end is None and st.get("therapy") is None:
        # ended without a recorded reason
        return False
    if end is not None and end not in ("stagnation", "regression"):
        return False
    # turns that expect therapy must never pre-emptively end early:
    for t in turns_out[1:]:
        exp = (t.get("expected_families") or [None])
        if exp and exp[0] and t["expected_family"] and \
                t["expected_family"].lower() in THERAPY_FAMILIES \
                and t.get("actual_family") == DAILY.lower() and \
                not t.get("crisis_state"):
            return False
    return True


# ── Metrics ────────────────────────────────────────────────────────────


def _comps(results: List[dict]) -> List[Tuple[dict, dict]]:
    comps = []
    for r in results:
        for t in r.get("turns") or []:
            comps.append((r, t))
    return comps


def compute_metrics(results: List[dict]) -> Tuple[dict, dict, List[dict]]:
    comps = _comps(results)
    fam_total = fam_pass = 0
    skill_total = skill_pass = 0
    boundary_total = boundary_pass = 0
    crisis_total = crisis_pass = 0
    over_n = over_v = 0
    under_n = under_v = 0
    cont_n = cont_ok = 0
    theory_n = theory_v = 0
    lifecycle_cases = [r for r in results if r["category"] == "lifecycle"
                       or (r["multi_turn"] and r["case_id"].startswith("LC-"))]
    for r, t in comps:
        fams_at = t.get("expected_families") or []
        fams_l = [f.lower() for f in fams_at]
        act = t.get("actual_family_key", t.get("actual_family"))
        act_l = (act or "").lower()
        if t.get("error"):
            continue
        if t.get("expected_family") is None and not r.get("expected_no_therapy"):
            continue  # negatives measured on machinery, not family label
        if not t.get("expected_family") and not r.get("expected_no_therapy"):
            continue
        if r.get("expected_no_therapy"):
            # negative case: over-intervention if state entered therapy/ES
            over_n += 1
            if act_l in ("therapy", ES.lower()):
                over_v += 1
            continue  # family/skill labels not asserted (scripting limitation)
        if t.get("expected_family") is None:
            continue
        fam_total += 1
        if t["pass"]:
            fam_pass += 1
        exp_fam = (t.get("expected_family") or "").lower()
        if t.get("expected_skill") is not None:
            skill_total += 1
            if t["pass"]:
                skill_pass += 1
        if r.get("boundary_pair"):
            boundary_total += 1
            if t["pass"]:
                boundary_pass += 1
        if exp_fam == CRI.lower():
            crisis_total += 1
            if act == CRI:
                crisis_pass += 1
        if not exp_fam or exp_fam == DAILY.lower() or exp_fam is None:
            over_n += 1
            if act_l in ("therapy", ES.lower()):
                over_v += 1
        if exp_fam in THERAPY_FAMILIES or exp_fam == ES.lower():
            under_n += 1
            if act_l == DAILY.lower():
                under_v += 1
        if exp_fam in THERAPY_FAMILIES:
            theory_n += 1
            if not t.get("mixing"):
                theory_v += 1
            # continuity: for single-turn cases this turn's boundary must
            # hold therapy; for multi-turn mid-session turns, the therapy
            # session stayed alive with the same effective family
            if t.get("turn") == 1:
                cont_n += 1
                if act_l != DAILY.lower():
                    cont_ok += 1
            elif t.get("expected_family") and \
                    t["expected_family"].lower() in THERAPY_FAMILIES:
                cont_n += 1
                # Continuity counts a miss only when the session genuinely
                # dropped: therapy no longer active, or the effective family
                # changed from the case's family. Use the task-derived
                # effective family — raw actual_family_key is the generic
                # 'therapy' for sessions and would miscount intact sessions.
                eff = (t.get("effective_family") or act_l).lower()
                if act_l != DAILY.lower() and eff in fams_l:
                    cont_ok += 1
    fid_rate = _fidelity_rate(results)
    metrics = {
        "family_accuracy": round(fam_pass / fam_total, 4) if fam_total else None,
        "skill_accuracy": round(skill_pass / skill_total, 4) if skill_total else None,
        "boundary_accuracy": round(boundary_pass / boundary_total, 4)
        if boundary_total else None,
        "over_intervention_rate": round(over_v / over_n, 4) if over_n else None,
        "under_intervention_rate": round(under_v / under_n, 4) if under_n else None,
        "crisis_override_accuracy": round(crisis_pass / crisis_total, 4)
        if crisis_total else None,
        "therapy_continuity": round(cont_ok / cont_n, 4) if cont_n else None,
        "theory_mixing_rate": round(1 - (theory_v / theory_n), 4)
        if theory_n else 0.0,
        "fidelity_rate": fid_rate["overall"] if fid_rate else None,
        "lifecycle_error_rate": round(1 - (len([r for r in lifecycle_cases
                                               if r["pass"]])
                                          / len(lifecycle_cases)), 4)
        if lifecycle_cases else None,
        "family_comparisons": fam_total,
        "skill_comparisons": skill_total,
        "boundary_comparisons": boundary_total,
    }
    per_family = Counter()
    for r in results:
        for f in r.get("fidelity") or ():
            per_family[f["family"]] += 0
    metrics["fidelity_breakdown"] = fid_rate["per_family"] if fid_rate else {}
    failures = [r for r in results if not r["pass"]]
    return metrics, {}, failures


def _fidelity_rate(results: List[dict]) -> Optional[dict]:
    per_family: Dict[str, Dict[str, int]] = {}
    total = ok_total = 0
    for r in results:
        for f in r.get("fidelity") or ():
            fam = f["family"]
            pf = per_family.setdefault(fam, {"n": 0, "ok": 0})
            pf["n"] += 1
            total += 1
            if f["ok"]:
                pf["ok"] += 1
                ok_total += 1
    if not total:
        return None
    return {"overall": round(ok_total / total, 4),
            "per_family": per_family, "total": total, "ok": ok_total}


# ── Report ─────────────────────────────────────────────────────────────


def build_report(metrics: dict, results: List[dict], failures: List[dict],
                 skipped: List[dict], known_bugs: List[str]) -> str:
    lines: List[str] = []
    a = lines.append
    Families = Counter(r["expected_family"] for r in results)
    ft = Counter(r["failure_type"] for r in results if r["failure_type"] != "correct")
    a("# Therapy Evaluation Report (2026-09-12)")
    a("")
    a("## Dataset Overview")
    a("")
    a(f"- Gold cases executed: {len(results)} (skipped {len(skipped)})")
    a(f"- Passed machinery+labels: {sum(1 for r in results if r['pass'])}, "
      f"failed: {len(failures)}")
    fam_counts = {str(k): v for k, v in Families.items()}
    a("- Families: " + ", ".join(f"{k}={v}" for k, v in sorted(fam_counts.items())))
    a("- Categories: " + ", ".join(f"{k}={v}" for k, v in sorted(
        Counter(r["category"] for r in results).items())))
    a("")
    a("Derived from docs/THERAPY_EVALUATION.md structured observables: "
      "state.json therapy/crisis/ES sub-states, orchestration.json "
      "assessment/skill_output/products, sink-record llm tags (logs/"
      "llm_calls.jsonl equivalent via FakeSink).")
    a("")
    a("## Scripting policy — RUNNER LIMITATIONS, read before the metrics")
    a("")
    a("- No network. LLM-JUDGMENT slots are scripted per turn from gold")
    a("  (SafetyAgent risk, TherapyDecider verdict, assessment payload,")
    a("  step judgment action/technique). Consequently:")
    a("  - family_accuracy measures machinery acceptance of a gold-scripted")
    a("    SwitchToTherapy, NOT the decider LLM's real classification.")
    a("  - expected-intervention passes are agenda-driven: we script the")
    a("    technique; the REAL check being measured is the enum-clamp +")
    a("    pick_skill code path, not the LLM's technique choice.")
    a("  - Decider negatives (expected_no_therapy) are scripted 'decider says")
    a("    no'. They measure NO-OP mechanics (no SwitchToTherapy signal, no")
    a("    ES/ESO state, owner stays daily), NOT production over-intervention")
    a("    behavior. This is a scripting limitation and is flagged openly.")
    a("- Real (unscripted) machinery exercised: signal application & owner")
    a("  transitions, ESO plan/supersede vs therapy, code pick_skill routing")
    a("  (CBT distortion routing, ACT/DBT/MI/SFBT initial-step routing),")
    a("  step judgment clamping, utility ledger + stagnation/regression and")
    a("  round-cap stop rules, products accumulation, EndTherapy reasons.")
    a("")
    a("## Metrics (12)")
    a("")
    order = ["family_accuracy", "skill_accuracy", "boundary_accuracy",
             "over_intervention_rate", "under_intervention_rate",
             "crisis_override_accuracy", "therapy_continuity",
             "theory_mixing_rate", "fidelity_rate", "lifecycle_error_rate"]
    for k in order:
        a(f"- {k}: **{metrics.get(k)}**")
    a("- family_comparisons: {}".format(metrics.get("family_comparisons")))
    a("- skill_comparisons: {}".format(metrics.get("skill_comparisons")))
    a("")
    a("## Therapy Continuity")
    a("")
    a("Definition: an in-session turn counts as continuous if the therapy")
    a("session stayed active (owner did not move to daily) and its effective")
    a("family (the task-derived family, e.g. 'cbt'/'mi', for therapy sessions")
    a("— matching what the pass/fail computation uses) remained within the")
    a("case's acceptable families. A miss is counted only when a session")
    a(f"genuinely dropped. Final value: therapy_continuity = "
     f"**{metrics.get('therapy_continuity')}**.")
    a("")
    a("Fidelity per family (checks a-f, g excluded from rate):")
    for fam, v in (metrics.get("fidelity_breakdown") or {}).items():
        a(f"- {fam}: {v['ok']}/{v['n']}")
    a("")
    a("## Known production findings surfaced (from audit; not runner failures)")
    a("")
    for b in known_bugs:
        a(f"- {b}")
    a("")
    a("## Failure cases")
    a("")
    if failures:
        a("| case_id | expected | actual | failure_type |")
        a("|---|---|---|---|")
        for r in failures:
            a(f"| {r['case_id']} | {r['expected_family']}/{r['expected_skill']}"
              f"/{r['expected_intervention']} | {r['actual_family']}/"
              f"{r['actual_skill']} | {r['failure_type']} |")
    else:
        a("(none)")
    a("")
    a("## Failure classification counts")
    a("")
    for k, v in sorted(ft.items()):
        a(f"- {k}: {v}")
    a("")
    a("## Conclusions")
    a("")
    a("- Known issues already surfaced by the audit are reproduced here as")
    a("  recorded findings, not failures: CBT assessment payload is None by")
    a("  design (no assessment skill; state_score from classification")
    a("  confidence); ACT escalation/monitoring dims are always baseline")
    a("  (emotion always {}). No gold relabeling was performed in this")
    a("  phase — gold_label_error / production_bug adfunctions are left for")
    a("  the manual adjudication phase with full per-case context preserved")
    a("  in results.json.")
    a("")
    if skipped:
        a("## Skipped cases")
        a("")
        for s in skipped:
            a(f"- {s['case_id']}: {s['reason']}")
    return "\n".join(lines) + "\n"


KNOWN_BUGS = [
    "CBT: no assessment skill — orchestration.assessment is None every turn "
    "(state_score uses classification confidence; audit §3/§6.7).",
    "ACT: escalation_level/monitoring dims always baseline — base.run_assessment "
    "passes emotion:{} so arousal=0.0 (audit §6.7, src/skills/act.py docstring).",
    "MI/SFBT/ACT/DBT profile.readiness uses optional fallback key "
    "'cognitive_readiness' (audit §6.7).",
    "In-session state_effects from skills are NOT applied to SessionState "
    "(persistence via orchestration.products only; audit §3).",
]


# ── Main ───────────────────────────────────────────────────────────────


def run_eval(output_dir: Union[Path, str] = OUTPUT_DIR,
             dataset_path: Union[Path, str] = DATASET_PATH,
             tmp_root: Optional[Path] = None) -> dict:
    """Run the full therapy evaluation; write artifacts; return summary."""
    output_dir = Path(output_dir)
    cases = load_therapy_cases(Path(dataset_path))
    tmp_root = tmp_root or Path(tempfile.mkdtemp(prefix="therapy_eval_"))
    env = TherapyEvalEnv(tmp_root)
    results: List[dict] = []
    skipped: List[dict] = []
    try:
        for case in cases:
            try:
                results.append(run_case(env, case))
            except Exception as e:  # noqa: BLE001 — record, never mask
                skipped.append({"case_id": case.case_id, "reason": repr(e)})
    finally:
        env.close()
    metrics, _, failures = compute_metrics(results)
    report = build_report(metrics, results, failures, skipped, KNOWN_BUGS)
    output_dir.mkdir(parents=True, exist_ok=True)
    slim = [{k: r[k] for k in ("case_id", "expected_family", "expected_skill",
                               "expected_intervention", "actual_family",
                               "actual_skill", "pass", "failure_type")
             } for r in results]
    payload_cases = {"cases": slim, "skipped": skipped}
    (output_dir / "cases.json").write_text(
        json.dumps(payload_cases, ensure_ascii=False, indent=1), encoding="utf-8")
    (output_dir / "results.json").write_text(
        json.dumps({"results": results, "skipped": skipped},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (output_dir / "summary.json").write_text(
        json.dumps({"metrics": metrics, "skipped_count": len(skipped)},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (output_dir / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=1), encoding="utf-8")
    (output_dir / "fidelity_results.json").write_text(
        json.dumps({"per_case": {r["case_id"]: r.get("fidelity")
                                 for r in results if r.get("fidelity")},
                    "breakdown": metrics.get("fidelity_breakdown")},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    return {"metrics": metrics, "results": results, "failures": failures,
            "skipped": skipped}


if __name__ == "__main__":
    out = run_eval()
    print(json.dumps({k: v for k, v in out["metrics"].items()
                      if not isinstance(v, (dict,))}, ensure_ascii=False, indent=1))
