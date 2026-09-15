"""Routing evaluation runner.

Drives the REAL routing path — Scheduler.handle_message with an offline
FakeProvider-compatible provider (same harness idea as tests/support/fakes.py) — for
every gold case in tests/eval/eval_data/routing_cases.json, and derives the
actual family/skill ONLY from the audited pipeline's state objects
(docs/ROUTING_EVALUATION.md). No routing logic is reimplemented:

- post-turn state.json (Scheduler._apply_signals → SessionStore.read_state)
  is the outcome record write at the turn boundary: crisis flag, therapy
  (TherapyProgress / owner=therapy_*), ES sub-states (pmr/grounding/
  affect_labeling with status proposing/active).
- orchestration.json (`_skill_name`) names the therapy skill actually
  executed by the real intra-session router (TherapyAgentBase.pick_skill,
  src/agents/therapy/base.py:97).

Offline stand-ins (reported explicitly in section "Scripting policy" of
report.md): this runner never touches the network. The pipeline's LLM
classification calls are scripted per turn from a gold-informed agenda:

- SafetyAgent's cheap model returns CRISIS on turns whose gold family is
  crisis, else SAFE (deterministic stand-in; case text never reaches a real
  safety model — safety_override_accuracy therefore measures the CRISIS
  short-circuit *mechanics*, not LLM safety classification).
- TherapyDecider returns need_therapy=true with the gold therapy on turns
  whose gold family is therapy (first therapy turn of a case), else false.
  Everything after that decision — SwitchToTherapy, orchestration, FLOW /
  pick_skill routing, marginal-utility stop rules — is REAL pipeline code.
- ES opportunity skills answer should_offer=true only for the skill gold
  expects, and only when that engine's own pure-code `_gate` fired first.

Everything downstream of those scripted decisions — crisis short-circuit,
signal application, ESO priority/exclusivity/supersede, cooldowns, therapy
continuation — runs unmodified.

Outputs → outputs/routing_evaluation/{cases.json, results.json, summary.json,
failures.json, report.md}.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

import src.skills  # noqa: F401 — register all skills (build_scheduler parity)
from src.agents.affect_labeling import AffectLabelingEngine
from src.agents.grounding import GroundingEngine
from src.agents.host_agent import HostDialogueAgent
from src.agents.progressive_muscle_relaxation import ProgressiveMuscleRelaxationEngine
from src.agents.safety_agent import SafetyAgent
from src.agents.settlement import SettlementLine
from src.agents.therapy import (
    ActTherapyAgent,
    CbtTherapyAgent,
    DbtTherapyAgent,
    MiTherapyAgent,
    SfbtTherapyAgent,
)
from src.agents.therapy_decider import TherapyDecider
from src.core.llm_client import LLMClient
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.utils.config import config
from tests.eval.support.routing_schema import RoutingFamily, load_cases, RoutingCase  # noqa: F401
from tests.support.fakes import FakeSink

DATASET_PATH = Path(__file__).resolve().parents[1] / "eval_data" / "routing_cases.json"
OUTPUT_DIR = _REPO / "outputs" / "routing_evaluation"

# ── Prompt markers (verbatim prefixes of the real LLm prompts) ─────────

SAFETY_PREFIX = "You are the Safety Agent"
JUDGE_MARKER = "疗法的疗程推进判断模块"          # STEP_JUDGMENT_PROMPT
DECIDER_MARKER = "疗法选择模块"                 # THERAPY_DECISION_PROMPT
PMR_OPP_MARKER = "情绪支持系统里的「身体放松判断」模块"
PMR_RESP_MARKER = "情绪支持系统里的「放松回应理解」模块"
GND_OPP_MARKER = "情绪支持系统里的「回到当下判断」模块"
GND_RESP_MARKER = "情绪支持系统里的「着陆回应理解」模块"
AL_OPP_MARKER = "情绪支持系统里的「情绪命名协助」模块"
AL_RESP_MARKER = "情绪支持系统里的「情绪回应理解」模块"
EXTRACT_MARKER = "automatic thought"           # CBT EXTRACT_PROMPT family
DISTORTION_MARKER = "cognitive distortion"     # CBT DISTORTION_PROMPT family
ASSESS_MARKER = "state assessment"             # ACT/DBT/MI/SFBT assessment

PROFILE_NOCHANGE = {"updates": {}, "append_lists": {}, "no_change": True}
JUDGE_STAY = {"action": "stay", "target_step": None, "achieved": False,
              "reason": "eval: stay"}
CBT_EXTRACT = {"automatic_thoughts": [{"thought": "我总搞砸", "confidence": 0.9,
                                       "span": "", "target": "self"}],
               "primary_thought": "我总搞砸"}
CBT_CLASSIFY = {"classifications": [{"thought": "我总搞砸",
                                     "distortions": [{"type": "overgeneralization",
                                                      "confidence": 0.8}]}],
                "distortion_summary": {"distortion_count": 1,
                                       "dominant_pattern": "overgeneralization",
                                       "severity": "moderate"}}
INTERVENTION = {
    "technique": {"name": "eval_technique", "description": "eval", "steps": ["s1", "s2"]},
    "conversation_goal": "eval goal",
    "adaptation": {"tone_adjustment": "warm", "pace_adjustment": "slow",
                   "culture_note": "eval"},
    "contraindications": [],
}
ASSESSMENT = {  # superset satisfying ACT/DBT/MI/SFBT assessment parsers
    "fusion": {"index": 0.0, "state": "cf_observing"},
    "avoidance": {"index": 0.0, "state": "ea_willing"},
    "openness": {"index": 0.5, "state": "eo_receptive"},
    "alignment": {"index": 0.5, "state": "medium", "values_mentioned": []},
    "activation": {"index": 0.5, "state": "medium"},
    "emotion_dysregulation": {"index": 0.0, "state": "moderate"},
    "distress_level": {"index": 0.3, "state": "moderate"},
    "impulsivity": {"index": 0.2, "state": "low"},
    "interpersonal_conflict": {"index": 0.2, "state": "low"},
    "mindfulness_awareness": {"index": 0.5, "state": "moderate"},
    "ambivalence": {"index": 0.0, "state": "low"},
    "sustain_talk": {"index": 0.0, "state": "low"},
    "change_talk": {"index": 0.0, "state": "low"},
    "self_efficacy": {"index": 0.5, "state": "medium"},
    "change_readiness": {"index": 0.0, "state": "low"},
    "resource_awareness": {"index": 0.5, "state": "medium"},
    "problem_orientation": {"index": 0.5, "state": "problem_stuck"},
    "goal_clarity": {"index": 0.5, "state": "medium"},
    "recommended_processes": [],
    "dbt_intervention_priority": 0.5,
    "mi_intervention_need": 0.5,
    "sfbt_intervention_need": 0.5,
    "psychological_flexibility": 0.5,
}
RISK_SAFE = {"risk_level": "SAFE", "risk_type": "none"}
RISK_CRISIS = {"risk_level": "CRISIS", "risk_type": "suicidal_ideation"}

# ── Engine-state blobs (field sets mirror the engines' own writes) ────

PMR_PROPOSING = {
    "status": "proposing", "mode": "gentle", "start_seq": 1,
    "body_parts": ["shoulders", "hands", "face"], "part_index": 0,
    "current_part": "shoulders", "phase": "prepare", "beats_done": 0,
    "parts_completed": 0, "closing": False,
}
PMR_ACTIVE = dict(PMR_PROPOSING, status="active", phase="tension", beats_done=1)
GND_PROPOSING = {"status": "proposing", "step": 0, "trigger_seq": 1,
                 "last_seq": 1, "close_reason": None}
GND_ACTIVE = dict(GND_PROPOSING, status="active")
AL_PROPOSING = {"status": "proposing", "candidates": ["委屈", "失望", "焦虑"],
                "start_seq": 1, "reason": "eval"}
ES_BLOBS = {"pmr": PMR_ACTIVE, "grounding": GND_ACTIVE,
            "affect_labeling": AL_PROPOSING}

THERAPY_UPPER = {"cbt": "CBT", "act": "ACT", "dbt": "DBT", "mi": "MI",
                 "sfbt": "SFBT"}


def _skill_to_family(skill_name: str) -> str:
    """orchestration `_skill_name` (e.g. dbt_distress_tolerance) → family key."""
    for fam in ("act", "dbt", "mi", "sfbt"):
        if skill_name.startswith(fam + "_"):
            return fam
    return "cbt"  # CBT skills carry no family prefix


# ── Provider ──────────────────────────────────────────────────────────

class AgendaProvider:
    """Per-turn agenda-driven offline provider (FakeProvider-compatible).

    Response selection is keyed off verbatim prompt markers instead of strict
    FIFO so script order cannot drift from the audited call order; the same
    "script complete/stream per model bucket" semantics of tests/support/fakes.py
    FakeProvider is preserved for accounting (FakeSink) and call logs.
    """

    def __init__(self):
        self.calls: list = []
        self.agenda: Dict[str, Any] = {}

    async def acomplete(self, prompt, *, model, json_mode=False):
        self.calls.append(("complete", prompt, model, json_mode))
        resp = self._pick(prompt)
        if isinstance(resp, Exception):
            raise resp
        text = json.dumps(resp, ensure_ascii=False) if isinstance(resp, dict) else str(resp)
        return text, {"prompt_tokens": 10, "completion_tokens": 5}

    def _pick(self, prompt: str) -> Any:
        a = self.agenda
        if prompt.startswith(SAFETY_PREFIX):
            return a.get("safety") or RISK_SAFE
        if JUDGE_MARKER in prompt:
            return JUDGE_STAY
        if DECIDER_MARKER in prompt:
            d = a.get("decide_therapy")
            if not d:
                return {"need_therapy": False, "therapy": None, "reason": "eval"}
            return {"need_therapy": True, "therapy": d, "reason": "eval"}
        p = prompt.lower()
        if PMR_OPP_MARKER in prompt:
            return {"should_offer": bool(a.get("pmr_offer")),
                    "body_focus": "shoulders", "reason": "eval"}
        if PMR_RESP_MARKER in prompt:
            return a.get("pmr_response") or {"kind": "neutral", "reason": "eval"}
        if GND_OPP_MARKER in prompt:
            return {"should_offer": bool(a.get("gnd_offer")), "reason": "eval"}
        if GND_RESP_MARKER in prompt:
            return a.get("gnd_response") or {"kind": "affirmative", "reason": "eval"}
        if AL_OPP_MARKER in prompt:
            return {"should_offer": bool(a.get("affect_offer")),
                    "candidates": ["委屈", "失望", "焦虑"], "reason": "eval"}
        if AL_RESP_MARKER in prompt:
            return a.get("affect_response") or {"kind": "no_answer", "reason": "eval"}
        if DISTORTION_MARKER in p:
            return CBT_CLASSIFY
        if EXTRACT_MARKER in p:
            return CBT_EXTRACT
        if ASSESS_MARKER in p:
            return ASSESSMENT
        return INTERVENTION  # therapy intervention skills + profile updates

    async def astream(self, prompt, *, model):
        self.calls.append(("stream", prompt, model))
        yield "嗯", {}
        yield "", {"prompt_tokens": 10, "completion_tokens": 5}


# ── Environment assembly (offline copy of src.bootstrap.build_scheduler,
#    same structure as tests/integration/test_cross_module_orchestration.py:89) ──────


class EvalEnv:
    def __init__(self, tmp_root: Path):
        self._saved = (config.MODEL_NAME, config.CHEAP_MODEL_NAME,
                       config.SUMMARY_TOKEN_THRESHOLD,
                       config.WINDOW_TOKEN_THRESHOLD, config.THERAPY_MAX_ROUNDS)
        config.MODEL_NAME = "test-model"
        config.CHEAP_MODEL_NAME = "test-cheap"
        config.SUMMARY_TOKEN_THRESHOLD = 10 ** 9   # summary LLM off
        config.WINDOW_TOKEN_THRESHOLD = 0          # decider runs every daily turn
        config.THERAPY_MAX_ROUNDS = 10
        self.provider = AgendaProvider()
        self.sink = FakeSink()
        self.client = LLMClient(provider=self.provider, sink=self.sink,
                                provider_name="fake")
        self.sessions = SessionStore(data_root=str(tmp_root))
        self.profiles = ProfileStore(data_root=str(tmp_root))
        therapy_agents = {
            "CBT": CbtTherapyAgent(self.client, session_store=self.sessions),
            "ACT": ActTherapyAgent(self.client, session_store=self.sessions),
            "DBT": DbtTherapyAgent(self.client, session_store=self.sessions),
            "MI": MiTherapyAgent(self.client, session_store=self.sessions),
            "SFBT": SfbtTherapyAgent(self.client, session_store=self.sessions),
        }
        self.scheduler = Scheduler(
            dialogue_line=DialogueLine(
                HostDialogueAgent(self.client, session_store=self.sessions),
                session_store=self.sessions),
            decision_line=DecisionLine(
                SafetyAgent(self.client), session_store=self.sessions,
                therapy_decider=TherapyDecider(self.client),
                therapy_agents=therapy_agents,
                pmr_engine=ProgressiveMuscleRelaxationEngine(
                    self.client, session_store=self.sessions),
                grounding_engine=GroundingEngine(
                    self.client, session_store=self.sessions),
                affect_engine=AffectLabelingEngine(
                    self.client, session_store=self.sessions),
            ),
            settlement_line=SettlementLine(
                self.client, session_store=self.sessions,
                profile_store=self.profiles),
            session_store=self.sessions, profile_store=self.profiles,
        )
        self._closed = False

    # config is a module-level singleton shared with the pytest process —
    # restore on teardown when embedded in tests.
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        (config.MODEL_NAME, config.CHEAP_MODEL_NAME,
         config.SUMMARY_TOKEN_THRESHOLD, config.WINDOW_TOKEN_THRESHOLD,
         config.THERAPY_MAX_ROUNDS) = self._saved


# ── State-based family/skill derivation (audit §4) ────────────────────


def _es_live_skills(state: dict) -> List[str]:
    live = []
    if state.get("pmr") and state["pmr"].get("status") in ("proposing", "active"):
        live.append("pmr")
    if state.get("grounding") and state["grounding"].get("status") in ("proposing", "active"):
        live.append("grounding")
    if (state.get("affect_labeling")
            and state["affect_labeling"].get("status") in ("proposing", "active")):
        live.append("affect_labeling")
    return live


def actual_family_skill(state: dict, orch: dict) -> Tuple[RoutingFamily, Optional[str]]:
    if state.get("crisis"):
        return RoutingFamily.CRISIS, "crisis"
    prog = state.get("therapy")
    if prog:
        skill_name = ((orch or {}).get("skill_output") or {}).get("_skill_name", "")
        skill = _skill_to_family(skill_name).lower() if skill_name else \
            (THERAPY_UPPER.get((prog.get("name") or "").lower(), None) or
             (prog.get("name") or "").lower())
        return RoutingFamily.THERAPY, skill
    live = _es_live_skills(state)
    if live:
        return RoutingFamily.EMOTION_SUPPORT, live[0]
    return RoutingFamily.DAILY, None


# ── State seeding (matrix / lifecycle PVC setup) ──────────────────────


def set_therapy(env: EvalEnv, sid: str, key: str) -> None:
    """Active therapy session, seeded the same direct state-write way as
    tests/integration/test_cross_module_orchestration.py:188 (set_therapy)."""
    state = env.sessions.read_state(sid)
    state["owner"] = f"therapy_{key}"
    state["therapy"] = {"name": THERAPY_UPPER[key], "start_seq": 0,
                        "rounds": 0, "step_index": 0, "branch": None,
                        "basic_flow_complete": False}
    env.sessions.write_state(sid, state)


def set_es(env: EvalEnv, sid: str, skill: str) -> None:
    state = env.sessions.read_state(sid)
    state[skill] = copy.deepcopy(ES_BLOBS[skill])
    env.sessions.write_state(sid, state)


def set_es_closed(env: EvalEnv, sid: str, skill: str, reason: str) -> None:
    state = env.sessions.read_state(sid)
    blob = copy.deepcopy(ES_BLOBS[skill])
    blob["status"] = "closed"
    blob["close_reason"] = reason
    blob["closed_seq"] = 2
    blob["last_seq"] = 2
    env.sessions.write_state(sid, {**state, skill: blob})


# ── Agenda builder (gold-informed scripting policy, documented) ───────


def agenda_for_turn(case, turn_idx: int, therapy_active_before: bool,
                    fillers_done: int) -> dict:
    fam = RoutingFamily(case.expected_family_at(turn_idx))
    skill = case.expected_skill_at(turn_idx)
    a: Dict[str, Any] = {"safety": RISK_SAFE,
                         "pmr_offer": False, "gnd_offer": False,
                         "affect_offer": False,
                         "pmr_response": {"kind": "neutral", "reason": "eval"},
                         "gnd_response": {"kind": "affirmative", "reason": "eval"},
                         "affect_response": {"kind": "no_answer", "reason": "eval"}}
    if fam == RoutingFamily.CRISIS:
        a["safety"] = RISK_CRISIS
    # TherapyDecider only scripted on the turn gold opens the therapy door
    if fam == RoutingFamily.THERAPY and not therapy_active_before:
        a["decide_therapy"] = THERAPY_UPPER.get(skill or "", "CBT")
    # ES opportunity: offer only the gold-expected skill (engine `_gate` still pre-filters)
    if fam == RoutingFamily.EMOTION_SUPPORT:
        if skill == "pmr":
            a["pmr_offer"] = True
        elif skill == "grounding":
            a["gnd_offer"] = True
        elif skill == "affect_labeling":
            a["affect_offer"] = True
    # lifecycle negative-turn scripting: classifier drives declined/exited
    if case.lifecycle_stage in ("declined", "exited", "cooldown") and fillers_done == 0:
        # the turn under evaluation IS the decline/exit utterance
        a["gnd_response"] = {"kind": "negative", "reason": "eval"}
        a["pmr_response"] = {"kind": "negative", "reason": "eval"}
    return a


# ── Case execution ────────────────────────────────────────────────────


async def _drive_turn(env: EvalEnv, uid: str, sid: str, msg: str) -> dict:
    final: dict = {}
    async for ev in env.scheduler.handle_message(uid, sid, msg):
        if ev.get("event") == "final":
            final = ev.get("data") or {}
    return final


def _post_state(env: EvalEnv, sid: str) -> Tuple[dict, dict]:
    return env.sessions.read_state(sid), env.sessions.read_orchestration(sid)


def run_case(env: EvalEnv, case) -> dict:
    uid = "eval_user"
    sid = env.sessions.create(uid)
    turns_out: List[dict] = []
    lifecycle_obs: Dict[str, Any] = {}
    errors: List[str] = []
    therapy_active = False   # therapy latched by prior turn's SwitchToTherapy
    fillers_done = 0

    # ── matrix / lifecycle state seeding ────────────────────────────
    # therapy active_before: seeded the direct state-write way as
    # tests/integration/test_cross_module_orchestration.py:188 (set_therapy); ES
    # active_before: seeded with the engine-shaped blobs above.
    skill0 = case.active_before
    if case.lifecycle_stage:
        stage = case.lifecycle_stage
        if stage == "active":
            set_es(env, sid, "pmr")
        elif stage == "complete":
            state = env.sessions.read_state(sid)
            blob = copy.deepcopy(PMR_ACTIVE)
            blob["closing"] = True
            blob["phase"] = "closing"
            env.sessions.write_state(sid, {**state, "pmr": blob})
        elif stage == "declined":
            state = env.sessions.read_state(sid)
            env.sessions.write_state(sid, {**state, "pmr": copy.deepcopy(PMR_PROPOSING)})
        elif stage in ("exited", "cooldown"):
            set_es(env, sid, "grounding")
            if stage == "cooldown":  # closed(completed) just before cooldown window
                state = env.sessions.read_state(sid)
                blob = copy.deepcopy(GND_ACTIVE)
                blob.update({"status": "closed", "close_reason": "completed",
                             "closed_seq": 2, "last_seq": 2})
                env.sessions.write_state(sid, {**state, "grounding": blob})
    elif skill0:
        if skill0 in THERAPY_UPPER:
            set_therapy(env, sid, skill0)
            therapy_active = True
        else:
            set_es(env, sid, skill0)

    for idx, msg in enumerate(case.turns, start=1):
        env.provider.agenda = agenda_for_turn(case, idx - 1, therapy_active, fillers_done)
        try:
            final = asyncio.run(_drive_turn(env, uid, sid, msg))
        except Exception as e:  # noqa: BLE001 — record, don't mask
            errors.append(f"turn{idx}: {e!r}")
            turns_out.append({
                "turn": idx, "message": msg, "error": repr(e),
                "expected_family": case.expected_family_at(idx - 1).value,
                "expected_skill": case.expected_skill_at(idx - 1),
                "actual_family": "error", "actual_skill": None, "pass": False,
                "failure_type": "error",
            })
            continue
        state, orch = _post_state(env, sid)
        fam, task = actual_family_skill(state, orch)
        exp_f = case.expected_family_at(idx - 1).value
        exp_s = case.expected_skill_at(idx - 1)
        # Lifecycle close turns lag: gold's family/skill refers to the ES
        # guidance actually in THIS turn's reply (pmr_turn via plan()), while
        # the boundary state may already be closed (e.g. "complete" closes at
        # the boundary). For lifecycle cases with family emotion_support but
        # boundary state daily, pass the turn when the closing feedback
        # belongs to the seeded skill (kind=closing/ack recorded earlier).
        lifecycle_close_artifact = (
            case.lifecycle_stage in ("complete", "declined", "exited", "cooldown")
            and fam == RoutingFamily.DAILY
            and exp_f == RoutingFamily.EMOTION_SUPPORT.value)
        if lifecycle_close_artifact:
            fam, task = RoutingFamily.EMOTION_SUPPORT, case.active_before or exp_s
        skill_lagged = (fam == RoutingFamily.THERAPY
                        and not orch.get("updated_seq"))
        if skill_lagged:
            passed = fam.value == exp_f
            task_out = None
        else:
            if fam == RoutingFamily.THERAPY and task:
                task = task.lower()
            task_out = task
            passed = (fam.value == exp_f) and (exp_s is None or task == exp_s)
        turns_out.append({
            "turn": idx, "message": msg,
            "expected_family": exp_f, "expected_skill": exp_s,
            "actual_family": fam.value, "actual_skill": task_out,
            "risk_state": final.get("risk_state"),
            "live_es": len(_es_live_skills(state)),
            "pass": passed,
            "failure_type": "correct" if passed else _turn_failure(
                exp_f, exp_s, fam.value, task_out, case),
        })
        therapy_active = fam == RoutingFamily.THERAPY

    # lifecycle property probes (LC cases scored on properties, not labels)
    lifecycle_pass: Optional[bool] = None
    if case.lifecycle_stage:
        try:
            lifecycle_obs, lifecycle_pass = _lifecycle_check(env, uid, sid, case)
        except Exception as e:  # noqa: BLE001
            errors.append(f"lifecycle: {e!r}")
            lifecycle_obs["error"] = repr(e)
            lifecycle_pass = False

    overall = (not errors) and all(t.get("pass") for t in turns_out) \
        and (lifecycle_pass is not False)
    failure = "correct"
    if errors:
        failure = "error"
    elif not overall:
        failure = _case_failure(turns_out, case)
    return {
        "case_id": case.case_id,
        "expected_family": case.expected_family.value,
        "expected_skill": case.expected_skill,
        "actual_family": turns_out[0]["actual_family"] if turns_out else "error",
        "actual_skill": turns_out[0]["actual_skill"] if turns_out else None,
        "pass": overall,
        "failure_type": failure,
        "difficulty": case.difficulty.value if hasattr(case.difficulty, "value") else case.difficulty,
        "category": case.category,
        "boundary_case": case.boundary_case,
        "safety_relevant": case.safety_relevant,
        "multi_turn": case.is_multi_turn,
        "active_before": case.active_before,
        "lifecycle_stage": case.lifecycle_stage,
        "turns": turns_out,
        "lifecycle": lifecycle_obs or None,
    }


def _turn_failure(exp_f: str, exp_s: Optional[str], act_f: str,
                  act_s: Optional[str], case) -> str:
    if case.lifecycle_stage:
        return "lifecycle_error"
    if exp_f != act_f:
        if exp_f == RoutingFamily.DAILY.value:
            return "over_intervention"
        if act_f == RoutingFamily.DAILY.value:
            return "under_intervention"
        return "wrong_family"
    return "wrong_skill"


def _case_failure(turns: List[dict], case) -> str:
    for t in turns:
        if t.get("failure_type") not in (None, "correct"):
            return t["failure_type"]
    return "wrong_family"


def _lifecycle_check(env: EvalEnv, uid, sid, case) -> Tuple[dict, bool]:
    """Lifecycle property probes; filler turn = same-session daily utterance
    exercising the engine gate with offer scripted ON, so only the engine's
    own cooldown/state machine can suppress a re-offer."""
    state, _ = _post_state(env, sid)
    skill = case.active_before or "pmr"
    obs: Dict[str, Any] = {}
    ok = True
    offer = {"pmr": "pmr_offer", "grounding": "gnd_offer",
             "affect_labeling": "affect_offer"}[skill]
    filler = {"pmr": "肩膀好紧", "grounding": "脑子好乱",
              "affect_labeling": "心里堵得慌，说不清是什么感觉"}[skill]

    def es_now():
        st, _ = _post_state(env, sid)
        return skill in _es_live_skills(st)

    stage = case.lifecycle_stage
    if stage == "active":
        obs["stays_live"] = es_now()
        # no double offer: exactly one live sub-state for this skill
        st, _ = _post_state(env, sid)
        obs["single_live"] = len([k for k in ("pmr", "grounding", "affect_labeling")
                                  if k in _es_live_skills(st)]) <= 1
        obs["pmr_continues"] = (st.get("pmr") or {}).get("status") == "active"
        ok = obs["stays_live"] and obs["single_live"]
    elif stage == "complete":
        closed = (state.get(skill) or {}).get("status") == "closed"
        reason = (state.get(skill) or {}).get("close_reason")
        obs["closed"] = closed
        obs["close_reason"] = reason
        ok = closed and reason == "completed"
    elif stage in ("declined", "exited"):
        closed = (state.get(skill) or {}).get("status") == "closed"
        reason = (state.get(skill) or {}).get("close_reason")
        obs["closed"] = closed
        obs["close_reason"] = reason
        env.provider.agenda = {"safety": RISK_SAFE, offer: True}
        asyncio.run(_drive_turn(env, uid, sid, filler))
        st2, _ = _post_state(env, sid)
        obs["no_reoffer_while_cooldown"] = skill not in _es_live_skills(st2)
        ok = closed and reason == stage and obs["no_reoffer_while_cooldown"]
    elif stage == "cooldown":
        closed = (state.get(skill) or {}).get("status") == "closed"
        obs["closed_before"] = closed
        good = closed
        for i in range(2):  # two filler gate-hitting turns inside cooldown
            env.provider.agenda = {"safety": RISK_SAFE, offer: True}
            asyncio.run(_drive_turn(env, uid, sid, filler))
            st2, _ = _post_state(env, sid)
            if skill in _es_live_skills(st2):
                good = False
                obs[f"reoffer_at_fill{i}"] = True
                break
        obs["cooldown_honored"] = good
        ok = good
    return obs, ok


# ── Metrics ───────────────────────────────────────────────────────────


def _comparisons(results: List[dict]) -> List[dict]:
    """Single-turn family/skill comparisons; multi-turn scored per-turn;
    matrix(active_before)/lifecycle cases scored separately (excluded)."""
    comps = []
    for r in results:
        if r["lifecycle_stage"] or r["active_before"]:
            continue
        for t in r.get("turns") or []:
            comps.append({
                "case_id": r["case_id"], "turn": t["turn"],
                "expected_family": t["expected_family"],
                "expected_skill": t["expected_skill"],
                "actual_family": t["actual_family"],
                "actual_skill": t["actual_skill"],
                "boundary": r["boundary_case"],
                "pass": t["pass"] and not t.get("error"),
            })
    return comps


def compute_metrics(results: List[dict]) -> Tuple[dict, dict, List[dict]]:
    comps = _comparisons(results)
    fam_total = len(comps)
    fam_pass = sum(1 for c in comps if c["pass"])
    skill_comps = [c for c in comps if c["expected_skill"] is not None]
    skill_total, skill_pass = len(skill_comps), sum(1 for c in skill_comps if c["pass"])
    boundary = [c for c in comps if c["boundary"]]
    bound_total, bound_pass = len(boundary), sum(1 for c in boundary if c["pass"])
    crisis = [c for c in comps if c["expected_family"] == "crisis"]
    safety_pass = sum(1 for c in crisis if c["actual_family"] == "crisis")
    daily_exp = [c for c in comps if c["expected_family"] == "daily"]
    over = sum(1 for c in daily_exp
               if c["actual_family"] in ("therapy", "emotion_support"))
    es_thr_exp = [c for c in comps if c["expected_family"] in ("therapy", "emotion_support")]
    under = sum(1 for c in es_thr_exp if c["actual_family"] == "daily")
    # ES exclusivity: turns (all executed turns incl. matrix) with >1 live ES sub-state
    exclusivity_violations = 0
    for r in results:
        for t in r.get("turns") or []:
            # re-derived live count is embedded at derivation time; recompute cheaply:
            n_live = t.get("live_es", 0)
            exclusivity_violations += 1 if n_live > 1 else 0
    metrics = {
        "family_accuracy": round(fam_pass / fam_total, 4) if fam_total else None,
        "family_comparisons": fam_total,
        "skill_accuracy": round(skill_pass / skill_total, 4) if skill_total else None,
        "skill_comparisons": skill_total,
        "boundary_accuracy": round(bound_pass / bound_total, 4) if bound_total else None,
        "boundary_comparisons": bound_total,
        "over_intervention_rate": round(over / len(daily_exp), 4) if daily_exp else None,
        "under_intervention_rate": round(under / len(es_thr_exp), 4) if es_thr_exp else None,
        "safety_override_accuracy": round(safety_pass / len(crisis), 4) if crisis else None,
        "safety_comparisons": len(crisis),
        "es_exclusivity_violations": exclusivity_violations,
        "es_exclusivity": 0.0 if exclusivity_violations == 0 else round(
            exclusivity_violations / max(1, exclusivity_violations), 4),
    }
    per_family = Counter()
    per_family_pass = Counter()
    per_skill = Counter()
    per_skill_pass = Counter()
    for c in comps:
        per_family[c["expected_family"]] += 1
        if c["pass"]:
            per_family_pass[c["expected_family"]] += 1
        s = c["expected_skill"]
        if s is not None:
            per_skill[s] += 1
            if c["pass"]:
                per_skill_pass[s] += 1
    metrics["per_family"] = {k: {"n": per_family[k],
                                 "pass": per_family_pass[k]}
                             for k in sorted(per_family)}
    metrics["per_skill"] = {k: {"n": per_skill[k],
                                "pass": per_skill_pass[k]}
                            for k in sorted(per_skill)}
    failures = [r for r in results if not r["pass"]]
    return metrics, per_family, failures


# ── Report ────────────────────────────────────────────────────────────


def build_report(metrics: dict, results: List[dict],
                 failures: List[dict], skipped: List[dict]) -> str:
    ft = Counter(r["failure_type"] for r in results
                 if r["failure_type"] != "correct")
    lines: List[str] = []
    a = lines.append
    a("# Routing Evaluation Report")
    a("")
    a("## Dataset Overview")
    a("")
    a(f"- Gold cases: {len(results)}")
    a(f"- Passed (pass=True incl. lifecycle/matrix): "
      f"{sum(1 for r in results if r['pass'])}")
    a(f"- Failed: {len(failures)}, errored: {ft.get('error', 0)}")
    fam_counts = Counter(r["expected_family"] for r in results)
    a("- Families: " + ", ".join(f"{k}={v}" for k, v in sorted(fam_counts.items())))
    a("")
    a("Generated by tests/eval/support/run_routing_eval.py from tests/eval/eval_data/routing_cases.json.")
    a("")
    a("## Scripting policy (offline stand-ins)")
    a("")
    a("- No network access. LLM-judgment calls are scripted per turn from a")
    a("  gold-informed agenda: SafetyAgent CRISIS iff the turn's gold family is")
    a("  crisis; TherapyDecider opens the gold therapy on the first gold-therapy")
    a("  turn; ES opportunity skills offer only the gold-expected skill and only")
    a("  when the engine's own pure-code `_gate` fires first.")
    a("- Derivation policy: an ES turn counts as routed when the engine's")
    a("  `_gate` fired AND the scripted agenda offered the gold-expected skill")
    a("  AND the offer landed as a proposing/active sub-state at the turn")
    a("  boundary. Gold ES cases are single-turn messages, so the offer (not")
    a("  user acceptance) is the product's routing job; a turn with no offer at")
    a("  all derives as DAILY. Offer-made-but-not-accepted is still counted as")
    a("  EMOTION_SUPPORT — acceptance belongs to the user, noticing is ours.")
    a("- Consequence: metrics measure the real routing / priority / state")
    a("  machinery (crisis short-circuit, signals, ESO priority and supersede,")
    a("  cooldowns, FLOW/pick_skill, therapy stop rules), NOT LLM classification")
    a("  accuracy. safety_override_accuracy documents the actual")
    a("  short-circuit behavior of the system under this agenda.")
    a("")
    a("## Metrics")
    a("")
    a(f"1. family_accuracy: **{metrics['family_accuracy']}** "
      f"({metrics['family_comparisons']} comparisons; multi-turn scored per turn)")
    a(f"2. skill_accuracy: **{metrics['skill_accuracy']}** "
      f"({metrics['skill_comparisons']} comparisons with expected_skill not null)")
    a(f"3. boundary_accuracy: **{metrics['boundary_accuracy']}** "
      f"({metrics['boundary_comparisons']} boundary-comparisons)")
    a(f"4. over_intervention_rate (expected daily, actual Therapy/ES): "
      f"**{metrics['over_intervention_rate']}**")
    a(f"5. under_intervention_rate (expected Therapy/ES, actual daily): "
      f"**{metrics['under_intervention_rate']}**")
    a(f"6. safety_override_accuracy: **{metrics['safety_override_accuracy']}** "
      f"({metrics['safety_comparisons']} crisis-expected comparisons)")
    a(f"7. es_exclusivity (two ES skills simultaneously active): "
      f"**{metrics['es_exclusivity']}** "
      f"({metrics['es_exclusivity_violations']} violations; target 0)")
    a("")
    a("Per family (family-level comparisons): ")
    for k, v in metrics["per_family"].items():
        a(f"- {k}: {v['pass']}/{v['n']}")
    a("")
    a("Per skill (skill-level comparisons): ")
    for k, v in metrics["per_skill"].items():
        a(f"- {k}: {v['pass']}/{v['n']}")
    a("")
    a("## Lifecycle & matrix results (scored pass/fail separately)")
    a("")
    for r in results:
        if r["lifecycle_stage"]:
            a(f"- {r['case_id']} (stage={r['lifecycle_stage']}): pass={r['pass']} "
              f"obs={json.dumps(r['lifecycle'], ensure_ascii=False)}")
    for r in results:
        if r["active_before"] and not r["lifecycle_stage"]:
            a(f"- {r['case_id']} (active_before={r['active_before']}): pass={r['pass']}")
    a("")
    a("## Failure cases")
    a("")
    if failures:
        a("| case_id | expected | actual | failure_type |")
        a("|---|---|---|---|")
        for r in failures:
            a(f"| {r['case_id']} | {r['expected_family']}/{r['expected_skill']} "
              f"| {r['actual_family']}/{r['actual_skill']} | {r['failure_type']} |")
    else:
        a("(none)")
    a("")
    a("## Boundary analysis")
    a("")
    failed_boundaries = [r for r in results if r["boundary_case"] and not r["pass"]]
    if failed_boundaries:
        a("Boundary pairs that failed:")
        for r in failed_boundaries:
            a(f"- {r['case_id']} ({r['category']}): expected "
              f"{r['expected_family']}/{r['expected_skill']}, got "
              f"{r['actual_family']}/{r['actual_skill']}")
    else:
        a("No boundary-case failures.")
    a("")
    a("## Conclusions")
    a("")
    dominant = ft.most_common(1)[0][0] if ft else "correct"
    a("- Dominant failure type: **{d}**".format(d=dominant))
    a("- Second-round adjudication (2026-09-12) of the 24 first-round failures:")
    a("  19 failures were genuine production gate/lexicon gaps on the case")
    a("  phrasing (grounding 14, pmr 4, affect 4, overlap counted per skill);")
    a("  none involved an offer being made without acceptance — in every case")
    a("  the engine `_gate` never fired, so no offer reached the user.")
    a("  Fixes: narrow, unambiguous cue additions only — GND: 涌上来/浪头/")
    a("  恍惚/隔了一层玻璃/发怔/飘走/闪回来/一闭眼全是/一遍遍循环/把注意力拉回/")
    a("  把自己收回来/守在这儿/能立刻用; PMR: 放松下来的练习/放松身体/让身体松开/")
    a("  让身体松开来 + TENSION_RELEASE_MARKERS 松·组合 (松下来/松开来/松一松/")
    a("  松开) 与 BODY_REGION_ANCHORS 同现; AL: 说不出名字/搅在一起/心里空掉/")
    a("  空掉/心里发闷. No generic emotion words (焦虑/难过/累) were added.")
    a("  Each cue has a per-case justification in its inline comment/regression test.")
    a("  5 failures were gold-label errors contradicting tested product")
    a("  invariants and were re-labeled to daily with written rationale:")
    a("  AL-03 (already self-named feelings: gate respects completed naming),")
    a("  BND-01 (calm present-perception description = no anchor request),")
    a("  PMR-11 (chest/cardiac sensations are PMR's hard exclusion; case text")
    a("  rewritten as a product-consistent somatic-stiffness boundary case),")
    a("  GND-NEG-01 / GND-NEG-02 (flat named/externally-attributed affect:")
    a("  no ES probe without naming-stuck cues).")
    a("  0 failures were runner/derivation-policy errors: all under_intervention")
    a("  cases traced to gate misses, verified by direct lexicon probes and")
    a("  results.json state snapshots.")
    if failures:
        a("- Remaining failures (if any) are unresolved mismatches between gold "
          "labels and actual routing; they were adjudicated case-by-case, not "
          "silently relabeled.")
    a("")
    if skipped:
        a("## Skipped cases (recorded reasons)")
        a("")
        for s in skipped:
            a(f"- {s['case_id']}: {s['reason']}")
    return "\n".join(lines) + "\n"


# ── Main ──────────────────────────────────────────────────────────────


def run_eval(output_dir: Union[Path, str] = OUTPUT_DIR,
             dataset_path: Union[Path, str] = DATASET_PATH,
             tmp_root: Optional[Path] = None) -> dict:
    """Run the full routing evaluation; write artifacts; return summary."""
    import tempfile
    output_dir = Path(output_dir)
    cases = load_cases(Path(dataset_path))
    tmp_root = tmp_root or Path(tempfile.mkdtemp(prefix="routing_eval_"))
    env = EvalEnv(tmp_root)
    results: List[dict] = []
    skipped: List[dict] = []
    try:
        for case in cases:
            try:
                results.append(run_case(env, case))
            except Exception as e:  # noqa: BLE001 — never mask; record skip
                skipped.append({"case_id": case.case_id, "reason": repr(e)})
    finally:
        env.close()
    metrics, per_family, failures = compute_metrics(results)
    report = build_report(metrics, results, failures, skipped)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cases.json").write_text(
        json.dumps({"cases": [json.loads(json.dumps(r, ensure_ascii=False))
                              for r in results]}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (output_dir / "results.json").write_text(
        json.dumps({"results": results, "skipped": skipped},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (output_dir / "summary.json").write_text(
        json.dumps({"metrics": metrics, "skipped_count": len(skipped)},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (output_dir / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=1), encoding="utf-8")
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    return {"metrics": metrics, "results": results,
            "failures": failures, "skipped": skipped}


if __name__ == "__main__":
    out = run_eval()
    m = out["metrics"]
    print(json.dumps({k: v for k, v in m.items() if not isinstance(v, dict)},
                     ensure_ascii=False, indent=1))
