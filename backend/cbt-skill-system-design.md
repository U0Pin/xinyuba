# CBT Skill System — Engineering Design Document

## Design Philosophy

A Skill is a **pure function on structured state**. It is not a prompt. It takes structured inputs, produces structured outputs, and may declare state transitions. Skills are stateless individually; state is held by the Agent orchestration layer.

```
Skill = {
  name: string,
  type: "analysis" | "intervention" | "memory" | "safety" | "meta",
  purpose: string,
  preconditions: Condition[],
  input_schema: JSONSchema,
  output_schema: JSONSchema,
  state_effects: StateTransition[],
  safety_constraints: SafetyRule[],
  downstream_routing: AgentTarget[]
}
```

---

# Layer 1: Cognitive Analysis Skills

Extract structure from user language. Never generate user-facing text.

---

## Skill 1.1 — extract_automatic_thought

**Type:** analysis

**Purpose:** Identify implicit automatic thoughts embedded in a user utterance — spontaneous, often distorted cognitions that arise in response to a situation.

**Trigger Conditions:**
- User expresses emotional distress
- User describes a negative interpretation of an event
- User makes a self-referential negative statement
- Downstream from Strategy Router after emotion_detection returns elevated negative valence

**Required Inputs:**

```json
{
  "user_utterance": "string",
  "emotion_state": { "pleasure": "float [-1,1]", "arousal": "float [-1,1]", "dominance": "float [-1,1]" },
  "context_window": "string[] (previous 2-3 turns, optional)"
}
```

**Output Schema:**

```json
{
  "automatic_thoughts": [
    {
      "thought": "string (first-person)",
      "confidence": "float [0,1]",
      "span": "string (supporting text segment)",
      "target": "self | others | future | situation"
    }
  ],
  "primary_thought": "string (highest-confidence thought)",
  "no_automatic_thought_detected": "boolean"
}
```

**Example:**

Input: `"My friend didn't reply. I probably said something wrong. She must be tired of me."`

```json
{
  "automatic_thoughts": [
    { "thought": "I said something wrong", "confidence": 0.85, "span": "I probably said something wrong", "target": "self" },
    { "thought": "She is tired of me", "confidence": 0.78, "span": "She must be tired of me", "target": "others" }
  ],
  "primary_thought": "I said something wrong",
  "no_automatic_thought_detected": false
}
```

**State Changes:** Writes `session.automatic_thoughts[]`, increments `session.thought_extraction_count`

**Safety Risks:** LOW. Read-only analysis.

**Downstream:** Strategy Router → detect_cognitive_distortion (1.2)

---

## Skill 1.2 — detect_cognitive_distortion

**Type:** analysis

**Purpose:** Classify extracted automatic thoughts against the CBT distortion taxonomy. Each thought may match zero, one, or multiple categories.

**Trigger Conditions:**
- extract_automatic_thought returned `no_automatic_thought_detected: false`
- Strategy Router requests distortion analysis before selecting intervention

**Required Inputs:**

```json
{
  "automatic_thoughts": "Thought[] (from 1.1)",
  "user_utterance": "string (original raw text)"
}
```

**Output Schema:**

```json
{
  "classifications": [
    {
      "thought": "string",
      "distortions": [
        { "type": "catastrophizing | mind_reading | black_white_thinking | overgeneralization | self_blame | emotional_reasoning | should_statements | labeling | fortune_telling | mental_filtering | discounting_positive | magnification | personalization", "confidence": "float [0,1]", "rationale": "string" }
      ],
      "primary_distortion": "string"
    }
  ],
  "distortion_summary": {
    "dominant_pattern": "string",
    "distortion_count": "int",
    "severity": "mild | moderate | severe"
  }
}
```

**Example:**

```json
{
  "classifications": [
    {
      "thought": "I said something wrong",
      "distortions": [
        { "type": "self_blame", "confidence": 0.90, "rationale": "Assumes personal fault without evidence" },
        { "type": "mind_reading", "confidence": 0.45, "rationale": "Assumes knowledge of friend's negative evaluation" }
      ],
      "primary_distortion": "self_blame"
    },
    {
      "thought": "She is tired of me",
      "distortions": [
        { "type": "mind_reading", "confidence": 0.92, "rationale": "Assumes knowledge of another's internal state without evidence" },
        { "type": "catastrophizing", "confidence": 0.60, "rationale": "Interprets non-response as relationship-ending judgment" }
      ],
      "primary_distortion": "mind_reading"
    }
  ],
  "distortion_summary": { "dominant_pattern": "mind_reading", "distortion_count": 2, "severity": "moderate" }
}
```

**State Changes:** Writes `session.distortion_analysis`, appends to `profile.distortion_history[]`, increments `profile.distortion_counts.{type}`

**Safety Risks:** LOW. Analysis only.

**Downstream:** Strategy Router → generate_reframe (2.1) or generate_socratic_questions (2.2)

---

## Skill 1.3 — identify_core_belief

**Type:** analysis

**Purpose:** Infer deeper intermediate and core beliefs from repeated automatic thought patterns. Operates across sessions — requires longitudinal data, not just the current utterance.

**Trigger Conditions:**
- `profile.distortion_history` ≥ 5 entries with same distortion type
- Strategy Router requests deeper cognitive assessment
- Profile Agent signals significant pattern stability

**Required Inputs:**

```json
{
  "current_automatic_thought": "Thought (from 1.1)",
  "distortion_history": "DistortionRecord[] (last N sessions)",
  "recurring_themes": "string[] (from Profile Agent)",
  "profile_context": {
    "attachment_style": "string | null",
    "core_fears": "string[]",
    "rejection_sensitivity": "float [0,1] | null"
  }
}
```

**Output Schema:**

```json
{
  "intermediate_beliefs": [
    { "belief": "string (conditional if-then rule)", "confidence": "float [0,1]", "evidence_frequency": "int" }
  ],
  "core_beliefs": [
    { "belief": "string (unconditional self/world/others belief)", "domain": "self_worth | lovability | competence | safety | control", "confidence": "float [0,1]", "activation_triggers": "string[]" }
  ],
  "belief_stability": "emergent | consolidating | entrenched",
  "requires_intervention": "boolean"
}
```

**Example:**

```json
{
  "intermediate_beliefs": [
    { "belief": "If someone doesn't respond quickly, it means they are upset with me", "confidence": 0.82, "evidence_frequency": 7 }
  ],
  "core_beliefs": [
    { "belief": "I am easily rejected by others", "domain": "lovability", "confidence": 0.74, "activation_triggers": ["delayed response", "ambiguous social signal", "perceived disinterest"] }
  ],
  "belief_stability": "consolidating",
  "requires_intervention": true
}
```

**State Changes:** Updates `profile.core_beliefs` (merge), sets `profile.belief_stability`

**Safety Risks:** MEDIUM. Incorrect inference could reinforce negative self-concept. Output must never be shown directly to the user — feeds intervention selection only. Below-threshold inferences are discarded.

**Downstream:** Strategy Router → cognitive_restructuring_chain (2.5) or evaluate_evidence (2.3)

---

## Skill 1.4 — assess_cognitive_triad

**Type:** analysis

**Purpose:** Evaluate the user's state across Beck's cognitive triad: view of self, world, and future. Provides systematic snapshot of cognitive health. Critical input for safety assessment.

**Trigger Conditions:**
- Session initialization (baseline)
- Significant emotional shift (ΔP > 0.3 or ΔA > 0.3)
- After crisis intervention to re-assess

**Required Inputs:**

```json
{
  "user_utterance": "string",
  "emotion_state": "PAD triple",
  "recent_automatic_thoughts": "Thought[] (current session)",
  "profile_snapshot": "ProfileSummary (from Profile Agent)"
}
```

**Output Schema:**

```json
{
  "triad": {
    "self_view": { "valence": "float [-1,1]", "themes": "string[]", "stability": "stable | fluctuating | deteriorating" },
    "world_view": { "valence": "float [-1,1]", "themes": "string[]", "stability": "stable | fluctuating | deteriorating" },
    "future_view": { "valence": "float [-1,1]", "themes": "string[]", "stability": "stable | fluctuating | deteriorating" }
  },
  "triad_balance": "balanced | mildly_negative | moderately_negative | severely_negative",
  "negative_triad_flag": "boolean (all three negative AND future_view.valence < -0.5)",
  "trend_direction": "improving | stable | declining"
}
```

**State Changes:** Writes `session.cognitive_triad`, appends to `profile.triad_history[]` (time-series), updates `profile.triad_trend`

**Safety Risks:** MEDIUM. `negative_triad_flag: true` combined with high arousal MUST trigger Safety Agent escalation.

**Downstream:** If `negative_triad_flag: true` → Safety Agent; else → Strategy Router

---

## Skill 1.5 — map_thought_emotion_link

**Type:** analysis

**Purpose:** Trace which automatic thoughts are associated with which emotional shifts. Produces the cognitive case formulation — the causal link between cognition and emotion that CBT interventions target.

**Trigger Conditions:**
- After both emotion_detection and extract_automatic_thought have run
- Strategy Router needs to prioritize which thought to intervene on

**Required Inputs:**

```json
{
  "automatic_thoughts": "Thought[] (from 1.1)",
  "emotion_state_before": "PAD triple",
  "emotion_state_after": "PAD triple",
  "situation_description": "string (triggering event)"
}
```

**Output Schema:**

```json
{
  "links": [
    {
      "thought": "string",
      "linked_emotion_shift": { "pleasure_delta": "float", "arousal_delta": "float", "dominance_delta": "float" },
      "inferred_emotion_label": "sadness | anxiety | anger | shame | guilt",
      "causal_strength": "float [0,1]",
      "is_primary_driver": "boolean"
    }
  ],
  "hot_thought": "string (highest causal_strength — primary intervention target)",
  "formulation": "string (one-sentence cognitive case formulation)"
}
```

**State Changes:** Writes `session.cognitive_formulation`, sets `session.hot_thought`

**Safety Risks:** LOW.

**Downstream:** Strategy Router → intervention skill targeting `hot_thought`

---

## Skill 1.6 — detect_thinking_trap_chain

**Type:** analysis

**Purpose:** Identify cascading distortion patterns where one distorted thought feeds another in sequence. A "trap chain" is where distortions compound rather than occurring in isolation.

**Trigger Conditions:**
- Three or more automatic thoughts extracted in a single turn
- Persistent negative affect despite individual intervention attempts
- Strategy Router detects intervention resistance

**Required Inputs:**

```json
{
  "automatic_thoughts": "Thought[] (ordered by appearance)",
  "distortion_classifications": "DistortionClassification[] (from 1.2)"
}
```

**Output Schema:**

```json
{
  "chains": [
    {
      "sequence": "int[] (thought indices)",
      "chain_type": "catastrophic_escalation | self_confirmation_loop | generalized_conclusion | emotional_snowball",
      "entry_point": "int",
      "cascade_depth": "int",
      "intervention_point": "int (where breaking the chain is most effective)"
    }
  ],
  "has_active_chain": "boolean",
  "dominant_chain_type": "string"
}
```

**State Changes:** Writes `session.active_thought_chains[]`, updates `profile.chain_pattern_history[]`

**Safety Risks:** LOW-MEDIUM. Catastrophic escalation chains reaching "life not worth living" endpoints must trigger Safety Agent.

**Downstream:** If safety-critical → Safety Agent; else → decatastrophize (2.4) or cognitive_restructuring_chain (2.5)

---

# Layer 2: Cognitive Restructuring Skills (Interventions)

Propose structured cognitive interventions. Produce plans, questions, and alternatives — never final user-facing dialogue.

---

## Skill 2.1 — generate_reframe

**Type:** intervention

**Purpose:** Generate multiple alternative interpretations for a given automatic thought. The core CBT reframing operation: "what else could be true?"

**Trigger Conditions:**
- Automatic thought extracted and classified with a distortion
- Strategy Router selects reframing as primary strategy
- User has not rejected reframing > 2 times in current session

**Required Inputs:**

```json
{
  "target_thought": "Thought (from 1.1)",
  "distortion_types": "string[] (from 1.2)",
  "situational_context": "string (triggering situation)",
  "user_profile": {
    "attachment_style": "string | null",
    "core_beliefs": "CoreBelief[] | null",
    "cognitive_readiness": "float [0,1]"
  }
}
```

**Output Schema:**

```json
{
  "alternatives": [
    {
      "interpretation": "string",
      "type": "evidence_based | perspective_taking | alternative_cause | decatastrophized | balanced_middle",
      "plausibility": "float [0,1]",
      "emotional_impact_estimate": { "pleasure": "float", "arousal": "float", "dominance": "float" },
      "requires_user_collaboration": "boolean"
    }
  ],
  "recommended_alternative": "int (index of best balanced alternative)",
  "reframe_readiness": "appropriate | premature | likely_to_be_rejected"
}
```

**Safety Risks:** LOW-MEDIUM. Reframes must never invalidate the user's emotional experience or minimize genuine danger. If the user's concern has a realistic basis, problem-solving is correct, not reframing.

**Downstream:** Dialogue Agent

---

## Skill 2.2 — generate_socratic_questions

**Type:** intervention

**Purpose:** Generate a sequence of Socratic questions to guide the user toward examining their automatic thought themselves. Preferred when user shows cognitive readiness and distortion is mild-to-moderate.

**Trigger Conditions:**
- `profile.cognitive_readiness >= 0.5`
- Distortion severity is "mild" or "moderate"
- Previous session showed positive response to Socratic approach

**Required Inputs:**

```json
{
  "target_thought": "Thought (from 1.1)",
  "distortion_type": "string (primary, from 1.2)",
  "cognitive_readiness": "float [0,1]",
  "user_fatigue_level": "low | moderate | high"
}
```

**Output Schema:**

```json
{
  "questions": [
    {
      "question": "string",
      "purpose": "evidence_examination | alternative_generation | impact_assessment | perspective_shift | reality_testing",
      "expected_cognitive_effect": "string",
      "difficulty": "float [0,1]",
      "follow_up_if": "string"
    }
  ],
  "sequence_length": "int",
  "recommended_pacing": "rapid | normal | gentle",
  "should_include_summary": "boolean"
}
```

**Safety Risks:** MEDIUM. Must not feel like interrogation. If user shows increased arousal or frustration, the sequence should be abandoned.

**Downstream:** Dialogue Agent (one question at a time, with active listening between)

---

## Skill 2.3 — evaluate_evidence

**Type:** intervention

**Purpose:** Structured evidence evaluation for and against a specific automatic thought. More explicit and directive than Socratic questioning.

**Trigger Conditions:**
- Automatic thought is specific and falsifiable
- User shows moderate cognitive rigidity
- Reframing alone was insufficient

**Required Inputs:**

```json
{
  "target_thought": "string",
  "distortion_types": "string[]",
  "user_context": "string (the situation)",
  "profile_knowledge": "string[] (relevant facts from Profile Agent)"
}
```

**Output Schema:**

```json
{
  "evidence_for": [
    { "item": "string", "strength": "weak | moderate | strong", "type": "fact | feeling | assumption | past_experience" }
  ],
  "evidence_against": [
    { "item": "string", "strength": "weak | moderate | strong", "type": "fact | feeling | assumption | past_experience | profile_data" }
  ],
  "balanced_conclusion": "string",
  "evidence_weight_imbalance": "strongly_biased_negative | moderately_biased_negative | balanced | biased_positive",
  "key_missing_evidence": "string[]"
}
```

**Safety Risks:** LOW.

**Downstream:** Dialogue Agent

---

## Skill 2.4 — decatastrophize

**Type:** intervention

**Purpose:** Systematically break down a catastrophic prediction by tracing the feared chain to its endpoint and evaluating actual likelihood and coping resources at each step.

**Trigger Conditions:**
- Distortion includes "catastrophizing"
- detect_thinking_trap_chain identifies catastrophic_escalation
- cognitive_triad shows future_view is strongly negative

**Required Inputs:**

```json
{
  "catastrophic_chain": "Thought[] (initial trigger to feared endpoint)",
  "initial_trigger": "string",
  "user_coping_resources": "string[] (from Profile Agent: strengths, support systems, past resilience)",
  "risk_level": "SAFE | LOW_RISK | MEDIUM_RISK | HIGH_RISK | CRISIS"
}
```

**Output Schema:**

```json
{
  "chain_analysis": [
    {
      "step": "int",
      "feared_outcome": "string",
      "actual_likelihood": "float [0,1]",
      "alternative_outcome": "string",
      "coping_resource": "string | null",
      "if_worst_happens": "string | null"
    }
  ],
  "decatastrophized_conclusion": "string",
  "chain_realism_score": "float [0,1] (0 = entirely catastrophic, 1 = realistic concern)",
  "should_switch_to_problem_solving": "boolean"
}
```

**Safety Risks:** LOW-MEDIUM. If `chain_realism_score > 0.6`, the concern may be realistic and minimizing it would be harmful. Do not decatastrophize genuinely dangerous situations.

**Downstream:** Dialogue Agent (if false) or plan_behavioral_activation (3.1) / problem_solving_structured (3.5) (if true)

---

## Skill 2.5 — cognitive_restructuring_chain

**Type:** intervention (meta-intervention)

**Purpose:** Orchestrate a full multi-step cognitive restructuring sequence when a single intervention is insufficient. Composes multiple other skills into a coherent chain.

**Trigger Conditions:**
- Core belief identified (1.3) and `requires_intervention: true`
- Multiple distortions with severity "moderate" or "severe"
- Single reframing attempts ineffective over multiple sessions

**Required Inputs:**

```json
{
  "core_belief": "CoreBelief (from 1.3)",
  "automatic_thoughts": "Thought[]",
  "distortions": "DistortionClassification[]",
  "cognitive_triad": "TriadAssessment (from 1.4)",
  "user_readiness": "float [0,1]",
  "previous_interventions": "InterventionRecord[]"
}
```

**Output Schema:**

```json
{
  "restructuring_plan": {
    "phases": [
      {
        "order": "int",
        "skill": "skill_name",
        "target": "string",
        "purpose": "string",
        "success_criterion": "string",
        "max_rounds": "int"
      }
    ],
    "estimated_total_rounds": "int",
    "can_complete_in_session": "boolean",
    "requires_multiple_sessions": "boolean"
  },
  "entry_skill": "skill_name",
  "exit_condition": "string",
  "fallback_strategy": "string"
}
```

**Safety Risks:** MEDIUM-HIGH. Restructuring chains can become adversarial. `max_rounds` per phase is a hard safety limit. If emotional state worsens (arousal +0.3 or pleasure -0.3), the chain must pause or abort. Never restructure during crisis states.

**Downstream:** Strategy Router (phase sequencing) → Dialogue Agent

---

## Skill 2.6 — generate_balanced_thought

**Type:** intervention

**Purpose:** Synthesize a balanced, realistic alternative thought incorporating evidence from both sides. The conclusion phase of cognitive restructuring.

**Trigger Conditions:**
- evaluate_evidence (2.3) completed
- User has acknowledged some evidence against their automatic thought
- Final step in a cognitive restructuring chain

**Required Inputs:**

```json
{
  "original_thought": "string",
  "evidence_for": "EvidenceItem[]",
  "evidence_against": "EvidenceItem[]",
  "user_engagement_level": "float [0,1]",
  "distortion_types": "string[]"
}
```

**Output Schema:**

```json
{
  "balanced_thought": "string (specific, believable, not toxically positive)",
  "thought_type": "nuanced | probabilistic | compassionate | contextualized | growth_oriented",
  "credibility": "float [0,1]",
  "compared_to_original": {
    "distortion_free": "boolean",
    "emotional_tone_shift": "string",
    "actionability": "boolean"
  },
  "anchoring_cue": "string (short, memorable version for recall)"
}
```

**Safety Risks:** LOW. Must not be toxically positive ("everything will be fine!"). Must acknowledge emotional reality while adding nuance.

**Downstream:** Dialogue Agent

---

## Skill 2.7 — reattribute

**Type:** intervention

**Purpose:** Help user distribute responsibility/causality more accurately. Targets self_blame and personalization distortions.

**Trigger Conditions:**
- Distortion includes "self_blame" or "personalization"
- User consistently attributes external events to personal failure
- Profile shows low self-worth valence

**Required Inputs:**

```json
{
  "target_thought": "string",
  "situation_description": "string",
  "profile_data": { "known_stressors": "string[]", "external_factors": "string[]" }
}
```

**Output Schema:**

```json
{
  "responsibility_distribution": [
    { "factor": "string", "category": "self | others | situation | chance | systemic", "estimated_contribution": "float [0,1]", "controllable": "boolean" }
  ],
  "original_attribution": { "self_blame_percentage": "float", "is_disproportionate": "boolean" },
  "balanced_attribution_summary": "string"
}
```

**Safety Risks:** LOW. Must not remove all agency — when user's actions did contribute, reattribution should be honest, not exculpatory.

**Downstream:** Dialogue Agent

---

# Layer 3: Behavioral Skills

Address the behavioral component of CBT — action, activity, and real-world engagement.

---

## Skill 3.1 — plan_behavioral_activation

**Type:** intervention

**Purpose:** Design a structured behavioral activation plan to counter withdrawal, low mood, and reduced activity.

**Trigger Conditions:**
- Sustained low pleasure (P < -0.3 over multiple sessions)
- User reports withdrawal from previously valued activities
- cognitive_triad shows low self-efficacy

**Required Inputs:**

```json
{
  "user_profile": {
    "valued_activities": "string[]",
    "current_activity_level": "low | moderate_low | moderate | high",
    "known_barriers": "string[]",
    "energy_level": "float [0,1]"
  },
  "emotion_state": "PAD triple",
  "safety_level": "SAFE | LOW_RISK | MEDIUM_RISK | HIGH_RISK | CRISIS"
}
```

**Output Schema:**

```json
{
  "activation_plan": [
    {
      "activity": "string",
      "category": "mastery | pleasure | social | physical | routine",
      "difficulty_level": "very_easy | easy | moderate | challenging",
      "time_commitment": "string",
      "predicted_mastery": "float [0,1]",
      "predicted_pleasure": "float [0,1]",
      "concrete_first_step": "string"
    }
  ],
  "plan_gradient": "appropriate (starts easy, builds up)",
  "total_suggested_activities": "int",
  "recommended_starting_point": "int",
  "avoidance_warning": "string | null"
}
```

**Safety Risks:** MEDIUM. Must never pressure the user. If user expresses inability, plan must scale down, not insist. During crisis states, behavioral activation beyond basic self-care is inappropriate.

**Downstream:** Dialogue Agent (present as invitation, not prescription)

---

## Skill 3.2 — design_behavioral_experiment

**Type:** intervention

**Purpose:** Design a real-world test of a specific belief where the user gathers data to evaluate the belief empirically.

**Trigger Conditions:**
- A specific, testable belief identified
- evaluate_evidence (2.3) was helpful but insufficient
- User is cognitively ready and willing
- Belief is not about genuinely dangerous situations

**Required Inputs:**

```json
{
  "target_belief": "string (must be testable/falsifiable)",
  "belief_domain": "social | performance | safety | self_control | other",
  "current_safety_behaviors": "string[]",
  "user_capabilities": "string[]",
  "safety_check": "boolean (Safety Agent approval required)"
}
```

**Output Schema:**

```json
{
  "experiment": {
    "hypothesis": "string (belief as prediction)",
    "experiment_action": "string",
    "control_condition": "string (normal safety behavior)",
    "predicted_outcome_by_belief": "string",
    "alternative_possible_outcomes": "string[]",
    "data_to_collect": "string[]",
    "duration": "string",
    "debrief_questions": "string[]"
  },
  "difficulty_level": "float [0,1]",
  "emotional_risk": "low | moderate | high",
  "requires_safety_override": "boolean"
}
```

**Safety Risks:** MEDIUM. Never put user in emotionally or physically dangerous situations. Social experiments require careful risk assessment. Safety Agent must review any experiment with `emotional_risk: high`.

**Downstream:** Dialogue Agent → on next session, evaluate_evidence (2.3) with experiment data

---

## Skill 3.3 — graded_exposure_plan

**Type:** intervention

**Purpose:** Build a fear/anxiety hierarchy with graduated exposure steps for avoidance patterns.

**Trigger Conditions:**
- User describes consistent avoidance of a specific situation
- map_thought_emotion_link (1.5) shows avoidance-driven patterns
- Anxiety is dominant emotional state

**Required Inputs:**

```json
{
  "feared_situation": "string",
  "current_avoidance_level": "complete | partial | minimal",
  "safety_behaviors": "string[]",
  "user_distress_tolerance": "float [0,1]",
  "emotion_state": "PAD triple",
  "previous_exposure_attempts": "ExposureRecord[] | null"
}
```

**Output Schema:**

```json
{
  "exposure_hierarchy": [
    {
      "step": "int",
      "situation": "string",
      "expected_anxiety": "float [0,1]",
      "duration": "string",
      "safety_behaviors_to_drop": "string[]",
      "coping_strategy": "string",
      "mastery_criterion": "string"
    }
  ],
  "hierarchy_gradient": "appropriate | too_steep | too_flat",
  "total_steps": "int",
  "estimated_completion_timeframe": "string",
  "contraindicated": "boolean"
}
```

**Safety Risks:** HIGH. Poorly calibrated exposure (too intense too soon) can increase avoidance. Must respect window of tolerance. Never use flooding. Requires Safety Agent approval. Contraindicated during crisis, high dissociation, or when feared situation has genuine danger.

**Downstream:** Safety Agent (approval required) → Dialogue Agent (one step at a time)

---

## Skill 3.4 — assess_mastery_pleasure

**Type:** analysis (post-behavioral)

**Purpose:** After a behavioral activation activity, assess experienced mastery and pleasure. Compares predicted vs. actual to calibrate future plans.

**Trigger Conditions:**
- User completed or attempted an activity from activation plan (3.1)
- User reports on a real-world action

**Required Inputs:**

```json
{
  "activity": "string",
  "completion_status": "completed | partially_completed | attempted | not_attempted",
  "user_report": "string",
  "predicted_mastery": "float",
  "predicted_pleasure": "float",
  "emotion_state_before": "PAD triple",
  "emotion_state_after": "PAD triple"
}
```

**Output Schema:**

```json
{
  "actual_mastery": "float [0,1]",
  "actual_pleasure": "float [0,1]",
  "prediction_accuracy": {
    "mastery_delta": "float",
    "pleasure_delta": "float",
    "user_overestimates_negative": "boolean",
    "user_underestimates_capacity": "boolean"
  },
  "mood_impact": { "pleasure_shift": "float", "arousal_shift": "float", "dominance_shift": "float" },
  "barriers_encountered": "string[]",
  "calibration_notes": "string[]"
}
```

**Safety Risks:** LOW.

**Downstream:** Strategy Router → if positive: reinforce and plan next; if negative: ACT acceptance

---

## Skill 3.5 — problem_solving_structured

**Type:** intervention

**Purpose:** Apply structured problem-solving to a specific, actionable life problem. Used when distress has a realistic external cause, not primarily a cognitive distortion.

**Trigger Conditions:**
- decatastrophize (2.4) returns `should_switch_to_problem_solving: true`
- User describes a concrete, actionable problem
- Distortion analysis shows realistic concern

**Required Inputs:**

```json
{
  "problem_description": "string",
  "problem_domain": "work | relationships | health | financial | academic | daily_living | other",
  "previous_attempts": "string[]",
  "user_resources": "string[]",
  "urgency": "low | moderate | high | crisis",
  "profile_data": {
    "problem_solving_style": "avoidant | impulsive | balanced | overanalyzing | null",
    "known_strengths": "string[]"
  }
}
```

**Output Schema:**

```json
{
  "problem_definition": "string (clarified, specific)",
  "goals": [
    { "goal": "string", "type": "outcome | process | minimum_acceptable", "realistic": "boolean", "timeframe": "string" }
  ],
  "solutions": [
    { "solution": "string", "pros": "string[]", "cons": "string[]", "feasibility": "float [0,1]", "likely_effectiveness": "float [0,1]", "required_resources": "string[]" }
  ],
  "recommended_solution": "int",
  "action_plan": { "steps": [{ "step": "int", "action": "string", "when": "string", "concrete_commitment": "string" }] },
  "potential_obstacles": "string[]",
  "review_plan": "string"
}
```

**Safety Risks:** MEDIUM. Must not advise on high-stakes legal, medical, or financial decisions. User generates solutions; system helps structure. System must never tell user what major life decision to make.

**Downstream:** Dialogue Agent (present as collaborative process)

---

# Layer 4: Memory & Longitudinal Tracking Skills

Operate on the user profile across sessions. Invoked by the Profile Agent, not directly by the Strategy Router.

---

## Skill 4.1 — record_thought_pattern

**Type:** memory

**Purpose:** Log current session's extracted thought patterns into the longitudinal profile in compressed structured form. Primary write-path from session-level analysis to profile-level memory.

**Trigger Conditions:**
- End of session (or significant session milestone)
- Profile Agent periodic consolidation

**Required Inputs:**

```json
{
  "session_id": "string",
  "timestamp": "ISO8601",
  "automatic_thoughts": "Thought[]",
  "distortions": "DistortionClassification[]",
  "hot_thought": "string | null",
  "cognitive_triad": "TriadAssessment | null",
  "emotion_trajectory": "PAD[] (time-series from session)"
}
```

**Output Schema:**

```json
{
  "compressed_record": {
    "session_summary": {
      "dominant_thought_theme": "string",
      "dominant_distortion": "string",
      "thought_count": "int",
      "emotion_trend": "improving | stable | worsening"
    },
    "new_patterns_detected": [
      { "pattern": "string", "type": "thought | distortion | emotional_reaction | situational", "is_novel": "boolean", "strength": "float [0,1]" }
    ],
    "pattern_updates": [
      { "existing_pattern_id": "string", "update_type": "reinforced | weakened | modified | resolved", "confidence_adjustment": "float" }
    ]
  },
  "storage_instruction": "append | merge | overwrite",
  "compression_ratio": "float"
}
```

**Safety Risks:** LOW.

**Downstream:** Profile Agent → detect_recurring_distortion (4.2)

---

## Skill 4.2 — detect_recurring_distortion

**Type:** memory (analysis)

**Purpose:** Analyze longitudinal distortion data to identify chronic cognitive patterns. A one-time distortion is coaching; a recurring distortion is a treatment target.

**Trigger Conditions:**
- `profile.distortion_history` length ≥ 5
- Periodic profile maintenance
- Triggered by record_thought_pattern (4.1)

**Required Inputs:**

```json
{
  "distortion_history": "DistortionRecord[] (time-series)",
  "lookback_window": "int (default 10)",
  "min_occurrence_threshold": "float [0,1]"
}
```

**Output Schema:**

```json
{
  "recurring_patterns": [
    {
      "distortion_type": "string",
      "frequency": "float [0,1]",
      "trend": "increasing | stable | decreasing",
      "associated_triggers": "string[]",
      "associated_emotions": "string[]",
      "resistance_to_intervention": "float [0,1]",
      "clinical_significance": "low | moderate | high"
    }
  ],
  "dominant_chronic_pattern": "string | null",
  "pattern_complexity": "simple | moderate | complex",
  "recommended_intervention_level": "surface_thought | intermediate_belief | core_belief"
}
```

**Safety Risks:** LOW.

**Downstream:** Profile Agent → Strategy Router (adjust intervention strategy)

---

## Skill 4.3 — compute_progress_indicator

**Type:** memory (analysis)

**Purpose:** Derive objective progress metrics from longitudinal data for evidence-based feedback on whether support is helping.

**Trigger Conditions:**
- Periodic (every N sessions)
- User expresses doubt about progress
- Strategy Router needs to evaluate intervention effectiveness

**Required Inputs:**

```json
{
  "profile_data": {
    "distortion_history": "DistortionRecord[]",
    "triad_history": "TriadAssessment[]",
    "emotion_trajectory": "PADTimeSeries",
    "activity_history": "ActivityAssessment[]",
    "core_beliefs": "CoreBelief[]",
    "session_count": "int"
  },
  "comparison_window": "last_5_sessions | last_10_sessions | all_sessions",
  "metrics_requested": "string[]"
}
```

**Output Schema:**

```json
{
  "metrics": {
    "cognitive_flexibility": { "value": "float [0,1]", "trend": "improving | stable | declining", "operationalization": "string" },
    "distortion_frequency": { "value": "float (per session)", "trend": "improving | stable | declining" },
    "emotional_regulation": { "value": "float [0,1]", "trend": "improving | stable | declining", "operationalization": "string" },
    "behavioral_engagement": { "value": "float [0,1]", "trend": "improving | stable | declining", "operationalization": "string" },
    "self_efficacy": { "value": "float [0,1]", "trend": "improving | stable | declining", "operationalization": "string" },
    "overall_progress": { "value": "float [0,1]", "trend": "improving | stable | declining", "confidence": "float [0,1]" }
  },
  "significant_changes": [{ "metric": "string", "change_magnitude": "float", "direction": "positive | negative", "likely_attributable_to": "string | null" }],
  "stagnation_warning": "boolean",
  "should_adjust_strategy": "boolean"
}
```

**Safety Risks:** MEDIUM. Raw declining metrics must never be shown to the user directly. If `stagnation_warning: true`, trigger strategy review, not communication of failure.

**Downstream:** To user (via Dialogue Agent): only with supportive framing. To system: Strategy Router (strategy adjustment). If stagnation → Profile Agent (deeper assessment).

---

## Skill 4.4 — identify_trigger_context

**Type:** memory (analysis)

**Purpose:** Identify situational, temporal, and interpersonal contexts that reliably trigger negative cognitive patterns.

**Trigger Conditions:**
- Recurring distortion patterns detected (4.2)
- Profile has sufficient data (≥ 8 sessions with situational tags)

**Required Inputs:**

```json
{
  "distortion_history": "DistortionRecord[] (with situation tags)",
  "emotion_history": "PADTimeSeries (with situation tags)",
  "session_metadata": "SessionMeta[] (time of day, day of week, user-reported context)"
}
```

**Output Schema:**

```json
{
  "triggers": [
    {
      "context": "string",
      "category": "social | work | family | health | time_based | internal_state | other",
      "triggered_patterns": "string[]",
      "activation_strength": "float [0,1]",
      "frequency": "float [0,1]",
      "latency": "string",
      "modifiable": "boolean"
    }
  ],
  "high_risk_contexts": "string[]",
  "protective_contexts": "string[] (where patterns are notably absent — resilience factors)",
  "recommended_preventive_strategies": "string[]"
}
```

**Safety Risks:** LOW.

**Downstream:** Profile Agent → Strategy Router

---

# Layer 5: CBT-Specific Safety Skills

Interface directly with the Safety Agent. Detect cognitive patterns that pose elevated risk.

---

## Skill 5.1 — detect_hopelessness_triad

**Type:** safety

**Purpose:** Monitor for Beck's negative cognitive triad combined with hopelessness language. Strongest cognitive predictor of suicidal ideation in CBT literature.

**Trigger Conditions:**
- `assess_cognitive_triad` (1.4) returns `negative_triad_flag: true`
- User uses hopelessness language ("nothing will ever change", "there's no point")
- Safety Agent requests cognitive risk assessment

**Required Inputs:**

```json
{
  "cognitive_triad": "TriadAssessment (from 1.4)",
  "user_utterance": "string",
  "emotion_state": "PAD triple",
  "profile_data": { "hopelessness_history": "boolean[]", "risk_history": "RiskRecord[]" }
}
```

**Output Schema:**

```json
{
  "hopelessness_indicators": {
    "cognitive_triad_severity": "mild | moderate | severe | extreme",
    "future_orientation": "intact | diminished | absent",
    "hopelessness_language_detected": "boolean",
    "language_markers": "string[]",
    "perceived_agency": "float [0,1]"
  },
  "risk_escalation": {
    "current_level": "SAFE | LOW_RISK | MEDIUM_RISK | HIGH_RISK | CRISIS",
    "recommended_level": "SAFE | LOW_RISK | MEDIUM_RISK | HIGH_RISK | CRISIS",
    "escalation_reason": "string | null"
  },
  "requires_crisis_protocol": "boolean",
  "immediate_actions": "string[] (concrete safety actions, not conversation suggestions)"
}
```

**Safety Risks:** HIGH. False negatives are dangerous. Err toward caution: when uncertain, escalate.

**Downstream:** Safety Agent (absolute priority — bypasses Strategy Router)

---

## Skill 5.2 — assess_cognitive_rigidity

**Type:** safety

**Purpose:** Detect rigid, all-or-nothing thinking patterns that resist intervention. Linked to hopelessness, predicts poor response to standard reframing.

**Trigger Conditions:**
- Multiple reframing/socratic attempts failed in current session
- User repeatedly returns to same thought despite intervention
- `black_white_thinking` distortion detected with high confidence

**Required Inputs:**

```json
{
  "session_interventions": "InterventionRecord[]",
  "distortion_classifications": "DistortionClassification[]",
  "user_utterances": "string[]",
  "profile_data": { "chronic_patterns": "ChronicPattern[]", "intervention_resistance_scores": "float[]" }
}
```

**Output Schema:**

```json
{
  "rigidity_assessment": {
    "level": "flexible | somewhat_rigid | rigid | severely_rigid",
    "indicators": {
      "repeated_rejection_of_alternatives": "boolean",
      "all_or_nothing_language": "boolean",
      "inability_to_generate_alternatives": "boolean",
      "emotional_certainty": "float [0,1]"
    },
    "intervention_blocked_reason": "string | null",
    "recommended_approach": "emotional_validation | ACT_acceptance | DBT_grounding | gentle_perspective | continue_CBT"
  },
  "risk_correlation": { "rigidity_hopelessness_link": "weak | moderate | strong", "escalation_recommended": "boolean" }
}
```

**Safety Risks:** MEDIUM. Pushing against rigid thinking can increase distress and entrenchment. When rigidity is high, the correct response is usually NOT more CBT — it's emotional validation and ACT acceptance.

**Downstream:** If rigid/severely_rigid → Safety Agent → ACT Companion Layer. Else → Strategy Router.

---

## Skill 5.3 — detect_intervention_resistance

**Type:** safety (meta)

**Purpose:** Monitor for signs that the current intervention approach is not working or is causing harm. Runtime safety monitor, not user-facing.

**Trigger Conditions:**
- Continuous monitoring during any intervention phase
- Explicitly triggered after each intervention round

**Required Inputs:**

```json
{
  "intervention_type": "string",
  "intervention_round": "int",
  "emotion_state_before": "PAD triple",
  "emotion_state_after": "PAD triple",
  "user_response": "string",
  "engagement_indicators": {
    "response_length_change": "float",
    "deflection_detected": "boolean",
    "explicit_rejection": "boolean",
    "topic_change": "boolean"
  }
}
```

**Output Schema:**

```json
{
  "resistance_detected": "boolean",
  "resistance_type": "none | mild_deflection | explicit_rejection | emotional_worsening | disengagement | topic_avoidance",
  "emotional_impact": { "direction": "improved | neutral | worsened", "magnitude": "float [0,1]", "concerning_shift": "boolean" },
  "recommended_action": "continue | adjust_tone | switch_strategy | pause_and_validate | escalate_to_safety",
  "strategy_switch_candidates": "string[]",
  "max_rounds_exceeded": "boolean"
}
```

**Safety Risks:** HIGH (gatekeeper skill). Failure to detect resistance can lead to system pushing harmful interventions. Must have high recall even at cost of lower precision.

**Downstream:** If `escalate_to_safety` → Safety Agent. Else → Strategy Router with `strategy_switch_candidates`.

---

# Layer 6: Meta/Orchestration Skills

Manage the CBT subsystem itself. Internal to the Strategy Router.

---

## Skill 6.1 — select_cbt_strategy

**Type:** meta

**Purpose:** Given all current analysis outputs, select the optimal CBT intervention strategy and specific skill. Primary decision-making node for the CBT subsystem.

**Trigger Conditions:**
- After all analysis skills have run
- When previous intervention completes or is abandoned
- Session start (determine initial approach)

**Required Inputs:**

```json
{
  "analysis_outputs": {
    "automatic_thoughts": "Thought[] | null",
    "distortions": "DistortionClassification[] | null",
    "core_beliefs": "CoreBelief[] | null",
    "cognitive_triad": "TriadAssessment | null",
    "thought_emotion_links": "ThoughtEmotionLink[] | null",
    "thinking_trap_chains": "TrapChain[] | null"
  },
  "safety_state": { "risk_level": "SAFE | LOW_RISK | MEDIUM_RISK | HIGH_RISK | CRISIS", "blocked_interventions": "string[]" },
  "profile_context": { "cognitive_readiness": "float [0,1]", "chronic_patterns": "ChronicPattern[]", "intervention_history": "InterventionRecord[]", "rigidity_level": "flexible | somewhat_rigid | rigid | severely_rigid | null" },
  "session_context": { "session_duration": "int", "intervention_count": "int", "user_fatigue": "low | moderate | high", "phase": "listen | reflect | validate | reframe | grounding | action | closure" }
}
```

**Output Schema:**

```json
{
  "selected_strategy": {
    "primary_skill": "skill_name",
    "secondary_skill": "skill_name | null",
    "rationale": "string",
    "confidence": "float [0,1]"
  },
  "strategy_parameters": { "intensity": "gentle | moderate | direct", "pacing": "slow | normal | brisk", "max_rounds": "int", "collaborative_framing": "boolean" },
  "contraindicated_skills": "string[]",
  "dialogue_tone_guidance": { "validation_level": "high | moderate | minimal", "directiveness": "guided_discovery | collaborative | directive", "emotional_stance": "warm | neutral | structured" }
}
```

**Safety Risks:** MEDIUM. Strategy selection errors can route user to inappropriate or harmful interventions. `contraindicated_skills` must be complete and conservative.

**Downstream:** The selected `primary_skill` → then Dialogue Agent

---

## Skill 6.2 — evaluate_intervention_outcome

**Type:** meta

**Purpose:** After each intervention round, evaluate effectiveness and decide next action: continue, adjust, switch, or stop.

**Trigger Conditions:**
- After every intervention round completes
- User provides response to intervention

**Required Inputs:**

```json
{
  "intervention_applied": { "skill": "string", "round": "int", "output": "StructuredOutput", "dialogue_rendering": "string" },
  "user_response": "string",
  "emotion_shift": { "before": "PAD triple", "after": "PAD triple" },
  "resistance_check": "ResistanceAssessment (from 5.3)",
  "session_strategy": "StrategySelection (from 6.1)"
}
```

**Output Schema:**

```json
{
  "outcome": { "effectiveness": "effective | partially_effective | neutral | ineffective | harmful", "user_engaged": "boolean", "cognitive_shift_detected": "boolean", "emotional_impact": "positive | neutral | negative" },
  "next_action": { "decision": "continue_current | continue_with_adjustment | switch_to_fallback | switch_to_new_strategy | stop_and_validate | escalate", "adjustment": "string | null", "new_strategy": "string | null", "reason": "string" },
  "session_should_end": "boolean",
  "learning_update": "string | null"
}
```

**Safety Risks:** MEDIUM. Failing to detect ineffective/harmful intervention means system continues a failing approach. `harmful` rating triggers immediate strategy abandonment.

**Downstream:** Strategy Router (next decision)

---

# Skill Dependency Graph

```
Analysis Layer:
  extract_automatic_thought (1.1) ──→ detect_cognitive_distortion (1.2)
                                      ├──→ identify_core_belief (1.3) [requires longitudinal data]
                                      ├──→ map_thought_emotion_link (1.5) [requires emotion data]
                                      └──→ detect_thinking_trap_chain (1.6) [requires 1.1 + 1.2]
  assess_cognitive_triad (1.4) [independent]

Intervention Layer:
  generate_reframe (2.1) ←── 1.1 + 1.2
  generate_socratic_questions (2.2) ←── 1.1 + 1.2
  evaluate_evidence (2.3) ←── 1.1 + 1.2
  decatastrophize (2.4) ←── 1.6
  cognitive_restructuring_chain (2.5) ←── 1.3 (composes 2.2 + 2.3 + 2.6)
  generate_balanced_thought (2.6) ←── 2.3
  reattribute (2.7) ←── 1.1 + 1.2

Behavioral Layer:
  plan_behavioral_activation (3.1) ←── 1.4 + profile
  design_behavioral_experiment (3.2) ←── 1.2 + 2.3
  graded_exposure_plan (3.3) ←── 1.5 + profile
  assess_mastery_pleasure (3.4) ←── 3.1 (post-hoc)
  problem_solving_structured (3.5) ←── 2.4

Memory Layer (cross-session):
  record_thought_pattern (4.1) ←── session output
  detect_recurring_distortion (4.2) ←── 4.1
  compute_progress_indicator (4.3) ←── profile data
  identify_trigger_context (4.4) ←── 4.2

Safety Layer (can interrupt any):
  detect_hopelessness_triad (5.1) ←── 1.4
  assess_cognitive_rigidity (5.2) ←── session interventions
  detect_intervention_resistance (5.3) ←── continuous monitoring

Meta Layer (orchestrates all):
  select_cbt_strategy (6.1) ←── all analysis + safety + profile
  evaluate_intervention_outcome (6.2) ←── intervention output + user response + 5.3
```

---

# State Machine Integration

## Risk State Gating

| Risk State | Analysis Skills | Intervention Skills | Behavioral Skills |
|---|---|---|---|
| SAFE | All available | All available | All available |
| LOW_RISK | All available | All available | All available |
| MEDIUM_RISK | All available | Reframing, Socratic, Evidence — no restructuring chains | Gentle activation only |
| HIGH_RISK | Analysis only | Blocked | Blocked (basic self-care only) |
| CRISIS | Safety analysis only | Blocked | Blocked |

## Emotional State Routing

| Dominant Emotion | Preferred CBT Approach | Cautions |
|---|---|---|
| Anxiety | Cognitive restructuring, decatastrophizing, behavioral experiments | Don't reinforce avoidance |
| Sadness/Depression | Behavioral activation, cognitive triad assessment, evidence evaluation | Watch for hopelessness triad |
| Anger | Reattribution, balanced thought, problem-solving | Validate before restructuring |
| Shame | Socratic (gentle), reattribution | Validate first |
| Guilt | Reattribution, problem-solving | Distinguish realistic vs. distorted guilt |

## Dialogue Phase Gating

| Phase | Skills Available |
|---|---|
| listen | Analysis skills only |
| reflect | Analysis skills only |
| validate | Analysis skills only |
| reframe | All intervention skills |
| grounding | Safety skills only |
| action | Behavioral skills |
| closure | assess_mastery_pleasure, record_thought_pattern, compute_progress_indicator |

---

# Agent Interface Contracts

## Input Contract (upstream → CBT subsystem)

```
Safety Agent → CBT:  risk_level, blocked_interventions[], crisis_mode: boolean
Emotion Agent → CBT: PAD triple, emotion_label, emotional_shift_detected: boolean
Profile Agent → CBT: profile_summary, chronic_patterns, cognitive_readiness, intervention_history
User Input → CBT:   raw utterance (always passed through, never modified)
```

## Output Contract (CBT subsystem → downstream)

```
CBT → Strategy Router:  selected_skill, skill_output (structured JSON), confidence, next_action
CBT → Dialogue Agent:   dialogue_guidance { tone, validation_level, directiveness, content_to_convey }
CBT → Safety Agent:     risk_flag, hopelessness_indicators, escalation_reason
CBT → Profile Agent:    memory_updates, pattern_records, progress_metrics
```

---

# Design Constraints

1. **Skills are stateless.** State lives in the session object and profile. Skills transform input → output.
2. **Skills never generate user-facing text.** That is the Dialogue Agent's sole responsibility.
3. **Safety can interrupt any skill at any time.** No skill is above the Safety Agent.
4. **Skills declare, don't decide.** Skills produce structured outputs. The Strategy Router decides.
5. **Every skill has a fallback.** If a skill fails or is rejected, the system must know what to try next.
6. **Max rounds are mandatory.** Every intervention skill has a hard limit on repetitions to prevent looping.
7. **All outputs are structured JSON.** No free-text skill outputs. Enables state machine integration, testing, and interpretability.
8. **Skills are testable.** Given structured inputs, a skill must produce deterministic or bounded outputs suitable for evaluation.
