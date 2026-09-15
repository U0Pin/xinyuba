# 候选池统计

## 1. 各疗法 complete 数量

| 疗法 | complete 数 | 备注 |
|---|---|---|
| ACT | 2 |  |
| CBT | 3 |  |
| DBT | 2 |  |
| MI | 3 |  |
| SFBT | 1 |  |
| **合计** | **11** | |

## 2. 各疗法 fragment 数量

| 疗法 | fragment 数 |
|---|---|
| ACT | 2 |
| CBT | 7 |
| DBT | 4 |
| MI | 12 |
| SFBT | 2 |
| （未触发疗法） | 3 |
| **合计** | **30** |

## 3. target → actual 分流情况

| target | actual | 数量 |
|---|---|---|
| CBT | CBT | 8 |
| MI | MI | 7 |
| DBT | DBT | 4 |
| SFBT | MI | 3 ⚠ boundary |
| SFBT | SFBT | 3 |
| ACT | ACT | 2 |
| ACT | CBT | 2 ⚠ boundary |
| ACT | MI | 2 ⚠ boundary |
| DBT | MI | 2 ⚠ boundary |
| DBT | （未触发） | 2 ⚠ boundary |
| ACT | DBT | 1 ⚠ boundary |
| CBT | ACT | 1 ⚠ boundary |
| CBT | DBT | 1 ⚠ boundary |
| CBT | MI | 1 ⚠ boundary |
| DBT | ACT | 1 ⚠ boundary |
| SFBT | （未触发） | 1 ⚠ boundary |

## 4. boundary case 数量

- 完整案例中 boundary（target ≠ actual）: **5** 个
- Fragment 中 boundary: **9** 个
- **合计 boundary case: 14** 个

**Boundary 列表**（按 actual_therapy 归桶，不能重新归回 target）：

| case_id | target | actual | quality |
|---|---|---|---|
| dbt-emotion-burst | DBT | ACT | complete |
| act-chronic-illness-acceptance-caregiver | ACT | DBT | complete |
| cbt-behavioral-activation-depression-withdrawal | CBT | MI | complete |
| dbt-interpersonal-effectiveness-dear-man-boss | DBT | MI | complete |
| sfbt-scaling-confidence | SFBT | MI | complete |
| act-committed-action-caregiving | ACT | CBT | fragment |
| act-self-compassion-mistake | ACT | CBT | fragment |
| act-value-action-divorce-decision | ACT | MI | fragment |
| act-value-conflict-career | ACT | MI | fragment |
| cbt-exposure-social-anxiety-meeting | CBT | DBT | fragment |
| cbt-social-avoidance | CBT | ACT | fragment |
| dbt-impulsive-shopping | DBT | MI | fragment |
| sfbt-exception-finding-career-stuck | SFBT | MI | fragment |
| sfbt-scaling-motivation-exercise | SFBT | MI | fragment |

## 5. 每个案例的质量状态

- **Complete: 11**
- **Fragment: 30**
- **Broken/Failed: 0**
- **合计: 41**

### 完整案例明细

| case_id | target | actual | 轮数 | therapy_rounds | step_changes | risk |
|---|---|---|---|---|---|---|
| act-defusion-rumination | ACT | ACT | 8 | 8 | 3 | SAFE |
| dbt-emotion-burst ⚠target=DBT | DBT | ACT | 8 | 5 | 3 | MEDIUM_RISK |
| cbt-cognitive-distortion-mind-reading-partner | CBT | CBT | 8 | 6 | 5 | SAFE |
| cbt-disaster-interview | CBT | CBT | 8 | 7 | 3 | LOW_RISK |
| cbt-perfectionism-procrastination | CBT | CBT | 8 | 8 | 3 | SAFE |
| act-chronic-illness-acceptance-caregiver ⚠target=ACT | ACT | DBT | 8 | 6 | 3 | SAFE |
| dbt-emotion-regulation | DBT | DBT | 8 | 8 | 3 | SAFE |
| cbt-behavioral-activation-depression-withdrawal ⚠target=CBT | CBT | MI | 8 | 4 | 3 | SAFE |
| dbt-interpersonal-effectiveness-dear-man-boss ⚠target=DBT | DBT | MI | 8 | 5 | 3 | SAFE |
| sfbt-scaling-confidence ⚠target=SFBT | SFBT | MI | 8 | 6 | 5 | SAFE |
| sfbt-miracle-question | SFBT | SFBT | 8 | 7 | 3 | SAFE |

### Fragment 明细

| case_id | target | actual | 主要 fragment 原因 |
|---|---|---|---|
| act-acceptance-illness | ACT | ACT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['values', 'acceptance'] |
| act-committed-action-caregiving ⚠target=ACT | ACT | CBT | [structure] orch_step_changes=1 < 2 |
| act-self-compassion-mistake ⚠target=ACT | ACT | CBT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['identify_thought', 'classify_distortion'] |
| act-value-action-divorce-decision ⚠target=ACT | ACT | MI | [completeness] 三层闸门全过，但编排步骤仅 2 个：['explore', 'evocation'] |
| act-value-conflict-career ⚠target=ACT | ACT | MI | [completeness] 三层闸门全过，但编排步骤仅 2 个：['explore', 'evocation'] |
| cbt-belief-revision-public-speaking-failure | CBT | CBT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['identify_thought', 'classify_distortion'] |
| cbt-breakup-rumination | CBT | CBT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['identify_thought', 'classify_distortion'] |
| cbt-exam-anxiety | CBT | CBT | [structure] orch_step_changes=1 < 2 |
| cbt-exposure-social-anxiety-meeting ⚠target=CBT | CBT | DBT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['mindfulness', 'emotion_regulation'] |
| cbt-self-devaluation | CBT | CBT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['identify_thought', 'classify_distortion'] |
| cbt-sleep-rumination | CBT | CBT | [structure] orch_step_changes=1 < 2 |
| cbt-social-avoidance ⚠target=CBT | CBT | ACT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['values', 'acceptance'] |
| dbt-emotion-regulation-opposite-action-shame ⚠target=DBT | DBT | （未触发） | [structure] actual_therapy 为空（未触发疗法或已 EndTherapy） |
| dbt-family-conflict | DBT | DBT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['distress_tolerance', 'interpersonal_effectiveness'] |
| dbt-impulsive-shopping ⚠target=DBT | DBT | MI | [structure] orch_step_changes=1 < 2 |
| dbt-interpersonal-conflict ⚠target=DBT | DBT | （未触发） | [structure] actual_therapy 为空（未触发疗法或已 EndTherapy） |
| dbt-mindfulness-anxiety | DBT | DBT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['mindfulness', 'distress_tolerance'] |
| dbt-panic-grounding | DBT | DBT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['mindfulness', 'distress_tolerance'] |
| mi-ambivalence-career-change-vs-stability | MI | MI | [structure] orch_step_changes=1 < 2 |
| mi-ambivalence-relationship-stay-or-leave | MI | MI | [structure] orch_step_changes=1 < 2 |
| mi-ambivalence-smoking | MI | MI | [completeness] 三层闸门全过，但编排步骤仅 2 个：['explore', 'evocation'] |
| mi-change-career | MI | MI | [completeness] 三层闸门全过，但编排步骤仅 2 个：['explore', 'evocation'] |
| mi-change-talk-sustain-talk-smoking | MI | MI | [completeness] 三层闸门全过，但编排步骤仅 2 个：['evocation', 'explore'] |
| mi-motivation-weight | MI | MI | [completeness] 三层闸门全过，但编排步骤仅 2 个：['explore', 'evocation'] |
| mi-sustain-talk-weight-loss-stuck | MI | MI | [completeness] 三层闸门全过，但编排步骤仅 2 个：['explore', 'evocation'] |
| sfbt-coping-question-grief | SFBT | SFBT | [completeness] 三层闸门全过，但编排步骤仅 2 个：['resource_exploration', 'exception_exploration'] |
| sfbt-exception-finding-career-stuck ⚠target=SFBT | SFBT | MI | [structure] orch_step_changes=1 < 2 |
| sfbt-exception-finding | SFBT | SFBT | [structure] therapy_rounds=3 < 4 |
| sfbt-relationship-miracle-marriage ⚠target=SFBT | SFBT | （未触发） | [structure] actual_therapy 为空（未触发疗法或已 EndTherapy） |
| sfbt-scaling-motivation-exercise ⚠target=SFBT | SFBT | MI | [completeness] 三层闸门全过，但编排步骤仅 2 个：['evocation', 'explore'] |

## 6. CBT / DBT 等缺失疗法的真实情况

五种疗法均有 complete candidate。

## 7. 是否满足正式用户测试的最低候选数量要求

**最低要求**（用户十节）：每种疗法准备约 4–6 个高质量候选，每名受测者从每种疗法随机抽 2 个。

| 疗法 | complete | 是否满足最低（≥4） | 缺口 |
|---|---|---|---|
| ACT | 2 | ✗ | 2 |
| CBT | 3 | ✗ | 1 |
| DBT | 2 | ✗ | 2 |
| MI | 3 | ✗ | 1 |
| SFBT | 1 | ✗ | 3 |

**结论：当前候选池不满足正式用户测试的最低要求。**

**下一步建议（按用户十四节 14 要求，仅报告事实，不擅自降低标准或启动测试）**：

1. 报告 CBT/DBT 缺失的现状，等待人工决策；
2. 不自动降低质量门槛（STRUCT_MIN_STEP_CHANGES 等）或修改 seed；
3. 是否补 seed 需用户单独决策（补 seed 时仍按 5 疗法×3 case 设计，但若决策器仍不触发 CBT/DBT，则如实记录）。

---

**说明**：
- 候选池按 `actual_therapy` 分桶，而非 target_therapy；boundary case 一律按实际触发归桶并标记 ⚠
- 质量门槛保持原值：therapy_rounds≥4, step_changes≥2, skill_turns≥3；未为凑数量修改
- fragment case 保留作为机制分析材料，但与 complete 严格区分
- CRISIS 案例已通过 research 闸门排除（普通用户测试）；专业组测试需另行单独设计

---

## 8. 专项安全池（`safety_tier: professional_only`，独立池，**不计入**普通池统计）

- Complete: **0** / Fragment: **2** / Broken: **0**

| case_id | target | actual | therapy_rounds | step_changes | peak_risk | final_risk | quality |
|---|---|---|---|---|---|---|---|
| dbt-distress-tolerance-tipp-self-harm-urge | DBT | （未触发） | - | - | CRISIS | SAFE | fragment |
| dbt-mindfulness-grounding-panic-alone | DBT | （未触发） | - | - | CRISIS | MEDIUM_RISK | fragment |

**说明**：专项安全池的判定逻辑（三层闸门 + completeness）**与普通池完全相同**，仅按 `safety_tier` 字段做硬隔离；不修改任何阈值。

