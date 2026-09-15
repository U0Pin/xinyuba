# Therapy Evaluation — Repository Audit

Audit date: 2026-09-12. Read-only trace of the therapy-specific chain (therapy start →
per-family orchestration → stop), verified against source code. Companion to
`docs/ROUTING_EVALUATION.md`, which covers the surrounding top-level pipeline
(/chat → Scheduler → parallel lines → settlement). All paths are relative to this backend
directory (the backend sub-project root inside the repository). See `docs/test_report.md`
for the recorded test baseline.

---

## 1. Therapy Pipeline in Detail (diagram + file:line per node)

```
Daily turn N  (state.therapy = None, owner = "daily")
  Scheduler.handle_message                                 src/core/scheduler.py:365
    ├─ ESO.plan(ctx)                                       scheduler.py:391
    │    └─ eligible() = no crisis + therapy is None + owner daily
    │       src/agents/emotion_support.py:118-119  (therapy always wins over ES)
    ├─ decision_task = DecisionLine.run_turn(ctx)          scheduler.py:394 → 182
    │    ├─ (a) _assess_safety → SafetyAgent.assess [LLM, cheap model]  scheduler.py:212
    │    │       CRISON ⇒ early return (therapy decision itself paused) scheduler.py:195-196
    │    ├─ (b) state.therapy is None → _maybe_decide_therapy           scheduler.py:236
    │    │       ├─ token-threshold gate: pending_user_tokens accumulates
    │    │       │  user-entry tokens (est_tokens_half, char/2) since last
    │    │       │  check; if pending < WINDOW_TOKEN_THRESHOLD → no evaluation,
    │    │       │  only accumulate                     scheduler.py:238-250
    │    │       ├─ window = read_flow_tail(WINDOW_SIZE=5)              scheduler.py:252
    │    │       ├─ TherapyDecider.decide(ctx, window) [LLM, main model]
    │    │       │    src/agents/therapy_decider.py:55
    │    │       │    ├─ prompt: THERAPY_DECISION_PROMPT + window transcript
    │    │       │    │  (last 5 turns) + user profile JSON + summary list +
    │    │       │    │  current message                therapy_decider.py:36-53
    │    │       │    ├─ output schema {need_therapy, therapy, reason}  :83-89
    │    │       ├─ failure visible: ajson(failure_log=therapy_decide_failed)
    │    │       │  + invalid-therapy warning           therapy_decider.py:64-77
    │    │       └─ need+valid name → SwitchToTherapy(therapy, start_seq)
    │    │          signal                              scheduler.py:258-263
    │    └─ (c) _emotion_updates → ESO.run(switched_to_therapy, risk)   scheduler.py:229
    ├─ DialogueLine.speak — turn N reply still uses DAILY template (lag!)
    ├─ _apply_signals → SwitchToTherapy.apply:            src/core/signals.py:16-29
    │      owner = "therapy_<family>", state.therapy = TherapyProgress(
    │      name, start_seq, rounds=0, step_index=-1),
    │      affect_labeling / pmr / grounding = None       signals.py:20-28
    └─ write state.json                                   scheduler.py:421

Turn N+1 (therapy active, step_index=-1)
  ├─ ESO.plan: eligible() is False → "blocked_not_daily"  emotion_support.py:86-90
  ├─ DecisionLine.run_turn:
  │    safety assess (unchanged)  → risk != CRISIS path
  │    state.therapy set → _orchestrate(ctx)              scheduler.py:265-334
  │      ├─ agent = therapy_agents[therapy.name]; missing → EndTherapy(
  │      │    reason="unknown_therapy")                   scheduler.py:269-272
  │      ├─ therapy = ctx.state.therapy.copy(); products = prev.products
  │      │  (from orchestration.json, lagged by design)   scheduler.py:275-277
  │      ├─ 1) run_assessment(ctx) [LLM via assessment skill]
  │      │     src/agents/therapy/base.py:125-165
  │      │      inputs: user_text, history (last 6, therapy transcript),
  │      │      emotion={}, profile{attachment_style, readiness},
  │      │      current_<family>_state = prev assessment   base.py:142-156
  │      ├─ step_index == -1 → initial_step_index(assessment) routing
  │      │     (per-family, see §3)                        scheduler.py:279-281
  │      ├─ 2) run_step_judgment [LLM]                     base.py:199-211
  │      │      prompt: STEP_JUDGMENT_PROMPT.format(therapy_name) + FLOW names
  │      │      + branch names + current step + assessment JSON + products JSON
  │      │      + last 6 therapy-transcript entries        base.py:169-186
  │      │      parse_step_judgment clamps action ∈ {stay,advance,go_to,complete}
  │      │                                                 base.py:188-197
  │      ├─ 3) run_current_skill [LLM via step's skill]    base.py:231-256
  │      │      judgment_only steps → None (no call)       base.py:233-234
  │      │      inputs include assessment_block + intervention_extra_inputs
  │      │                                                 base.py:213-229
  │      ├─ products_update (per-family)                   scheduler.py:285-289
  │      ├─ 4) apply_judgment → advance/go_to/complete/stay; therapy.rounds += 1
  │      │                                                 base.py:258-296
  │      │                                                 scheduler.py:296
  │      ├─ 5) marginal-utility ledger: state_score → compute_record →
  │      │     decide(utility_history)                     scheduler.py:300-312
  │      │                                                 src/core/marginal_utility.py
  │      └─ 6) stop: rounds ≥ THERAPY_MAX_ROUNDS(10) → EndTherapy("round_cap");
  │            else verdict.stop → EndTherapy(rule)        scheduler.py:313-317
  │      write_orchestration(sid, orchestration.to_dict()) scheduler.py:320
  │        orchestration.app_log "orchestration_done"      scheduler.py:321
  │      return state_updates {"therapy": therapy}         scheduler.py:326
  ├─ Dialogue reply: therapy template consumed THIS turn = PREVIOUS turn's
  │  orchestration (lag-by-one, doc'd in host_agent.py:248-258)
  │  src/agents/host_agent.py reply_stream :248
  │    └─ state.therapy.name in THERAPY_VOICE_GUIDES → build_therapy_dialogue_prompt
  │       :265-273 → template at :195-244; LLM agent tag = owner_for(name)
  │       ("therapy_<family>")                             :273
  │    CRISIS: state.crisis from snapshot beats therapy    :259
  ├─ ESO.run: _decide_run sees ctx.state.therapy present → SUPERSEDE
  │  "blocked_by_higher_priority"                          emotion_support.py:192-226
  └─ _apply_signals → EndTherapy.apply writes state.last_therapy {name, rounds,
     ended_reason} and clears therapy / owner back to "daily"  signals.py:32-44
```

### Therapy transcript definition

`current_therapy_transcript` (src/store/session_store.py:49-62) scans the flow
backwards collecting the contiguous run of entries whose `owner` starts with
`therapy_` — so the "session" is owner-contiguous; any daily/crisis reply
breaks the run even though `state.therapy` survives (see §6 risk).

---

## 2. Therapy Session Lifecycle

- **Start conditions**: only through `TherapyDecider` in daily state. Triggered when
  (i) not in crisis, (ii) `state.therapy is None`, (iii) accumulated user tokens since
  `last_therapy_check_seq` ≥ `WINDOW_TOKEN_THRESHOLD` (env; default `0` = every
  message; scheduler.py:238-253, config.py:29). `SwitchToTherapy` is applied at the
  turn boundary; the therapy's first spoken turn is N+1 (signal lag same as crisis).
- **Continue**: every subsequent turn in therapy state runs `_orchestrate`
  (assessment + judgment + skill + utility). `step_index=-1` on the first orchestrated
  turn routes to the per-family initial step from the assessment (scheduler.py:279-281).
- **Stop conditions** (signals.py:32-44; marginal_utility.py:120-156; scheduler.py:313-317):
  1. `round_cap`: `therapy.rounds >= config.THERAPY_MAX_ROUNDS` (default 10, config.py:31);
  2. `utility → regression`: single-round ΔS ≤ −0.24, or two consecutive ΔS ≤ −0.12
     (marginal_utility.py:144-152);
  3. `utility → stagnation`: 2 consecutive rounds with no gain (no progress action, no
     new technique, ΔS < 0.03)  (marginal_utility.py:154-158);
  4. `unknown_therapy` (agent registry miss) scheduler.py:269-272;
  5. `crisis`: CrisisDetected ends therapy with ended_reason="crisis"  signals.py:47-58.
  - Cold-start: ledger shorter than `min_rounds=3` never stops (marginal_utility.py:139-140).
  - Note: `complete` action sets `basic_flow_complete` (base.py:261-263) but does NOT
    stop the session — no LLM "continue?" evaluation exists anymore (marginal_utility
    docstring; retired ContinueEval). Stopping is purely formulaic + hard caps.
- **Records**: `state.last_therapy = {name, rounds, ended_reason}` survives in state.json
  for Host handoff / observation (signals.py:36-40).
- **Signals inventory**: `SwitchToTherapy`, `EndTherapy`, `CrisisDetected`, `CrisisCleared`
  (src/core/signals.py:15-69). App-log events per turn: `safety_done`,
  `therapy_decide_done`, `utility_update`, `orchestration_done`
  (scheduler.py:212,257,312,322).

---

## 3. Per-Family Implementation Inventory

All five agents subclass `TherapyAgentBase` (src/agents/therapy/base.py:45). All
intervention skills subclass `TherapyInterventionSkill` (src/core/skill.py:207-280):
ONE SHARED prompt envelope (context block + last-4 history + user message + output
schema), ONE shared output shape `{technique:{name,description,steps},
conversation_goal, adaptation{interaction_style,tone_adjustment,pace_adjustment,
culture_note}, contraindications[]}` (skill.py:253-272). Differences across families
live in: (a) the therapy agent's FLOW/BRANCHES/routing hooks, (b) assessment skills
and their backend formulas, (c) per-skill prompt_template + technique enum, (d)
fallback selection logic, (e) state_effects keys.

| Family | Agent file | FLOW steps | assessment skill | intervention skills / techniques |
|---|---|---|---|---|
| CBT | agents/therapy/cbt.py:11 | identify_thought → classify_distortion → challenge → reframe → evaluate_effect (judgment_only) + branch `behavioral_activation` (cbt.py:15-28) | **None** (cbt.py:14 — analysis done by steps 1/2) | extract_automatic_thought, detect_cognitive_distortion [analysis]; generate_reframe (alternative_causes…), evaluate_evidence (pros_cons_table…), decatastrophize (chain_analysis…), plan_behavioral_activation (activity_scheduling…) — prompts src/skills/prompts/cbt.py |
| ACT | agents/therapy/act.py:20 | defusion/acceptance/present_moment/self_as_context/values/committed_action (act.py:27-35) | act_state_assessment (5-dim indices; backend `psychological_flexibility` = 0.35(1−fus)+0.25(1−avo)+0.20open+0.10ali+0.10act, act.py skill src/skills/act.py:88-95; src/core/act_state.py:198-212) | act_defusion (thought_labeling…), act_acceptance, act_present_moment, act_self_as_context, act_values, act_committed_action — technique enums src/skills/techniques.py:13-48 |
| DBT | agents/therapy/dbt.py:22 | mindfulness → distress_tolerance → emotion_regulation → interpersonal_effectiveness | dbt_state_assessment (5 dims + backend `dbt_intervention_priority` = 0.25ED+0.25DL+0.15IMP+0.15IC+0.20(1−MA), src/skills/dbt.py:43-63) | dbt_mindfulness (wise_mind, observe_describe), dbt_distress_tolerance (radical_acceptance, self_soothe), dbt_emotion_regulation (check_the_facts, opposite_action), dbt_interpersonal_effectiveness (dear_man, GIVE) — techniques.py:56-77 |
| MI | agents/therapy/mi.py:16 | explore → evocation → commitment | mi_state_assessment (ambivalence/sustain_talk/change_talk/self_efficacy/change_readiness + backend `mi_intervention_need`, src/skills/mi.py:40-63) | mi_explore (oars, agenda_mapping), mi_evocation (darn_cat, importance_confidence_ruler), mi_commitment (change_plan, commitment_language) — techniques.py:85-100 |
| SFBT | agents/therapy/sfbt.py:14 | resource_exploration → exception_exploration → future_construction | sfbt_state_assessment (resource_awareness / problem_orientation / goal_clarity + backend `sfbt_intervention_need`, src/skills/sfbt.py:41-58) | sfbt_resource_exploration (coping_questions, compliments_strengths), sfbt_exception_exploration (exception_finding, scaling_questions), sfbt_future_construction (miracle_question, small_step) — techniques.py:108-126 |

### Family routing (mechanisms differ per family — Q12)

Initial step and advance-destination are deterministic **code rules over the LLM
assessment**, all in agents/therapy/*:

- **CBT** `pick_skill` routes *within* the shared `challenge` step by distortion type:
  catastrophizing/fortune_telling → decatastrophize; mind_reading/personalization/
  self_blame/overgeneralization/black_white → generate_reframe; else
  evaluate_evidence (cbt.py:30-50). `products_update` persists cumulative
  automatic_thoughts/distortions with dedup (cbt.py:62-102).
- **ACT** initial step by escalation/openness/fusion/avoidance/alignment thresholds
  (act.py:44-60); advance = first `recommended_processes` entry not yet visited;
  exhaustion → flow complete (act.py:62-67).
- **DBT** initial = first recommended module (DBT only uses recommendations, no
  threshold override; dbt.py:42-47); advance like ACT.
- **MI** initial stage by change-talk/sustain-talk/readiness/self-efficacy/ambivalence
  thresholds (mi.py:27-49); default advance is linear +1 (base.py:90-95).
- **SFBT** initial by problem_orientation=="problem_stuck" / resource_awareness<0.4 /
  goal_clarity<0.5 (sfbt.py:19-31).
- **SFBT adaptation override**: SfbtIntervention._adaptation pins
  tone_adjustment="hopeful" regardless of LLM output (sfbt.py:189-196) — unique
  family-marker at output-construction time.
- Technique name is validated per-skill against `valid_techniques` enum; invalid/
  missing name falls back to `default_technique` (skill.py:253-256). Technique values
  never cross family boundaries (separate enums in src/skills/techniques.py).
- `state_effects` keys distinguish families:
  `session.cbt_intervention_log` / `act_intervention_log` / `dbt_intervention_log` /
  `mi_intervention_log` / `sfbt_intervention_log` (skill.py:270 per-skill
  state_effects_value) plus `session.act_state` / `dbt_state` / `mi_state` /
  `sfbt_state` writes on assessment, and CBT's `session.automatic_thoughts` /
  `session.distortion_analysis` / `profile.distortion_history`
  (src/skills/cbt.py:107,168). Note: in the Phase 3 pipeline these state_effects are
  returned inside SkillResult but the scheduler's `_orchestrate` does NOT apply them
  to SessionState — persistence is via orchestration.products / utility_history.

---

## 4. Structured Observables for Offline Fidelity Evaluation

Every field below is deterministic (post-LLM-clamped / backend-computed) and
readable from disk without re-running the LLM.

### state.json (`<DATA_ROOT>/sessions/<sid>/state.json`, written only by
`Scheduler._apply_signals`, scheduler.py:421; model src/core/state.py:73-136)

| JSON path | Meaning |
|---|---|
| `owner` | "therapy_cbt" \| "therapy_act" \| … \| "daily" \| "daily_crisis"; audit label only (state.py:9-31) |
| `therapy.name` | family key "CBT"/"ACT"/"DBT"/"MI"/"SFBT" — the authoritative session family |
| `therapy.start_seq` | flow seq at SwitchToTherapy |
| `therapy.rounds` | orchestrated turns so far (per-round increment, scheduler.py:296) |
| `therapy.step_index` | current FLOW index (−1 until first orchestration) |
| `therapy.branch` | CBT only: "behavioral_activation" or null |
| `therapy.basic_flow_complete` | true after judgment complete / last-step advance |
| `therapy.visited` | list of completed adult step names (ACT/DBT recommended-loop driving data → next_step) |
| `therapy.utility_history` | list of UtilityRecord dicts: {round, action, progress, state_score, state_delta, novelty, technique, utility, ewma, gain} (marginal_utility.py:42-69) — full replayable stop-decision ledger |
| `last_therapy` | {name, rounds, ended_reason: stagnation\|regression\|round_cap\|unknown_therapy\|crisis} (signals.py:36-40)|
| `risk_level`, `crisis` | safety context |
| `last_therapy_check_seq`, `pending_user_tokens` | decider trigger bookkeeping |

### orchestration.json (`SessionStore.write_orchestration`, session_store.py:202;
model src/core/state.py:139-181; written every therapy turn, scheduler.py:320)

| JSON path | Meaning |
|---|---|
| `updated_seq` | monotonic orchestration counter |
| `therapy` | family name again ( redundancy = cross-check against state.therapy.name) |
| `current_step` / `next_step` | step names (before / after apply_judgment) |
| `step_judgment` | {action, target_step, achieved, reason} — clamped, base.py:188-197 |
| `assessment` | full per-family assessment dict (LLM clamped + backend formulas): ACT fusion/avoidance/openness/alignment/activation {index,state,confidence,evidence} + psychological_flexibility + escalation_level + recommended_processes; DBT 5 dims {index,state,confidence,evidence} + dbt_intervention_priority + recommended_processes(sorted); MI ambivalence/sustain_talk/change_talk/self_efficacy/change_readiness + mi_intervention_need; SFBT resource_awareness/problem_orientation/goal_clarity + sfbt_intervention_need |
| `skill_output` | as produced by run_current_skill: for INTERVENTION the full TherapyInterventionSkill shape PLUS `_skill_name`; for analysis steps (CBT steps 1/2) the extract/distortion payload; `{}` / absent for judgment_only steps (utility novelty=None) |
| `skill_output.technique.name` | enum-validated technique — per-family technique lexicon (fidelity signal #1) |
| `skill_output.adaptation.interaction_style` | per-skill style; SFBT always carries tone "hopeful" (fidelity signal #2) |
| `skill_output.contraindications`... | contraindications[] {condition, reason} |
| `products` | family-specific accumulated products: CBT {automatic_thoughts[], primary_thought, distortions[], distortion_summary{dominant_pattern,distortion_count,severity}, no_automatic_thought_detected}; others = passthrough snapshot from CBT-only products_update (ACT/DBT/MI/SFBT use default base.products_update returning products unchanged — base.py:112-115) — i.e. products is CBT-distinct |
| `utility_eval` | last record + {stop, rule_hit} — verdict at write time (scheduler.py:314-319) |

### logs/app.jsonl (`app_log` entries, src/core/logging_utils.py:37-48; each line
{ts, level, module, event, trace_id, **fields})

- `decision/therapy_decide_done` {need_therapy, therapy, reason} (scheduler.py:257)
- `decision/therapy_decide_failed` and `decision/therapy_decide_invalid` warnings
  (therapy_decider.py:64-77)
- `decision/utility_update` {round, action, progress, state_score, state_delta,
  novelty, technique, utility, ewma, gain, stop, rule} (scheduler.py:311-312)
- `decision/orchestration_done` {therapy, step, judgment, skill} (scheduler.py:321-323)
- therapy warnings: `therapy/assessment_skill_missing`, `assessment_call_failed`,
  `assessment_failed`, `skill_call_failed`, `skill_failed` (base.py:136-165, 246-254)
- `decision/unknown_therapy` (scheduler.py:270)

### logs/llm_calls.jsonl (LLMCallSink, logging_utils.py:50+)

One line per LLM call with agent tag: `therapy_decider`,
`therapy_{family}_assessment`, `therapy_{family}_judgment`,
`therapy_{family}_skill`, `therapy_{family}` (Host dialogue), plus `daily_crisis`
etc. — the agent tag doubles as the pipeline-stage ground truth.

### flow.jsonl (`append_flow` entries, DialogueLine.speak scheduler.py:145-176)

Each entry {role, owner, text, trace_id, tokens?, latency_ms?}; `owner`
"therapy_*" is the transcript-partition key used by
`current_therapy_transcript` (session_store.py:49).

### SSE final event

`final` includes DEBUG_TALKS extras when enabled: `planned_skill` (latest
orchestration skill `_skill_name`), `current_therapy`, `intervention_count`
(scheduler.py:429-438).

---

## 5. Existing Test Coverage

tests/integration/test_therapy_phase3.py (36 tests): fake-LLM scripted pipeline. Asserted
behaviors include:

- CBT full flow progression + step indices per round + products merging +
  end-of-flow via utility (tests/integration/test_therapy_phase3.py:122-181);
  round_cap stop (:183-193); go_to back to challenge branch (:195-208).
- Per-family initial routing: ACT identity-fusion→self_as_context (:212-231),
  MI change-talk→evocation (:233-252), SFBT problem_stuck→exception (:254-270),
  DBT recommended-order (:273-298).
- Decider: SwitchToTherapy signal → owner "therapy_mi" (:300-313); next-turn
  therapy dialogue uses MI voice guide + orchestration + owner-tagged flow
  (:315-346); token-threshold blocks decider (:348-357).
- Anti-hogging prompt-rule regression tests (CBT-over-MI, DBT-over-MI,
  SFBT signal requirement, conflict examples, "no fixed priority")
  (:370-404); parametrized all-five-families routing (:406-421).
tests/unit/test_therapy_decider.py: LLM-failure warning log (:44), invalid therapy
name warning (:63), need=false no warning (:82), happy path (:97).
tests/unit/test_therapy_selection.py: registry roundtrip + FlowStep skills registered
+ assessment skills ANALYSIS-typed + branch return targets exist (:29-71).
tests/unit/test_marginal_utility.py: pure-function ledger/verdict math.
test_mi_skills.py / test_sfbt_skills.py / test_act_skills.py /
test_act_state.py: per-skill prompt/parse/fallback/routing formula units.
test_cross_module_orchestration.py: therapy-beats-emotion-support priority.
test_eval_hide_therapy_name.py / tests/eval/: questionnaire runner artifacts.

---

## 6. Facts a Therapy Evaluation Must Respect

1. **Do not re-implement routing/matrices**: the family decision is ONE
   `TherapyDecider` LLM call over a window of ≤5 turns + profile + summary
   (therapy_decider.py:36-78). Its "routing rules"/"signal table" live entirely
   inside THERAPY_DECISION_PROMPT (prompts/therapy.py:14-81) and are
   byte-frozen by golden snapshots — prompt edits require recapture.
2. **No second-stage skill-selection LLM exists** (Q3 answer): within a step,
   skill choice is the step's `skills[0]` / `pick_skill` code rule
   (base.py:97-110); within a skill, technique choice is the LLM but
   enum-constrained. An evaluator should verify family consistency via the
   technique-enum membership + `_skill_name`, not expect a separate record.
3. **Scripting policy**: judgment action drives progress; for CBT the
   judgment/skill sequence and `products` accumulation (dedup semantics,
   cbt.py:62-102) shape which skill runs at `challenge`. Reply template for
   turn N uses turn N−1's orchestration (lag) — a live-replay evaluator must
   either account for the lag or script two turns before asserting the
   therapy voice.
4. **Stop semantics are fully observable**: utility_history in state.json
   reproduces `decide()` exactly (style: replay `compute_record`+`decide`
   over the saved ledger to verify every stop reason). `complete` ≠ stop.
5. **Boundary risk areas (Q13)**:
   - The decider prompt is family-partitioning by LLM only; once in-therapy,
     no code re-verifies family. Cross-family leak = misdecided family; the
     observable signals are assessment dimension semantics (e.g. mixing ACT
     fusion talk inside an MI session still runs MI skills).
   - therapy-relevant prompts are all family-typed (per-skill prompt
     template, per-family voice guide, per-family assessment schema);
     nothing shared encodes another family's vocation — except THERAPY_DECISION's
     whole ESO/grounding vocabulary is daily-only.
   - `current_step` on the dialogue side comes from the lagged file; a family
     switch mid-session (EndTherapy → next SwitchToTherapy) is invisible to
     Host for one turn if new therapy starts the same turn the old ended —
     technically possible via signal ordering in `_apply_signals`
     (state_updates first, then signals — scheduler.py:448-459).
   - `current_therapy_transcript` partitions by owner label in flow.jsonl; if
     Host replies daily (e.g. crisis lag), the therapy transcript run breaks
     even though `state.therapy` survives — measurable as a discontinuity in
     the owner-labelled flow.
   - state_effects returned by skills (e.g. `session.act_state: write`) are
     not applied to SessionState by the scheduler — any evaluator assuming
     skill-level state writes will find nothing in state.json; the actual
     per-turn assessment is only in orchestration.json.
   - CBT has no assessment skill (cbt.py:14): `assessment` in orchestration is
     None and CBT `state_score` derives from skill_output classification
     confidence (marginal_utility.py:101-110) — a CBT fidelity check cannot
     expect a state assessment payload.
   - `emotional_intensity` in ACT assessment comes from `emotion.arousal`,
     but `run_assessment` always passes `emotion: {}` (base.py:147) —
     arousal is always 0.0, escalation always baseline in pipeline. Known
     pre-existing issue (src/skills/act.py:26-28 docstring). Similarly
     `profile.readiness` uses fallback key "cognitive_readiness"
     (base.py:151-154).
