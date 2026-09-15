"""评测样本抽取 — 从 candidate_pool.json 抽取主样本与专项安全样本。

规则：
  - 普通池（safety_tier=general）抽取 10 段主样本
    · 每 actual_therapy 至少 1 段（含 NULL 作决策失败对照）
    · 至少 1 段 boundary（target≠actual）
    · 至少 1 段 NULL actual（决策失败对照）
    · 优先 complete，无 complete 则退到 fragment
  - 专项安全池（safety_tier=professional_only）独立导出，给专业组

不修改 candidate_pool.json / seed_runs（spec §10）。
"""
import json
import sys
from pathlib import Path

# 路径
_AGENT_DIR = Path(__file__).resolve().parent.parent
_PROJECT_DIR = _AGENT_DIR
POOL_JSON = _PROJECT_DIR / "outputs" / "candidate_pool.json"
SEED_RUNS_DIR = _PROJECT_DIR / "outputs" / "seed_runs"
EVAL_DIR = _PROJECT_DIR / "outputs" / "eval_samples"
EVAL_DIR.mkdir(parents=True, exist_ok=True)
OUT_MAIN = EVAL_DIR / "main_samples.json"
OUT_PRO = EVAL_DIR / "professional_only_samples.json"

# 抽样规则
MIN_MAIN_SAMPLES = 10
NULL_MARKER = "NULL"  # 决策未触发疗法的标记


def _all_cases(pool: dict) -> list[dict]:
    """聚合普通池所有 case（complete + fragment）。"""
    out = []
    for _therapy, cases in pool["complete_by_therapy"].items():
        for c in cases:
            out.append({
                "case_id": c["case_id"],
                "target": c["target_therapy"],
                "actual": c["actual_therapy"],
                "quality": "complete",
                "safety_tier": c.get("safety_tier", "general"),
            })
    for c in pool["fragments"]:
        out.append({
            "case_id": c["case_id"],
            "target": c.get("target_therapy"),
            "actual": c.get("actual_therapy") or NULL_MARKER,
            "quality": "fragment",
            "safety_tier": c.get("safety_tier", "general"),
        })
    return out


def _select_first(cases: list[dict], key_fn, used_ids: set) -> dict | None:
    """从 cases 中按 key_fn 取第一个未用过的；返回并标记 used。"""
    for c in cases:
        if c["case_id"] not in used_ids:
            return c
    return None


def select_main_samples(pool: dict) -> list[dict]:
    """从普通池抽取主样本（≥10 段，覆盖 5 疗法 + NULL + 1 boundary）。"""
    cases = [c for c in _all_cases(pool) if c["safety_tier"] == "general"]
    used: set[str] = set()
    selected: list[dict] = []

    # 1. 每 actual_therapy 取 1 段（优先 complete）
    complete_cases = [c for c in cases if c["quality"] == "complete"]
    fragment_cases = [c for c in cases if c["quality"] == "fragment"]
    for target_therapy in ["ACT", "CBT", "DBT", "MI", "SFBT"]:
        pick = _select_first(
            [c for c in complete_cases if c["actual"] == target_therapy],
            None, used,
        ) or _select_first(
            [c for c in fragment_cases if c["actual"] == target_therapy],
            None, used,
        )
        if pick:
            selected.append(pick)
            used.add(pick["case_id"])

    # 2. NULL（决策失败）至少 1 段
    null_pick = _select_first(
        [c for c in cases if c["actual"] == NULL_MARKER],
        None, used,
    )
    if null_pick:
        selected.append(null_pick)
        used.add(null_pick["case_id"])

    # 3. boundary（target≠actual 且两者都非 NULL）至少 1 段
    bnd_pick = _select_first(
        [c for c in cases
         if c["target"] and c["actual"] != NULL_MARKER
         and c["target"] != c["actual"]
         and c["quality"] == "complete"],
        None, used,
    )
    if bnd_pick:
        selected.append(bnd_pick)
        used.add(bnd_pick["case_id"])

    # 4. 不足 MIN_MAIN_SAMPLES 则补充（任意剩余 case）
    if len(selected) < MIN_MAIN_SAMPLES:
        for c in complete_cases + fragment_cases:
            if c["case_id"] in used:
                continue
            selected.append(c)
            used.add(c["case_id"])
            if len(selected) >= MIN_MAIN_SAMPLES:
                break

    return selected


def select_professional_samples(pool: dict) -> list[dict]:
    """从专项池抽取专业组样本（含完整 + fragment）。"""
    out = []
    pro = pool.get("professional_only_pool", {})
    for _therapy, cases in pro.get("complete_by_therapy", {}).items():
        for c in cases:
            out.append({
                "case_id": c["case_id"],
                "target": c["target_therapy"],
                "actual": c["actual_therapy"],
                "quality": "complete",
                "safety_tier": "professional_only",
            })
    for c in pro.get("fragments", []):
        out.append({
            "case_id": c["case_id"],
            "target": c.get("target_therapy"),
            "actual": c.get("actual_therapy") or NULL_MARKER,
            "quality": "fragment",
            "safety_tier": "professional_only",
        })
    return out


def build_decision_events(run_path: Path) -> list[dict]:
    """从 seed_runs/{case_id}.json 抽出每轮决策事件序列（turn_start / safety / therapy_decide / orchestration / continue_eval）。"""
    with open(run_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    events: list[dict] = []
    for t in summary.get("turns", []):
        # 抽取每轮关键字段
        safety = None
        therapy_decide = None
        orchestration = None
        continue_eval = None

        for ev in t.get("events", []):
            etype = ev.get("event")
            if etype == "safety_done":
                safety = ev.get("data")
            elif etype == "therapy_decide_done":
                therapy_decide = ev.get("data")
            elif etype == "orchestration_done":
                orchestration = ev.get("data")
            elif etype == "continue_eval_done":
                continue_eval = ev.get("data")

        events.append({
            "turn": t["turn"],
            "user_text": t.get("user_text"),
            "agent_text": t.get("agent_text"),
            "safety": safety,
            "therapy_decide": therapy_decide,
            "orchestration": orchestration,
            "continue_eval": continue_eval,
        })
    return events


def enrich_with_events(samples: list[dict]) -> list[dict]:
    """给每段 sample 附上决策事件序列（从 seed_runs 读）。"""
    enriched = []
    for s in samples:
        run_path = SEED_RUNS_DIR / f"{s['case_id']}.json"
        if run_path.exists():
            s["decision_events"] = build_decision_events(run_path)
        else:
            s["decision_events"] = []
        enriched.append(s)
    return enriched


def main():
    pool = json.loads(POOL_JSON.read_text())

    main_samples = select_main_samples(pool)
    main_samples = enrich_with_events(main_samples)

    pro_samples = select_professional_samples(pool)
    pro_samples = enrich_with_events(pro_samples)

    OUT_MAIN.write_text(json.dumps(main_samples, ensure_ascii=False, indent=2))
    OUT_PRO.write_text(json.dumps(pro_samples, ensure_ascii=False, indent=2))

    print(f"[eval_pick_samples] main: {len(main_samples)} samples → {OUT_MAIN}", file=sys.stderr)
    print(f"[eval_pick_samples] professional: {len(pro_samples)} samples → {OUT_PRO}", file=sys.stderr)


if __name__ == "__main__":
    main()