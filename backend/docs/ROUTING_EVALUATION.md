# Routing Evaluation — Repository Audit

Audit date: 2026-09-12. Read-only trace of the real routing path from a user message to a
final response, verified against source code (not just ARCHITECTURE.md). All paths are
relative to this backend directory (the backend sub-project root inside the repository).

---

## 1. Current Routing Pipeline (call chain as verified)

```
POST /chat  (server.py:93-104)
  └─ build_app → scheduler (src/bootstrap.py:31, build_scheduler)
      └─ Scheduler.handle_message(user_id, session_id, message)   src/core/scheduler.py:365
          ├─ trace_id + session lock                              scheduler.py:367-373
          ├─ read SessionState / profile / summary / Orchestration scheduler.py:374-387
          ├─ TurnContext(...)                                     scheduler.py:378-387
          ├─ 1. ESO.plan(ctx)            — SAME-turn, serial, BEFORE dialogue
          │     src/agents/emotion_support.py:123 (calls PMR.step → Grounding.step →
          │     Affect.maybe_plan in priority order; writes ctx.pmr_turn /
          │     ctx.grounding_turn / ctx.affect_labeling_plan)    scheduler.py:391
          ├─ 2. decision_task = DecisionLine.run_turn(ctx) (async, PARALLEL)  scheduler.py:394
          │     └─ DecisionLine.run_turn                        scheduler.py:182
          │         ├─ (a) _assess_safety → SafetyAgent.assess   scheduler.py:212,
          │         │        src/agents/safety_agent.py:31   [LLM, cheap model]
          │         │        emits CrisisDetected / CrisisCleared signals
          │         ├─ (b) risk == CRISIS → EARLY RETURN (short-circuits therapy
          │         │        decision AND emotion support)       scheduler.py:195-196
          │         ├─ (c) state.therapy is None → _maybe_decide_therapy       :236
          │         │        └─ token-threshold gate (WINDOW_TOKEN_THRESHOLD, default 0
          │         │           = evaluate every message)        scheduler.py:239-249
          │         │        └─ TherapyDecider.decide(ctx, window) [LLM]     :252
          │         │           src/agents/therapy_decider.py:55
          │         │           → SwitchToTherapy(therapy, start_seq) signal
          │         ├─     else → _orchestrate(ctx)              scheduler.py:265
          │         │        └─ TherapyAgentBase: run_assessment → initial_step_index
          │         │           (if step_index==-1) → run_step_judgment →
          │         │           run_current_skill → products_update → apply_judgment
          │         │           → marginal-utility record → verdict.decide
          │         │           (therapy/base.py:125,199,231,258;
          │         │            scheduler.py:280-334; EndTherapy on round_cap/utility)
          │         └─ (d) _emotion_updates → ESO.run(ctx, switched_to_therapy, risk) :229
          │                └─ ESO._decide_run (pure, deterministic)
          │                   emotion_support.py:187 → CONTINUE/SUPERSEDE/NONE
          │                   └─ ESO._run_pmr / _run_grounding / _run_affect
          │                      (engine.run → state_updates dict)
          ├─ 3. DialogueLine.speak(ctx) — streams tokens (awaited FIRST)      scheduler.py:398
          │     └─ HostDialogueAgent.reply_stream               src/agents/host_agent.py:248
          │         template chosen by CURRENT (stale-by-one-turn) snapshot state:
          │           state.crisis        → CRISIS_REPLY_PROMPT   host_agent.py:259-264
          │           state.therapy set   → therapy dialogue prompt (voice guide +
          │             last turn's Orchestration block)          host_agent.py:265-273
          │           else  (DAILY)       → DAILY_PROMPT + mutually-exclusive block:
          │             pmr_block | grounding_block | affect_block  host_agent.py:185-187
          ├─ yield dialogue_done (SSE)                            scheduler.py:407
          ├─ 4. await decision_task
          ├─ 5. Scheduler._apply_signals: state_updates → signals.apply() → risk_level
          │                                              scheduler.py:451-461 (signals:
          │                                              src/core/signals.py — SwitchToTherapy,
          │                                              EndTherapy, CrisisDetected/Cleared)
          │     → write state.json                                scheduler.py:421
          ├─ 6. SettlementLine.run_after_turn (profile + summary) scheduler.py:425,
          │     src/agents/settlement.py:73
          └─ 7. yield "final" {message, risk_state, crisis, [DEBUG_TALKS extras]}
                                                                scheduler.py:432-444
```

Key timing facts (important for any evaluation runner):

- **Two decision points for emotion support per turn**: `ESO.plan()` runs serially *before*
  the dialogue line so the current turn's reply contains the skill's guidance line
  ("same-turn questioning"); `ESO.run()` runs in the decision line so the *state* write
  lands at the turn boundary (applies next turn). scheduler.py:391 vs scheduler.py:207/391.
- **Signals are lag-by-one-turn by design**: `CrisisDetected` applied at end of turn N makes
  the Host reply with the crisis template on turn N+1 (scheduler.py:451-461;
  host_agent.py:259 reads `state.crisis` from the snapshot taken at turn start).
- **Therapy skill output also lags one turn**: the Orchestration consumed by the Host this
  turn was produced by last turn's `_orchestrate` (host_agent.py:248-258 docstring;
  scheduler.py:377 reads orchestration.json at turn start).

---

## 2. Module-by-Module Details

### 2.1 Crisis / Safety (DecisionLine step a)

- `src/agents/safety_agent.py:31` `SafetyAgent.assess(*, user_id, session_id, trace_id,
  user_text, current_risk) -> {"risk_level": RiskState, "risk_type": str}`.
  Cheap LLM model (`config.CHEAP_MODEL_NAME`), any failure falls back to SAFE (:40-50).
- 5-level ladder `RiskState` (`SAFE | LOW_RISK | MEDIUM_RISK | HIGH_RISK | CRISIS`,
  core/state.py:47-59).
- Crisis decision happens in `DecisionLine._assess_safety` (scheduler.py:212-227) —
  it compares the LLM result against `ctx.state.crisis` and emits `CrisisDetected` /
  `CrisisCleared` signals.
- **Short-circuit mechanics**: in `run_turn`, `risk == RiskState.CRISIS` returns immediately
  with no therapy decision and no emotion support (scheduler.py:195-196). However the
  *reply-side* crisis behavior still goes through the Host template on the **next** turn
  (CrisisDetected.apply, signals.py:52-65, also terminates therapy and clears
  affect/pmr/grounding sub-state, leaving `last_therapy.ended_reason="crisis"`).
- Output shape: `DecisionResult(signals, risk_level, risk_type, state_updates)`
  (scheduler.py:69-80); final SSE event exposes `{"risk_state": str, "crisis": bool}`
  (scheduler.py:432-436).

### 2.2 Therapy decision (`TherapyDecider`)

- Runs only in daily state (`state.therapy is None`) and only if the token gate passes
  (scheduler.py:236-261). `WINDOW_TOKEN_THRESHOLD` defaults to **0** (config.py:29), so by
  default therapy is decided on **every daily message**; sliding window `WINDOW_SIZE=5`.
- `src/agents/therapy_decider.py:55` `TherapyDecider.decide(ctx, window) ->
  {"need_therapy": bool, "therapy": str|None, "reason": str}`; therapy name must be in
  `THERAPIES = ("CBT","ACT","DBT","MI","SFBT")` (state.py:28) or it is nulled with a
  `therapy_decide_invalid` warning (:70-78).
- The decider keeps **no session state of its own**. The scheduler tracks
  `state.last_therapy_check_seq` and `state.pending_user_tokens` (state.py:106-107).
- Start: `SwitchToTherapy.apply` (signals.py:20-26) sets `owner=therapy_<key>`,
  creates `TherapyProgress(name, start_seq, step_index=-1, ...)`, and clears
  affect_labeling/pmr/grounding (therapy supersedes emotion support next turn).
- Continue/Stop: inside `_orchestrate` (scheduler.py:265-340) via the **marginal utility
  ledger** (`core/marginal_utility.py`, pure functions) — `EndTherapy` on
  `rounds >= THERAPY_MAX_ROUNDS` (10) or a utility verdict (stagnation/regression).
  Reason is preserved in `state.last_therapy = {name, rounds, ended_reason}`
  (signals.py:37-45). Contemporary LLM "continue-eval" is retired.
- In-session skill selection: `TherapyAgentBase.pick_skill` (therapy/base.py:97) picks the
  step's skill from `FLOW`/`BRANCHES`; each therapy subagent (cbt/act/dbt/mi/sfbt.py)
  defines `FLOW`, `BRANCHES`, `assessment_skill`, and routing hooks.

### 2.3 Emotion Support Orchestrator (ESO)

`src/agents/emotion_support.py` — thin, deterministic, **no LLM**.

- Specs with numeric priorities (emotion_support.py:64-68):
  `pmr=20 > grounding=15 > affect_labeling=10`, all `exclusive=True`.
  **Verified: PMR > Grounding > Affect Labeling. Matches the expected order.**
- Eligibility (emotion_support.py:117-119): `eligible(state) = not crisis and therapy is
  None and owner == "daily"` — ESO is gated to daily+no-crisis+no-therapy. High risk
  blocks via `_decide_run` (risk in HIGH_RISK/CRISIS → SUPERSEDE, :196-217).
- **Plan phase** (same turn, pre-dialogue) `ESO.plan(ctx)` (emotion_support.py:123-183):
  probes PMR (`pmr_engine.step(ctx)`) → if not live, Grounding (`grounding_engine.step`)
  → if not live, Affect (`affect_engine.maybe_plan`); writes `ctx.pmr_turn`,
  `ctx.grounding_turn`, `ctx.affect_labeling_plan`; never writes state. Returns
  `EmotionSupportDecision(action, skill, reason)`.
- **Run phase** (post-decision) `ESO.run(ctx, switched_to_therapy, risk)`
  (emotion_support.py:221-236) mirrors the priority probe via `_decide_run` (:187-219);
  on SUPERSEDE returns `{}` (no persistence — engine residue is cleared by signals or the
  run-subcalls); PMR occupying the slot clears grounding+affect state (:238-250), Grounding
  clears affect (:252-262).
- Per-engine lifecycle entry points (all two-phase, `_next_state` pattern, zero LLM in `run`):
  - **PMR** `src/agents/progressive_muscle_relaxation.py:152 step(ctx) -> Optional[pmr_turn
    dict with kind/line/_next_state]`; `:497 run(ctx, *, switched_to_therapy) -> dict`.
    Sub-state `state.pmr`: keys `status` (off/proposing/active/closed), `mode`, `phase`
    (tension/notice), `beats_done`, `body_parts`, `part_index`, `current_part`,
    `parts_completed`, `closing`, `start_seq`, `last_seq`, `close_reason`.
    LLM budget: 1 opportunity call (`pmr_opportunity`) + 1 response classifier
    (`pmr_response`) on ambiguous replies only.
  - **Grounding** `src/agents/grounding.py:128 step(ctx)`; `:361 run(ctx, ...)`.
    `state.grounding`: `status` (proposing/active/closed), `step` 0=visual, 1=tactile,
    2=auditory, 3=check, `trigger_seq`, `last_seq`, `close_reason`. Close reasons:
    declined/discomfort/exited/felt_better/completed/topic_shift/superseded (:44-50).
    Cooldown after close: `_COOLDOWN_SEQS = 6` flow entries (~3 turns) shared pattern with
    PMR and Affect.
  - **Affect Labeling** `src/agents/affect_labeling.py:122 maybe_plan(ctx) -> {candidates,
    reason} | None`; `:173 run(ctx, *, switched_to_therapy) -> dict`. Pure-code `_gate`
    (:85-108) with lexicons (`src/skills/affect_lexicon.py`); skills
    `affect_labeling_opportunity` / `affect_labeling_response` via `skill_registry`.
    `state.affect_labeling`: `status` (proposing/closed), `candidates`, `start_seq`,
    `close_reason` (answered|no_response|superseded), `outcome` {emotion_label,
    user_confirmed, user_correction, source, confidence}, `closed_seq` (:184-242, :270-282).

### 2.4 Host Agent (`src/agents/host_agent.py`)

- The ONLY speaker. `reply_stream(...)` (host_agent.py:248-287) selects one prompt template
  by priority **crisis > therapy > daily** based strictly on the turn-start state snapshot:
  - `state.crisis` → `CRISIS_REPLY_PROMPT` with risk level (:259-264; billing tag
    `daily_crisis`);
  - `state.therapy.name in THERAPY_VOICE_GUIDES` → `build_therapy_dialogue_prompt` =
    therapy-common instructions + verbatim voice guide + last-turn Orchestration block
    (current_step / judgment reason / skill technique / goal / contraindications)
    (:195-244; billing tag `therapy_cbt` etc.);
  - else daily → `DAILY_PROMPT` + profile + summary + recent + exactly one emotion-support
    block picked by `pmr_block | grounding_block | affect_block` (:185-187; billing
    tag `daily`). Affect block source: same-turn plan or last-turn proposing state
    (`_pick_affect_block`, :289-298).
- Host never routes itself — it only *renders* decisions already encoded in state + ctx.
  Billing tags (daily / daily_crisis / therapy_cb t) are audit labels for `llm_calls.jsonl`.

### 2.5 Scheduler

State lifecycle managed in `state.json`:
- `owner`: "daily" | "therapy_<cbt|act|dbt|mi|sfbt>" (audit label; state.py:28-42).
- `crisis: bool`, `risk_level: RiskState`, `therapy: TherapyProgress | None`.
- Emotion-support sub-states: `affect_labeling`, `pmr`, `grounding` (opaque dicts
  owned by their engines; state.py:108-110).
- Checkpoint bookkeeping: `last_therapy`, `last_therapy_check_seq`, `pending_user_tokens`.
- Single writer: `Scheduler._apply_signals` (scheduler.py:451) — state_updates first,
  signals after (so EndTherapy/CrisisDetected override), then risk_level. All changes take
  effect at the turn boundary for the next turn.

### 2.6 Settlement (`src/agents/settlement.py:79`)

`SettlementLine.run_after_turn(user_id, session_id, trace_id)` — profile incremental LLM
update (user-level lock) + summary regeneration on token threshold
(`SUMMARY_TOKEN_THRESHOLD=1500`). Not part of routing; observability only.

---

## 3. Reusable Public APIs & Schemas for Evaluation

Compose a runner from `build_scheduler` + fake LLM/store, exactly like the existing tests do
(tests/support/fakes.py, tests/integration/test_scheduler_phase2.py env fixture). Nothing requires rebuilding
any routing logic.

Core assembly & top-level entry:
- `src.bootstrap.build_scheduler(llm=None, session_store=None, profile_store=None) -> Scheduler`
  (bootstrap.py:31) — accepts injected fakes; provider is lazy so no API key needed for import.
- `Scheduler.handle_message(user_id, session_id, message) -> AsyncIterator[dict]` SSe events:
  `{"event": "token"|"dialogue_done"|"final"|"error"}` — the full real routing path.
- FastAPI app: `server.build_app(scheduler)` + `HTTPX ASGI transport` (tests/integration/test_server.py
  does this) if route-level is wanted.

Decision-level APIs (no dialogue generated):
- `DecisionLine.run_turn(ctx) -> DecisionResult` (scheduler.py:182) with
  `TurnContext(user_id, session_id, trace_id, message, state, profile, summary,
  orchestration, affect_labeling_plan, pmr_turn, grounding_turn)` (scheduler.py:48-66).
- `DecisionResult.signals / risk_level / risk_type / state_updates` (scheduler.py:69-80).
- `SafetyAgent.assess(...)` (safety_agent.py:31); `TherapyDecider.decide(ctx, window)`
  (therapy_decider.py:55).
- `EmotionSupportOrchestrator.plan(ctx) -> EmotionSupportDecision` and
  `.run(ctx, *, switched_to_therapy, risk) -> dict` (emotion_support.py:123, 221);
  `.active_skill(state)`, `.eligible(state)`, `.pmr/affect/grounding_is_live(state)`.
- Engines: `PMR.step/run`, `Grounding.step/run`, `AffectLabelingEngine.maybe_plan/run`
  (pure-ish, mockable through the two registered skills each: `<name>_opportunity`,
  `<name>_response` in `src.core.skill.skill_registry`).

Schemas / enums:
- `SessionState.to_dict()/from_dict()` (state.py:112-145) — full routing outcome record.
- `TherapyProgress` (state.py:64-93): name, start_seq, rounds, step_index, branch,
  basic_flow_complete, visited, utility_history.
- `Orchestration` (state.py:158-181): updated_seq, therapy, current_step, next_step,
  step_judgment, assessment, skill_output (with `_skill_name`), products, utility_eval.
- `RiskState` enum; `THERAPIES` tuple; `owner_for()` / `therapy_key_for_owner()`.
- Signals: `SwitchToTherapy(therapy, start_seq) | EndTherapy(reason) | CrisisDetected |
  CrisisCleared`, each `frozen dataclass` with `.apply(state)` (signals.py:76 union).
- ESO: `EmotionSupportAction` (NONE/START/CONTINUE/SUPERSEDE), `EmotionSupportSkillSpec`,
  `EmotionSupportDecision` (emotion_support.py:33-57).
- Skill layer: `SkillResult(skill_name, output, state_effects, error, success)`,
  `SkillType` (skill.py:9-24); therapy intervention skills all emit
  `{technique{name,description,steps}, conversation_goal, adaptation, contraindications}`
  (skill.py:253-272).
- Marginal utility: `UtilityRecord`, `compute_record`, `decide`, `state_score`
  (core/marginal_utility.py) — pure functions, directly unit-testable.

Observability:
- `logs/app.jsonl`: `turn_start → safety_done → therapy_decide_done | orchestration_done →
  utility_update → turn_end`, all keyed by `trace_id` (see ARCHITECTURE.md; scheduler.py
  app_log calls).
- `logs/llm_calls.jsonl` per-call records with agent tag.
- SSE `final` gives `risk_state`/`crisis`; with `DEBUG_TALKS=1` also `planned_skill`,
  `current_therapy`, `intervention_count` (scheduler.py:437-443).

---

## 4. Lifecycle & State Semantics

- **Safety precedence**: risk assessment runs first every turn (scheduler.py:192);
  `CRISIS` short-circuits run_turn before therapy & ESO (:195-196).
- **Crisis lifecycle**: entry = `CrisisDetected.apply` (end of the detecting turn →
  effective the turn after; sets crisis=True, owner=daily, therapy=None,
  last_therapy.ended_reason="crisis", clears affect/pmr/grounding — signals.py:52-65).
  Exit = `CrisisCleared.apply` (signals.py:71-74). Engines self-guard during crisis
  (affect_labeling.py:129, grounding.py:134-139, PMR gate CRISIS_CUES).
- **Emotion-support skill lifecycle** (per engine, mirrored by ESO):
  off → (gate + opportunity LLM passes) offering `proposing` (state written in run())
  → user accepts → `active` (skill-specific beat/step progression) → `closed`
  (reasons: declined / discomfort / exited / felt_better / completed / topic_shift /
  superseded) → cooldown `closed_seq`/`last_seq` + 6 flow entries blocks re-offer.
  `complete` = closed with reason completed after check step. `decline`/`exit`/`supersede`
  are all expressed via close_reason; `EmotionSupportAction` carries
  START/CONTINUE/SUPERSEDE with `complete/decline/exit` living in the engine state machines.
- **Affect outcomes** on close: `answered` (chosen/custom/unknown/refused, with
  emotion_label / user_confirmed / user_correction / source / confidence) or `no_response`
  (max one re-offer, never a third). affect_labeling.py:184-242.
- **ESO priority order (verified)**: PMR(20) > Grounding(15) > Affect(10), all exclusive,
  at most one active per turn (emotion_support.py:64-68). Grounding occupying the slot
  clears residual affect; PMR clears both. **The plan's expected priority (PMR > Grounding >
  Affect) matches the code.** (Note: emotion_support.py's older module docstring at :14-16
  still omits Grounding from the priority chain — cosmetic doc drift only, code is correct.)
- **Supersede by higher priority**: within ESO `_decide_run`, HIGH_RISK/CRISIS or an active
  therapy turn → SUPERSEDE for any live skill (emotion_support.py:196-217). Cross-module:
  `SwitchToTherapy.apply` and `CrisisDetected.apply` clear all three sub-states
  (signals.py:24-26, 63-65), and next turn `ESO.plan` returns
  `blocked_not_daily` because `owner != "daily"` (current-therapy solution is a
  `therapy_*` owner). Current therapy beats emotion support.
- **Exit from therapy**: there is no
  only `EndTherapy(reason="round_cap"|"stagnation"|"regression"|"unknown_therapy")` or
  crisis. Therapy cannot be "declined" — TherapyDecider simply never emits SwitchToTherapy.
- **Daily conversation**: there is **no explicit "no intervention" decision or record**.
  Daily is the implicit default: `owner="daily"`, `therapy=None`, no sub-state keys, and
  the Host's DAILY_PROMPT path with all three emotion-support blocks absent. An evaluation
  that wants a "daily / no-intervention / handled" ground truth must derive it from the
  absence of signals + empty updates (e.g. `decision.risk_level == SAFE`, no signals, empty
  `state_updates`) — nothing in the code labels a turn as "daily-noop".

---

## 5. Facts an Evaluation Must Respect

1. **Do not re-implement**: safety ladder short-circuit, therapy token threshold, ESO
   priority/exclusivity/cooldown, signalled state transitions, single-writer turn-boundary
   semantics — these exist in `src/core/scheduler.py`, `src/core/signals.py`,
   `src/agents/emotion_support.py` and are heavily regression-tested.
2. **Invoke the real path** via `build_scheduler()` with `FakeProvider` +
   scriptable stores (tests/support/fakes.py:21 `FakeProvider` with `script_complete` /
   `script_stream` bucketed by model; `FakeSink` for billing). Model-bucket routing:
   dialogue uses the primary model, safety/decisions the cheap model — see
   tests/integration/test_scheduler_phase2.py env fixtures (they also use `MEMORY_*`-style tmp dirs and
   monkeypatched config). Existing tests to model after:
   - `tests/integration/test_scheduler_phase2.py` — full turn flow via `Scheduler.handle_message`,
     ordering of SSE events, crisis effective next turn (:109), safety failure fallback,
     session serialization.
   - `tests/integration/test_cross_module_orchestration.py` — system-level priority regressions:
     crisis supersede, therapy occupying the emotion-support slot, ESO plan/run as two
     guard gates, LLM accounting assertions.
   - `tests/integration/test_emotion_support.py`, `tests/integration/test_grounding.py`, `tests/integration/test_pmr.py`,
     `tests/integration/test_affect_labeling.py` — engine lifecycle and priority (incl.
     `test_eso_plan_selects_pmr_over_affect`).
   - `tests/unit/test_therapy_decider.py`, `tests/integration/test_therapy_phase3.py`,
     `tests/unit/test_therapy_selection.py`, `tests/unit/test_marginal_utility.py` — therapy decision
     and stop rules with pure inputs.
   - Golden prompts frozen byte-level in `tests/golden/snapshots.json`
     (`tests/golden/test_snapshots.py`, regenerate with `tests/support/golden_v2_capture.py`).
3. **Expected script order for a scripted run** (what each decision consumes):
   dialogue stream (primary model), safety (`CHEAP_MODEL_NAME`), then per branch:
   therapy_decider / therapy assessment / judgment / skill, affect opportunity or response,
   pmr opportunity/response, grounding — an eval runner scripting `FakeProvider` must
   enqueue responses per model bucket in that call order.
4. **Lag semantics**: assertions about a crisis/therapy/skill change must check either
   state at the *turn boundary* (after `_apply_signals`) or behavior on the *next* turn;
   same-turn ChatResponse should show the ESO guidance block from `plan()` while state
   flips at boundary. `dialogue_done` fires before decision-line completion.
5. **Where outcomes are recorded** for inspection after each turn:
   `state.json` (SessionState: crisis, risk_level, owner, therapy, pmr/grounding/
   affect_labeling), `orchestration.json` (Orchestration incl. `_skill_name`,
   `step_judgment.action`, `utility_eval.stop`), SSE `final`, `logs/app.jsonl` decision
   events, `logs/llm_calls.jsonl` agent tags (grounding_opportunity etc.).
6. **Config knobs** (`src/utils/config.py`): `WINDOW_SIZE=5`,
   `WINDOW_TOKEN_THRESHOLD=0` (default evaluates every daily message), `THERAPY_MAX_ROUNDS=10`,
   `SUMMARY_TOKEN_THRESHOLD=1500`, `RECENT_TURNS`.
7. **Grounding skill banned guidance**: breathing/mindfulness/emotion-naming are hard
   exclusions inside the grounding prompt (commit dacbae3); an eval checking grounding
   replies should assert these absences rather than only presence of the line.
8. **Known doc/code drift**: emotion_support.py header comment (:14-16) and
   test_cross_module_orchestration.py header still state the pre-grounding priority chain
   without Grounding; actual SPECS and `_decide_run` include Grounding between PMR and
   Affect. ARCHITECTURE.md matches the code.
