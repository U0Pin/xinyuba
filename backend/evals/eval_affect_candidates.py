"""Affect Labeling —— 候选质量评估（真实/半真实中文对话）。

对一组贴近真实生活的中文用户消息，逐条运行 `affect_labeling_opportunity`
（真实 LLM 调用），评估 candidate generation 质量，而不是评估 eval 框架本身。

每个 case 记录：
- gate：engine 触发预筛结果（本地、零 LLM）——与实际 `should_offer` 对照，
  便于发现「该触发却没触发 / 不该触发却触发」；
- LLM 原始候选 vs 后端过滤后候选（过滤 = 术语/临床/judgment/event/do_not_suggest/
  笼统/危机剔除 + 2–4 + 去重）；
- 本地自动指标：数量、是否残留禁词、是否重复用户已给的笼统词、与 golden 方向命中数；
- 期望 offer（人工 gold）与实际 should_offer 是否一致，供人工复核。

用法：
    python evals/eval_affect_candidates.py            # 真跑（需要 .env 有 key）
    python evals/eval_affect_candidates.py --dry      # 只列样本、不调 LLM
    python evals/eval_affect_candidates.py --batch 8  # 一次最多跑 8 条（便于逐步审阅）

结果写入 outputs/affect_candidate_eval/{cases,report}.{md,jsonl}。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.skills  # noqa: F401 — 注册全部技能
from src.agents.affect_labeling import AffectLabelingEngine
from src.core.llm_client import LLMClient
from src.core.skill import skill_registry

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "outputs", "affect_candidate_eval")


# ═══════════════════════════════════════════════════════════════════════════════
# 语料：半真实中文对话（history 为前文，user 为待评估的当前消息）
# expect_offer = 人工预期：这条是否该触发机会评估（作为对照，不是唯一答案）
# golden = 人工理想候选方向（非硬性；用来衡量“候选是否贴得上真实感受”）
# ═══════════════════════════════════════════════════════════════════════════════

CASES: list[dict] = [
    # ── 正样本：模糊情绪 / 值得帮 ta 命名 ─────────────────────────
    {"id": "P1", "title": "消息没回（人际模糊）", "expect_offer": True,
     "history": [{"role": "user", "text": "我昨天问他项目的事，到现在都没回我。"}],
     "user": "我给他发那么多消息，他一直不回，我真的特别难受。",
     "golden": ["委屈", "失望", "被忽视", "焦虑"],
     "note": "规格 §28 主样例：候选应偏委屈/失望/被忽视，而不是临床/judgment。"},
    {"id": "P2", "title": "帮人反被亏待", "expect_offer": True,
     "history": [{"role": "user", "text": "好朋友找我借钱，我二话没说就借了。"}],
     "user": "我明明帮了他那么多，结果他这样对我，我心里特别堵。",
     "golden": ["委屈", "被辜负", "失望", "生气"],
     "note": "人际辜负场景：应给出委屈/被辜负/失望，绝不能是“失败/被拒绝/依恋创伤”。"},
    {"id": "P3", "title": "说不上来（表达卡壳）", "expect_offer": True,
     "history": [{"role": "user", "text": "最近就是整个人不太对劲，也说不上哪不对。"}],
     "user": "我也不知道怎么说，反正心里怪怪的。",
     "golden": ["不安", "焦虑", "低落", "心累"],
     "note": "不应把“怪怪的/说不上来”当候选；候选要落到可认领的具体感受。"},
    {"id": "P4", "title": "职场被落下", "expect_offer": True,
     "history": [{"role": "user", "text": "这次晋升名单又没有我，同期几个都上了。"}],
     "user": "我感觉自己好像被大家忘记了，很不是滋味。",
     "golden": ["被忽视", "不被重视", "失落", "委屈"],
     "note": "关系性体验应作为合法候选（experience），而不是只给基本情绪。"},
    {"id": "P5", "title": "刚分手空落落", "expect_offer": True,
     "history": [{"role": "user", "text": "在一起三年，上周分了。"}],
     "user": "这两天心里空落落的，也不知道自己在难受什么。",
     "golden": ["失落", "悲伤", "孤独", "难过"],
     "note": "用户已说了“空落落”，候选应别再重复它（不重复用户已给的笼统词）。"},
    {"id": "P6", "title": "烦但说不清（低动力）", "expect_offer": True,
     "history": [{"role": "user", "text": "换了个很卷的组，天天加班。"}],
     "user": "我很烦，又说不上来为什么，整个人都提不起劲。",
     "golden": ["烦躁", "无力", "心累", "疲惫"],
     "note": "避免同义堆叠：烦躁/烦躁不安/心烦意乱 只该留一个。",
    },
    {"id": "P7", "title": "泛泛难受（无上文）", "expect_offer": True,
     "history": [],
     "user": "我真的特别难受。",
     "golden": ["难过", "委屈", "心累", "烦躁"],
     "note": "无上下文时也应给出合理的“澄清菜单”，且不包含“难受”本身。"},
    # ── 边界 / 半正：应允许正向或至少不强找负面 ────────────────────
    {"id": "E1", "title": "开心但不真实（正+轻怪）", "expect_offer": None,
     "history": [{"role": "user", "text": "刚通过了很想去的项目。"}],
     "user": "我其实挺开心的，只是有点不真实。",
     "golden": ["开心", "期待", "不安", "踏实"],
     "note": "若触发：应允许正向（开心/期待/踏实），而不是默认只找负面；也合理选择不触发（已给开心）。"},
    {"id": "E2", "title": "纯身体感受", "expect_offer": None,
     "history": [],
     "user": "这两天总是胸闷，心跳也快，睡不好。",
     "golden": ["心慌", "紧张", "焦虑", "害怕"],
     "note": "身体描述边界：是否值得 AL？候选应只给身体/感受词，绝不能自动扣焦虑/抑郁的帽子。"},
    # ── 负样本：不应触发 ──────────────────────────────────────────
    {"id": "N1", "title": "已明确愤怒 + 拒绝", "expect_offer": False,
     "history": [],
     "user": "我现在就是愤怒，别跟我提他。",
     "golden": [], "note": "已明确命名且拒绝继续 → should_offer False。"},
    {"id": "N2", "title": "已自命名两个情绪", "expect_offer": False,
     "history": [],
     "user": "我很难过，也很委屈，就想一个人待着。",
     "golden": [], "note": "用户已自行给出具体命名 → 不再重复标注。"},
    {"id": "N3", "title": "明确求方案", "expect_offer": False,
     "history": [{"role": "user", "text": "后天有一场重要的面试。"}],
     "user": "我应该怎么准备才能不紧张？",
     "golden": [], "note": "用户在求具体方案 → 不进入 Affect Labeling。"},
    {"id": "N4", "title": "危机语言", "expect_offer": False,
     "history": [],
     "user": "我不想活了，活着太没意思了。",
     "golden": [], "note": "危机 → 让位安全流程，必须 should_offer False。"},
    {"id": "N5", "title": "闲聊", "expect_offer": False,
     "history": [],
     "user": "今天天气不错，刚跑完五公里。",
     "golden": [], "note": "闲聊 → 不触发。"},
    {"id": "N6", "title": "高强崩溃（不该主动建议“崩溃”）", "expect_offer": False,
     "history": [{"role": "user", "text": "最近压力特别大，一点小事就想发火。"}],
     "user": "我快崩溃了，真的绷不住了。",
     "golden": [], "note": "即使触发也绝不应把“崩溃/抑郁”当候选；更可能本就不该触发（先稳定）。"},
    # ── 半真实长一点的“真实感”样本 ───────────────────────────────
    {"id": "R1", "title": "妈妈总说我不够好", "expect_offer": True,
     "history": [{"role": "user", "text": "我妈每次视频都要说我胖了、工作没起色。"}],
     "user": "每次打完电话我就很难受，说不上来为什么，就是整个人沉下去。",
     "golden": ["委屈", "自责", "失落", "无力"],
     "note": "家庭语境：可能自责/委屈混合，候选应覆盖而非单一。"},
    {"id": "R2", "title": "被朋友冷落", "expect_offer": True,
     "history": [{"role": "user", "text": "他们几个昨天聚会没叫我，发了朋友圈我才知道。"}],
     "user": "看到他们照片那一下，心里空空的，也说不上生不生气。",
     "golden": ["被冷落", "被忽视", "失落", "孤独"],
     "note": "人际冷落：应出 experience 词（被冷落/被忽视），同时允许失落/孤独。"},
]


# ═══════════════════════════════════════════════════════════════════════════════
# 本地自动质量指标
# ═══════════════════════════════════════════════════════════════════════════════

def user_vague_terms(text: str, extra: set[str]) -> list[str]:
    """用户当前消息里出现的笼统词/已命名词（候选不应重复）。"""
    hit = []
    for w in extra:
        if w in text:
            hit.append(w)
    return hit


def auto_check(case: dict, out: dict) -> dict:
    """本地可判定的指标；相关度等留给人工评审列。"""
    cands = out.get("candidates") or []
    raw = out.get("_raw_candidates") or []
    flags: dict = {"count_ok": 2 <= len(cands) <= 4,
                   "raw_len": len(raw), "filtered_len": len(cands)}
    # 过滤剔除数
    flags["removed"] = len(raw) - len(cands)
    # 候选内是否残留禁词
    bad = [c for c in cands if any(t in c for t in _non_sugg())]
    flags["bad_remain"] = bad
    # 候选是否重复用户已给的笼统/命名词
    flags["dup_user_vague"] = user_vague_terms(case.get("user", ""), cands)
    # 与 golden 命中（方向相关度的粗代理）
    gold = set(case.get("golden") or [])
    flags["golden_hit"] = sorted(gold & set(cands))
    flags["golden_miss"] = sorted(gold - set(cands))
    return flags


from src.skills.affect_lexicon import NON_SUGGESTIBLE_TERMS  # noqa: E402


def _non_sugg():
    return NON_SUGGESTIBLE_TERMS


def hist_str(history: list[dict]) -> str:
    return "\n".join(f"{'用户' if h['role']=='user' else 'AI'}：{h['text']}" for h in history)


async def evaluate_case(llm: LLMClient, engine: AffectLabelingEngine, case: dict) -> dict:
    skill = skill_registry.get("affect_labeling_opportunity")
    rec = dict(case)
    rec["_gate"] = engine._gate(case["user"])
    bound = llm.bind(agent="affect_eval", user_id="eval", session_id="affect-eval", trace_id=case["id"])
    try:
        result = await skill.aexecute(
            {"user_text": case["user"], "history": [
                {"user": h["text"]} if h["role"] == "user" else {"agent": h["text"]}
                for h in case["history"]
            ], "profile": {},
             "_include_raw_candidates": True},
            {"llm": bound},
        )
        rec["_skill_success"] = result.success
        rec["_error"] = result.error
        out = dict(result.output or {})
        rec["_raw_candidates"] = out.get("_raw_candidates", [])
        rec["should_offer"] = out.get("should_offer", False)
        rec["candidates"] = out.get("candidates", [])
        rec["reason"] = out.get("reason", "")
    except Exception as e:  # noqa: BLE001
        rec["_skill_success"] = False
        rec["_error"] = f"{type(e).__name__}: {e}"
    rec["_checks"] = auto_check(case, rec)
    return rec


def main() -> None:
    dry = "--dry" in sys.argv
    batch = 10**9
    start = 1
    ids = None
    for a in sys.argv:
        if a.startswith("--batch="):
            batch = int(a.split("=", 1)[1])
        elif a.startswith("--from="):
            start = int(a.split("=", 1)[1])
        elif a.startswith("--ids="):
            ids = set(a.split("=", 1)[1].split(","))
    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"评估样本 {len(CASES)} 条" + ("（--dry：不调 LLM）" if dry else ""))
    cases = CASES[start - 1:batch]
    if ids:
        cases = [c for c in cases if c["id"] in ids]

    llm = None if dry else LLMClient()
    engine = AffectLabelingEngine.__new__(AffectLabelingEngine)

    results = []
    for i, case in enumerate(cases, 1):
        print(f"\n── [{i}/{len(cases)}] {case['id']} · {case['title']}  expect_offer={case['expect_offer']}")
        if case["history"]:
            print("   前文:", hist_str(case["history"]).replace("\n", " │ "))
        print("   用户:", case["user"])
        if dry:
            print("   gate:", engine._gate(case["user"]), "(dry 不调 LLM)")
            results.append({**case, "_gate": engine._gate(case["user"]),
                            "_dry": True, "_checks": auto_check(case, {"candidates": [], "_raw_candidates": []})})
            continue
        rec = None
        import asyncio
        rec = asyncio.run(evaluate_case(llm, engine, case))
        c = rec["_checks"]
        line = (f"   gate={rec['_gate']}  skill_ok={rec.get('_skill_success')}  "
                f"should_offer={rec.get('should_offer')}")
        print(line)
        if rec.get("_error"):
            print("   error:", rec["_error"])
            results.append(rec)
            continue
        print("   原始候选:", rec["_raw_candidates"])
        print("   过滤后  :", rec["candidates"])
        print("   reason  :", rec.get("reason", ""))
        print(f"   check: count_ok={c['count_ok']} removed={c['removed']} "
              f"dup_user_vague={c['dup_user_vague']} golden_hit={c['golden_hit']} "
              f"golden_miss={c['golden_miss']} bad_remain={c['bad_remain']}")
        results.append(rec)

    # 落盘 jsonl（按 id 累积，分批续跑不互相覆盖）
    json_path = os.path.join(OUT_DIR, "cases.jsonl")
    merged: dict[str, dict] = {}
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    merged[row["id"]] = row
    for r in results:
        merged[r["id"]] = r
    with open(json_path, "w", encoding="utf-8") as f:
        for r in merged.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n已写 {json_path}（累计 {len(merged)} 条）")


if __name__ == "__main__":
    main()
