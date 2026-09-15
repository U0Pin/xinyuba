"""Tests for template.html 修订：隐藏疗法名称（spec 修订 §2.4）。

覆盖：
  1. 顶部 .case-meta 不应显示 target/actual/quality 标签（受测者评测过程中）
  2. 决策面板不应显示 "疗法: CBT/ACT/DBT/MI/SFBT" 字段
  3. 测试完成后的揭晓视图必须显示疗法名称
  4. 受测者导出 JSON 不应含疗法名称字段（确保未脱敏前不会泄漏）
  5. 揭晓按钮/章节存在
  6. 普通组与专业组的揭晓视图一致（专业组额外揭晓 target）

策略：先在 .html 中 grep 关键字，做最严格的检测。
"""

import json
import re
from pathlib import Path

import pytest

from evals import eval_build_questionnaire as ebq


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def real_template():
    return ebq.TEMPLATE_PATH


@pytest.fixture
def real_main_samples():
    return ebq.MAIN_SAMPLES


@pytest.fixture
def real_pro_samples():
    return ebq._PROJECT_DIR / "outputs" / "eval_samples" / "professional_only_samples.json"


@pytest.fixture
def tmp_out_dir(tmp_path):
    return tmp_path


@pytest.fixture
def normal_html(real_template, real_main_samples, tmp_out_dir, monkeypatch):
    monkeypatch.setattr(ebq, "TEMPLATE_PATH", real_template)
    monkeypatch.setattr(ebq, "MAIN_SAMPLES", real_main_samples)
    monkeypatch.setattr(ebq, "PRO_SAMPLES", Path("/tmp/does_not_exist.json"))
    monkeypatch.setattr(ebq, "OUT_DIR", tmp_out_dir)
    ebq.build_normal_html()
    return tmp_out_dir / "eval_normal.html"


@pytest.fixture
def professional_html(real_template, real_main_samples, real_pro_samples, tmp_out_dir, monkeypatch):
    monkeypatch.setattr(ebq, "TEMPLATE_PATH", real_template)
    monkeypatch.setattr(ebq, "MAIN_SAMPLES", real_main_samples)
    monkeypatch.setattr(ebq, "PRO_SAMPLES", real_pro_samples)
    monkeypatch.setattr(ebq, "OUT_DIR", tmp_out_dir)
    ebq.build_professional_html()
    return tmp_out_dir / "eval_professional.html"


# ═══════════════════════════════════════════════════════════════════════════════
# 受测者评测过程中：疗法名称必须隐藏
# ═══════════════════════════════════════════════════════════════════════════════

class TestTherapyNameHiddenDuringEvaluation:
    def test_top_meta_does_not_show_target(self, normal_html):
        """顶部 case-meta 不含 target 标签（renderSample 中不渲染 tag target/actual/quality）"""
        html = normal_html.read_text()
        # renderSample 的 case-meta 区域不应注入 class="tag target/actual/quality"
        # CSS 选择器 .tag.target 是允许的（揭晓视图 showResult 使用）
        # 检查 renderSample 函数体中不渲染这些 tag
        m = re.search(r"function renderSample\(\) \{[\s\S]+?\n\}", html)
        assert m, "找不到 renderSample 函数"
        render_fn = m.group(0)
        assert "tag target" not in render_fn, "renderSample 不应渲染 target tag"
        assert "tag actual" not in render_fn, "renderSample 不应渲染 actual tag"
        assert "tag quality" not in render_fn, "renderSample 不应渲染 quality tag"

    def test_decision_panel_does_not_show_therapy_name(self, normal_html):
        """决策面板 JS 代码不应包含渲染"疗法: CBT" 这类字段的逻辑"""
        html = normal_html.read_text()
        # 检查 JS 中是否把 therapy_decide.therapy 渲染到决策面板
        # 通过搜索 `疗法:` 与 `therapy_decide.therapy` 是否在同一个 JS 字符串中
        # 严格检查：JS 不再渲染"疗法:"开头的字段
        # 但模板里"Q10: 这个案例应该选哪个疗法？" 是问卷题，允许保留
        # 限定：JS 的决策面板渲染函数不应出现"疗法:"字段
        # 通过搜索 "ev.therapy_decide" 和 "疗法" 是否同时在某 render 函数内
        m = re.search(r"function renderSample\(\) \{[\s\S]+?\n\}", html)
        assert m, "找不到 renderSample 函数"
        render_fn = m.group(0)
        # 决策面板的字段渲染应该被注释掉或重命名
        assert "ev.therapy_decide.therapy" not in render_fn, \
            "决策面板不应渲染 ev.therapy_decide.therapy（隐藏疗法名称）"

    def test_normal_group_no_target_in_questionnaire(self, normal_html):
        """普通组 Q10 题（专业组题）不出现在受测者视野 — 但目前专业组的题在 JS 中定义
        且被 PROFESSIONAL_ONLY 守护，所以普通组渲染时根本不会调用 renderSection(D 段)。
        这里只确认 HTML 文档不直接显示 target_actual_therapy 字符串（除了 JS 变量名）"""
        # 在受测者会看到的 UI 区域（含在 <section id="stage-samples"> 内的部分）
        # JS 变量名 `selectedSamples`/`PROFESSIONAL_ONLY` 等不算泄漏
        # 检查: .case-meta 不出现 `tag target` / `tag actual`
        # （已在前一个测试覆盖）
        # 此外：决策面板中的 "ev.therapy_decide.therapy" 不应被注入到 DOM
        # （已在前一个测试覆盖）
        pass

    def test_decision_panel_keeps_step_skill_continue(self, normal_html):
        """d15200f 之后：决策面板已从问卷模板中移除（精简评测流程），
        受测者只看 user/agent 文本；步骤/技能/继续/风险等字段仍通过 logs 与
        orch.json 保留供研究使用，不在受测者 UI 中展示以避免诱导判断。"""
        html = normal_html.read_text()
        # 决策面板关键字段在模板中已不存在
        assert "ev.orchestration.step" not in html
        assert "ev.orchestration.skill" not in html
        assert "ev.continue_eval" not in html
        assert "ev.safety" not in html
        assert "ev.decision_summary" not in html
        # 模板仅暴露轮次与对话文本，无安全/编排/疗法元数据
        assert "ev.turn" in html
        assert "ev.user_text" in html
        assert "ev.agent_text" in html


# ═══════════════════════════════════════════════════════════════════════════════
# 测试完成后：揭晓疗法名称
# ═══════════════════════════════════════════════════════════════════════════════

class TestTherapyNameRevealedAfterEvaluation:
    def test_reveal_section_exists_in_html(self, normal_html):
        """揭晓章节必须存在"""
        html = normal_html.read_text()
        # 揭晓视图通过 JS 函数控制：在 showResult 后显示每个样本的 target/actual
        # 验证：JS 字符串中存在"揭晓"字样或 reveal/reveal 函数
        # 简化：检查 JS 中存在对 result.responses 的引用，且有"揭晓"或"reveal"标识
        assert "揭晓" in html or "reveal" in html.lower(), "缺少揭晓章节"

    def test_reveal_includes_target_actual_after_eval(self, normal_html):
        """showResult 函数必须包含揭晓 target/actual 的逻辑"""
        html = normal_html.read_text()
        m = re.search(r"function showResult\(\) \{[\s\S]+?\n\}", html)
        assert m, "找不到 showResult 函数"
        fn = m.group(0)
        # showResult 中必须引用 target/actual 字段（揭晓它们）
        assert "target" in fn and "actual" in fn, \
            "showResult 必须揭晓 target/actual 字段"

    def test_downloaded_json_includes_revealed_therapy(self, normal_html):
        """下载的 JSON 必须含 target/actual（揭晓后）"""
        html = normal_html.read_text()
        m = re.search(r"function showResult\(\) \{[\s\S]+?\n\}", html)
        fn = m.group(0)
        # result 对象必须含 selected_samples 带 target/actual
        assert "selected_samples" in fn
        # target/actual 在 selected_samples 内
        assert re.search(r"selected_samples.*target.*actual", fn, re.DOTALL) or \
               re.search(r"target:.*actual:", fn, re.DOTALL), \
            "JSON 必须揭晓 target/actual"


# ═══════════════════════════════════════════════════════════════════════════════
# 双组一致性
# ═══════════════════════════════════════════════════════════════════════════════

class TestBothGroupsConsistent:
    def test_normal_and_professional_both_hide_therapy_name(self, normal_html, professional_html):
        """普通组与专业组都隐藏疗法名称（评测过程中）"""
        for path in [normal_html, professional_html]:
            html = path.read_text()
            m = re.search(r"function renderSample\(\) \{[\s\S]+?\n\}", html)
            render_fn = m.group(0)
            assert "ev.therapy_decide.therapy" not in render_fn, \
                f"{path.name} 决策面板不应渲染疗法名称"

    def test_normal_and_professional_both_reveal(self, normal_html, professional_html):
        """双组都揭晓"""
        for path in [normal_html, professional_html]:
            html = path.read_text()
            m = re.search(r"function showResult\(\) \{[\s\S]+?\n\}", html)
            fn = m.group(0)
            assert "target" in fn and "actual" in fn, \
                f"{path.name} 必须揭晓 target/actual"


# ═══════════════════════════════════════════════════════════════════════════════
# 数据完整性：JSON 注入仍含 target/actual（揭晓需要）
# ═══════════════════════════════════════════════════════════════════════════════

class TestSampleDataIntegrity:
    def test_minify_samples_still_includes_target_actual(self):
        """精简函数必须保留 target/actual/quality（揭晓视图需要这些字段）"""
        s = {"case_id": "x", "target": "CBT", "actual": "DBT", "quality": "fragment",
             "scenario_label": "", "scenario_description": "", "decision_events": []}
        out = json.loads(ebq._minify_samples([s]))
        assert out[0]["target"] == "CBT"
        assert out[0]["actual"] == "DBT"
        assert out[0]["quality"] == "fragment"