# Therapy Evaluation Report (2026-09-12)

## Dataset Overview

- Gold cases executed: 176 (skipped 0)
- Passed machinery+labels: 176, failed: 0
- Families: CRISIS=10, EMOTION_SUPPORT=5, None=21, act=30, cbt=31, dbt=26, mi=28, sfbt=25
- Categories: ambiguous=8, boundary=40, easy=24, lifecycle=1, medium=86, multi_turn=5, negative=12

Derived from docs/THERAPY_EVALUATION.md structured observables: state.json therapy/crisis/ES sub-states, orchestration.json assessment/skill_output/products, sink-record llm tags (logs/llm_calls.jsonl equivalent via FakeSink).

## Scripting policy — RUNNER LIMITATIONS, read before the metrics

- No network. LLM-JUDGMENT slots are scripted per turn from gold
  (SafetyAgent risk, TherapyDecider verdict, assessment payload,
  step judgment action/technique). Consequently:
  - family_accuracy measures machinery acceptance of a gold-scripted
    SwitchToTherapy, NOT the decider LLM's real classification.
  - expected-intervention passes are agenda-driven: we script the
    technique; the REAL check being measured is the enum-clamp +
    pick_skill code path, not the LLM's technique choice.
  - Decider negatives (expected_no_therapy) are scripted 'decider says
    no'. They measure NO-OP mechanics (no SwitchToTherapy signal, no
    ES/ESO state, owner stays daily), NOT production over-intervention
    behavior. This is a scripting limitation and is flagged openly.
- Real (unscripted) machinery exercised: signal application & owner
  transitions, ESO plan/supersede vs therapy, code pick_skill routing
  (CBT distortion routing, ACT/DBT/MI/SFBT initial-step routing),
  step judgment clamping, utility ledger + stagnation/regression and
  round-cap stop rules, products accumulation, EndTherapy reasons.

## Metrics (12)

- family_accuracy: **1.0**
- skill_accuracy: **1.0**
- boundary_accuracy: **1.0**
- over_intervention_rate: **0.0**
- under_intervention_rate: **0.0**
- crisis_override_accuracy: **1.0**
- therapy_continuity: **1.0**
- theory_mixing_rate: **0.0**
- fidelity_rate: **1.0**
- lifecycle_error_rate: **0.0**
- family_comparisons: 168
- skill_comparisons: 149

## Therapy Continuity

Definition: an in-session turn counts as continuous if the therapy
session stayed active (owner did not move to daily) and its effective
family (the task-derived family, e.g. 'cbt'/'mi', for therapy sessions
— matching what the pass/fail computation uses) remained within the
case's acceptable families. A miss is counted only when a session
genuinely dropped. Final value: therapy_continuity = **1.0**.

Fidelity per family (checks a-f, g excluded from rate):
- cbt: 279/279
- act: 240/240
- dbt: 182/182
- mi: 196/196
- sfbt: 200/200

## Known production findings surfaced (from audit; not runner failures)

- CBT: no assessment skill — orchestration.assessment is None every turn (state_score uses classification confidence; audit §3/§6.7).
- ACT: escalation_level/monitoring dims always baseline — base.run_assessment passes emotion:{} so arousal=0.0 (audit §6.7, src/skills/act.py docstring).
- MI/SFBT/ACT/DBT profile.readiness uses optional fallback key 'cognitive_readiness' (audit §6.7).
- In-session state_effects from skills are NOT applied to SessionState (persistence via orchestration.products only; audit §3).

## Failure cases

(none)

## Failure classification counts


## Conclusions

- Known issues already surfaced by the audit are reproduced here as
  recorded findings, not failures: CBT assessment payload is None by
  design (no assessment skill; state_score from classification
  confidence); ACT escalation/monitoring dims are always baseline
  (emotion always {}). No gold relabeling was performed in this
  phase — gold_label_error / production_bug adfunctions are left for
  the manual adjudication phase with full per-case context preserved
  in results.json.

