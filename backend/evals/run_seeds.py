"""批量 / 单个 seed 跑通脚本（数据补料用）。

调用 Scheduler.handle_message 真实运行 seed，并把每轮 SSE 事件 + state +
orchestration 快照 dump 到 outputs/seed_runs/{seed_id}.jsonl。

不修改任何 seed 内容，不修改 Agent 决策逻辑。

用法：
  cd ea-core
  python evals/run_seeds.py mi-ambivalence-smoking        # 单 case 跑通
  python evals/run_seeds.py --list                        # 列出全部 seed id
  python evals/run_seeds.py --batch                        # 跑全部 15 个
  python evals/run_seeds.py --batch --ids cbt-disaster-interview act-value-conflict-career
"""

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

# 路径：保证 import 到 src.*
_AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENT_DIR))

from src.agents.host_agent import HostDialogueAgent  # noqa: E402
from src.agents.safety_agent import SafetyAgent  # noqa: E402
from src.agents.settlement import SettlementLine  # noqa: E402
from src.agents.therapy import (  # noqa: E402
    ActTherapyAgent,
    CbtTherapyAgent,
    DbtTherapyAgent,
    MiTherapyAgent,
    SfbtTherapyAgent,
)
from src.agents.therapy_decider import TherapyDecider  # noqa: E402
from src.core.llm_client import LLMClient  # noqa: E402
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler  # noqa: E402
from src.store.profile_store import ProfileStore  # noqa: E402
from src.store.session_store import SessionStore  # noqa: E402

import src.skills  # noqa: E402, F401 — 注册全部疗法技能

from evals.seed_scripts import all_seed_ids, get_seed  # noqa: E402

OUT_DIR = _AGENT_DIR / "outputs" / "seed_runs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def build_scheduler() -> Scheduler:
    """与 server.py / main.py 同一组装路径（cheapest model 走 CHEAP_MODEL_NAME）。"""
    llm = LLMClient()
    sessions = SessionStore()
    profiles = ProfileStore()
    therapy_agents = {
        "CBT": CbtTherapyAgent(llm, session_store=sessions),
        "ACT": ActTherapyAgent(llm, session_store=sessions),
        "DBT": DbtTherapyAgent(llm, session_store=sessions),
        "MI": MiTherapyAgent(llm, session_store=sessions),
        "SFBT": SfbtTherapyAgent(llm, session_store=sessions),
    }
    return Scheduler(
        dialogue_line=DialogueLine(
            HostDialogueAgent(llm, session_store=sessions), session_store=sessions
        ),
        decision_line=DecisionLine(
            SafetyAgent(llm),
            session_store=sessions,
            therapy_decider=TherapyDecider(llm),
            therapy_agents=therapy_agents,
        ),
        settlement_line=SettlementLine(
            llm, session_store=sessions, profile_store=profiles
        ),
        session_store=sessions,
        profile_store=profiles,
    )


async def run_one_seed(scheduler: Scheduler, seed: dict, user_id_prefix: str = "seed") -> dict:
    """跑一个 seed 全程（多轮）；返回结构化结果。"""
    user_id = f"{user_id_prefix}-{seed['id'][:24]}"
    sid = scheduler.sessions.create(user_id)
    turns: list[dict] = []

    for turn_idx, user_text in enumerate(seed["messages"], start=1):
        turn_record: dict = {
            "turn": turn_idx,
            "trace_id": None,
            "user_text": user_text,
            "agent_text_parts": [],
            "events": [],
            "final": None,
            "state_after": None,
            "orchestration_after": None,
            "error": None,
        }
        t0 = time.time()
        try:
            async for ev in scheduler.handle_message(user_id, sid, user_text):
                etype = ev["event"]
                if etype == "token":
                    turn_record["agent_text_parts"].append(ev["data"])
                elif etype == "dialogue_done":
                    turn_record["trace_id"] = ev["data"].get("trace_id")
                elif etype == "final":
                    turn_record["final"] = ev["data"]
                turn_record["events"].append({"event": etype, "data": ev.get("data")})
        except Exception as e:
            turn_record["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        turn_record["wall_seconds"] = round(time.time() - t0, 3)
        turn_record["agent_text"] = "".join(turn_record["agent_text_parts"])
        turn_record["state_after"] = scheduler.sessions.read_state(sid)
        turn_record["orchestration_after"] = scheduler.sessions.read_orchestration(sid)
        turns.append(turn_record)
        if turn_record["error"]:
            print(f"  [WARN] turn {turn_idx} error: {turn_record['error'].splitlines()[0]}",
                  file=sys.stderr)

    # ── 汇总：实际疗法 & 完整性 ────────────────────────────────
    therapy_rounds = 0
    last_orch_step = None
    orch_step_changes = 0
    switched_to_therapy_at_turn: int | None = None
    last_state = turns[-1]["state_after"] if turns else {}
    for i, t in enumerate(turns, start=1):
        st = t["state_after"]
        if st.get("therapy") and switched_to_therapy_at_turn is None:
            switched_to_therapy_at_turn = i
        if st.get("therapy"):
            therapy_rounds += 1
        orch = t["orchestration_after"]
        step = (orch or {}).get("current_step")
        if step and step != last_orch_step:
            orch_step_changes += 1
            last_orch_step = step

    actual_therapy = None
    if last_state.get("therapy"):
        actual_therapy = last_state["therapy"].get("name")
    elif switched_to_therapy_at_turn:
        # 已结束疗程，从 switch 痕迹里找：扫每轮的 state_after 第一个出现 therapy 的
        for t in turns:
            n = (t["state_after"].get("therapy") or {}).get("name")
            if n:
                actual_therapy = n
                break

    is_complete = (
        actual_therapy is not None
        and therapy_rounds >= 4
        and orch_step_changes >= 2
        and not any(t["error"] for t in turns)
    )

    summary = {
        "seed_id": seed["id"],
        "target_family": seed["target_family"],
        "scenario_label": seed["scenario_label"],
        "scenario_description": seed["scenario_description"],
        "user_id": user_id,
        "session_id": sid,
        "turns_total": len(turns),
        "switched_to_therapy_at_turn": switched_to_therapy_at_turn,
        "actual_therapy": actual_therapy,
        "therapy_rounds": therapy_rounds,
        "orch_step_changes": orch_step_changes,
        "is_complete": is_complete,
        "final_risk_level": last_state.get("risk_level"),
        "final_crisis": last_state.get("crisis", False),
        "turns": turns,
    }
    return summary


def dump_summary(summary: dict) -> Path:
    out = OUT_DIR / f"{summary['seed_id']}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return out


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("seed_id", nargs="?", default=None,
                        help="单个 seed id；省略时若带 --batch 则跑全部")
    parser.add_argument("--list", action="store_true", help="列出全部 seed id")
    parser.add_argument("--batch", action="store_true", help="跑全部 seed")
    parser.add_argument("--ids", nargs="*", default=None,
                        help="批量跑时限定 seed id 列表")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="批量跑时的并发数（默认 1；推荐 3）")
    args = parser.parse_args()

    if args.list:
        for sid in all_seed_ids():
            print(sid)
        return

    if args.batch:
        targets = args.ids or all_seed_ids()
    elif args.seed_id:
        targets = [args.seed_id]
    else:
        parser.error("需要 seed_id 或 --batch 或 --list")

    print(f"[run_seeds] targets: {targets}  concurrency={args.concurrency}", file=sys.stderr)

    sem = asyncio.Semaphore(args.concurrency)

    async def run_with_sem(seed: dict) -> dict:
        async with sem:
            scheduler = build_scheduler()
            t0 = time.time()
            summary = await run_one_seed(scheduler, seed)
            out_path = dump_summary(summary)
            print(
                f"\n── {seed['id']}  (target={seed['target_family']}, msgs={len(seed['messages'])}) ──\n"
                f"   actual_therapy={summary['actual_therapy']}  "
                f"therapy_rounds={summary['therapy_rounds']}/{summary['turns_total']}  "
                f"step_changes={summary['orch_step_changes']}  "
                f"is_complete={summary['is_complete']}  "
                f"wall={time.time()-t0:.1f}s  "
                f"→ {out_path.relative_to(_AGENT_DIR)}",
                file=sys.stderr,
            )
            return summary

    seeds = [get_seed(s) for s in targets]
    seeds = [s for s in seeds if s is not None]
    results = await asyncio.gather(*(run_with_sem(s) for s in seeds))

    # 仅观察日志（不作为 gate，不影响 Phase A→B 决策）
    n_complete = sum(1 for s in results if s["is_complete"])
    n_total = len(results)
    print(
        f"[run_seeds] phase_observer: complete={n_complete}/{n_total} "
        f"({n_complete * 100 // n_total if n_total else 0}%) — 仅观察，不作 gate",
        file=sys.stderr,
    )
    print(f"\n[run_seeds] done: {len(results)} case(s)", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())