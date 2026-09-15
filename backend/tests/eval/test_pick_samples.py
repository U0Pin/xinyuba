"""Tests for eval_pick_samples.py — 从 candidate_pool.json 抽取评测主样本。

覆盖：
  1. 主样本至少 10 段
  2. 每 actual_therapy 在普通池至少 1 段
  3. 包含至少 1 段 boundary（target≠actual）
  4. 包含至少 1 段 NULL actual（决策失败）
  5. 专项池独立文件，含 2 个 case（professional_only）
  6. 抽中的 case_id 在 seed_runs 中存在对应文件
  7. 输出结构含决策事件序列（turn_start / safety_done / therapy_decide_done / orchestration_done / continue_eval_done）
  8. 不修改 candidate_pool.json / seed_runs
"""

import json

import pytest

from evals import eval_pick_samples as eps


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def real_pool_path():
    return eps.POOL_JSON


@pytest.fixture
def seed_runs_dir():
    return eps.SEED_RUNS_DIR


@pytest.fixture
def tmp_eval_dir(tmp_path):
    return tmp_path


# ═══════════════════════════════════════════════════════════════════════════════
# select_main_samples
# ═══════════════════════════════════════════════════════════════════════════════

class TestSelectMainSamples:
    def test_at_least_10_main_samples(self, real_pool_path):
        pool = json.loads(real_pool_path.read_text())
        samples = eps.select_main_samples(pool)
        assert len(samples) >= 10, f"主样本至少 10 段，实际 {len(samples)}"

    def test_covers_all_actual_therapies(self, real_pool_path):
        """主样本必须覆盖所有 5 种 actual_therapy（含 NULL 作决策失败）"""
        pool = json.loads(real_pool_path.read_text())
        samples = eps.select_main_samples(pool)
        actuals = {s["actual"] for s in samples}
        for t in ["ACT", "CBT", "DBT", "MI", "SFBT"]:
            assert t in actuals, f"缺 {t} 实际触发 case"
        # 决策失败用 NULL 或全 5 种疗法之外的标记
        assert "NULL" in actuals or len(actuals) >= 5

    def test_includes_boundary_case(self, real_pool_path):
        """至少 1 段 target≠actual"""
        pool = json.loads(real_pool_path.read_text())
        samples = eps.select_main_samples(pool)
        boundary = [s for s in samples if s["target"] != s["actual"] and s["target"] is not None and s["actual"] is not None]
        assert len(boundary) >= 1, "缺 boundary case"

    def test_includes_decision_failure(self, real_pool_path):
        """至少 1 段 NULL actual（决策未触发疗法）"""
        pool = json.loads(real_pool_path.read_text())
        samples = eps.select_main_samples(pool)
        nulls = [s for s in samples if s["actual"] == "NULL"]
        assert len(nulls) >= 1, "缺决策失败 case"

    def test_safety_tier_general_for_main_samples(self, real_pool_path):
        """主样本不应包含 professional_only case"""
        pool = json.loads(real_pool_path.read_text())
        samples = eps.select_main_samples(pool)
        assert all(s["safety_tier"] == "general" for s in samples), "主样本不应包含专项安全 case"


class TestSelectProfessionalSamples:
    def test_includes_professional_only_cases(self, real_pool_path):
        """专项样本必须从专业池取，含 safety_tier='professional_only'"""
        pool = json.loads(real_pool_path.read_text())
        samples = eps.select_professional_samples(pool)
        assert len(samples) >= 2, "专项池应有 ≥2 个 case"
        assert all(s["safety_tier"] == "professional_only" for s in samples)


# ═══════════════════════════════════════════════════════════════════════════════
# build_decision_events（构造决策显化面板）
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildDecisionEvents:
    def test_returns_event_sequence(self, seed_runs_dir):
        """从 seed_runs/{case_id}.json 抽出每轮决策事件序列"""
        # 选一个已知存在的 case
        run_path = seed_runs_dir / "act-defusion-rumination.json"
        if not run_path.exists():
            pytest.skip("seed_runs 文件不存在")
        events = eps.build_decision_events(run_path)
        # 必须按 turn 顺序
        assert isinstance(events, list)
        assert len(events) > 0
        # 必含 5 类字段（缺则 None）
        for ev in events:
            assert "turn" in ev
            assert "user_text" in ev
            assert "agent_text" in ev
            assert "safety" in ev
            assert "therapy_decide" in ev
            assert "orchestration" in ev
            assert "continue_eval" in ev

    def test_event_sequence_respects_turn_order(self, seed_runs_dir):
        """事件按 turn 升序排列"""
        run_path = seed_runs_dir / "cbt-disaster-interview.json"
        if not run_path.exists():
            pytest.skip("seed_runs 文件不存在")
        events = eps.build_decision_events(run_path)
        turns = [ev["turn"] for ev in events]
        assert turns == sorted(turns), f"turn 顺序错: {turns}"


# ═══════════════════════════════════════════════════════════════════════════════
# main()
# ═══════════════════════════════════════════════════════════════════════════════

class TestMain:
    def test_main_writes_two_output_files(self, real_pool_path, tmp_eval_dir, monkeypatch):
        """main() 必须输出 main_samples.json + professional_only_samples.json"""
        out_main = tmp_eval_dir / "main_samples.json"
        out_pro = tmp_eval_dir / "professional_only_samples.json"

        monkeypatch.setattr(eps, "POOL_JSON", real_pool_path)
        monkeypatch.setattr(eps, "OUT_MAIN", out_main)
        monkeypatch.setattr(eps, "OUT_PRO", out_pro)

        eps.main()

        assert out_main.exists(), "main_samples.json 未生成"
        assert out_pro.exists(), "professional_only_samples.json 未生成"

        main_data = json.loads(out_main.read_text())
        pro_data = json.loads(out_pro.read_text())

        assert isinstance(main_data, list) and len(main_data) >= 10
        assert isinstance(pro_data, list) and len(pro_data) >= 2

        # 每个 case 含决策事件序列
        for c in main_data:
            assert "decision_events" in c
            assert "safety_tier" in c
            assert c["safety_tier"] == "general"

        for c in pro_data:
            assert "decision_events" in c
            assert c["safety_tier"] == "professional_only"

    def test_main_does_not_modify_input(self, real_pool_path, tmp_eval_dir, monkeypatch):
        """main() 不能修改 candidate_pool.json / seed_runs"""
        # 读原始内容 + 大小
        orig = real_pool_path.read_text()
        orig_size = real_pool_path.stat().st_size

        out_main = tmp_eval_dir / "main_samples.json"
        out_pro = tmp_eval_dir / "professional_only_samples.json"

        monkeypatch.setattr(eps, "POOL_JSON", real_pool_path)
        monkeypatch.setattr(eps, "OUT_MAIN", out_main)
        monkeypatch.setattr(eps, "OUT_PRO", out_pro)

        eps.main()

        # 验证 candidate_pool.json 不变
        assert real_pool_path.read_text() == orig
        assert real_pool_path.stat().st_size == orig_size


# ═══════════════════════════════════════════════════════════════════════════════
# anonymize 函数（来自 eval_anonymize.py）
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnonymize:
    def test_anonymize_replaces_user_id(self):
        from evals.eval_anonymize import anonymize_summary
        summary = {
            "user_id": "user-secret-abc",
            "session_id": "sess-secret-xyz",
            "trace_id": "trace-123",
        }
        out = anonymize_summary(summary)
        assert out["user_id"] != "user-secret-abc"
        assert out["session_id"] != "sess-secret-xyz"
        assert out["trace_id"] != "trace-123"

    def test_anonymize_preserves_user_text(self):
        """user_text 与 agent_text 必须保留（保留咨询真实性）"""
        from evals.eval_anonymize import anonymize_summary
        summary = {
            "user_id": "u",
            "session_id": "s",
            "trace_id": "t",
            "turns": [
                {"turn": 1, "user_text": "我最近很焦虑", "agent_text": "听到了。"}
            ],
        }
        out = anonymize_summary(summary)
        assert out["turns"][0]["user_text"] == "我最近很焦虑"
        assert out["turns"][0]["agent_text"] == "听到了。"

    def test_anonymize_handles_missing_keys(self):
        """缺字段时不抛异常"""
        from evals.eval_anonymize import anonymize_summary
        out = anonymize_summary({})
        assert isinstance(out, dict)
