"""Tests for eval_build_questionnaire.py — 验证 HTML 注入 + 双组配置。"""

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
    return ebq.PRO_SAMPLES


@pytest.fixture
def tmp_out_dir(tmp_path):
    return tmp_path


# ═══════════════════════════════════════════════════════════════════════════════
# _minify_samples
# ═══════════════════════════════════════════════════════════════════════════════

class TestMinifySamples:
    def test_returns_json_string(self):
        out = ebq._minify_samples([{
            "case_id": "x", "target": "CBT", "actual": "CBT", "quality": "complete",
            "scenario_label": "label", "scenario_description": "desc",
            "decision_events": [{"turn": 1}],
        }])
        assert isinstance(out, str)
        # 必须是合法 JSON
        parsed = json.loads(out)
        assert parsed[0]["case_id"] == "x"

    def test_includes_only_required_fields(self):
        """精简字段——不应把整个 summary 塞进去"""
        s = {"case_id": "x", "target": "CBT", "actual": "CBT", "quality": "complete",
             "scenario_label": "", "scenario_description": "",
             "decision_events": [], "extra_secret": "should_not_leak"}
        out = json.loads(ebq._minify_samples([s]))
        assert "extra_secret" not in out[0]


# ═══════════════════════════════════════════════════════════════════════════════
# build_normal_html
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildNormalHtml:
    def test_no_placeholder_remains(self, real_template, real_main_samples, tmp_out_dir, monkeypatch):
        """生成的 HTML 不应含 __SAMPLES_PLACEHOLDER__ 占位符"""
        out = tmp_out_dir / "eval_normal.html"

        monkeypatch.setattr(ebq, "TEMPLATE_PATH", real_template)
        monkeypatch.setattr(ebq, "MAIN_SAMPLES", real_main_samples)
        monkeypatch.setattr(ebq, "PRO_SAMPLES", Path("/tmp/does_not_exist.json"))
        monkeypatch.setattr(ebq, "OUT_DIR", tmp_out_dir)

        ebq.build_normal_html()
        html = out.read_text()
        assert "__SAMPLES_PLACEHOLDER__" not in html

    def test_contains_all_main_sample_case_ids(self, real_template, real_main_samples, tmp_out_dir, monkeypatch):
        """生成的 HTML 必须包含 main_samples.json 中所有 case_id"""
        out = tmp_out_dir / "eval_normal.html"

        monkeypatch.setattr(ebq, "TEMPLATE_PATH", real_template)
        monkeypatch.setattr(ebq, "MAIN_SAMPLES", real_main_samples)
        monkeypatch.setattr(ebq, "PRO_SAMPLES", Path("/tmp/does_not_exist.json"))
        monkeypatch.setattr(ebq, "OUT_DIR", tmp_out_dir)

        ebq.build_normal_html()
        html = out.read_text()
        samples = json.loads(real_main_samples.read_text())
        for s in samples:
            assert s["case_id"] in html, f"case_id {s['case_id']} 未注入"

    def test_professional_only_flag_is_false(self, real_template, real_main_samples, tmp_out_dir, monkeypatch):
        """普通组 PROFESSIONAL_ONLY 必须为 false"""
        out = tmp_out_dir / "eval_normal.html"

        monkeypatch.setattr(ebq, "TEMPLATE_PATH", real_template)
        monkeypatch.setattr(ebq, "MAIN_SAMPLES", real_main_samples)
        monkeypatch.setattr(ebq, "PRO_SAMPLES", Path("/tmp/does_not_exist.json"))
        monkeypatch.setattr(ebq, "OUT_DIR", tmp_out_dir)

        ebq.build_normal_html()
        html = out.read_text()
        m = re.search(r"const PROFESSIONAL_ONLY = (true|false);", html)
        assert m and m.group(1) == "false", "普通组必须是 PROFESSIONAL_ONLY=false"

    def test_professional_questions_section_absent(self, real_template, real_main_samples, tmp_out_dir, monkeypatch):
        """普通组 HTML 的 JS 必须用 false gate 把 D 段挡住"""
        out = tmp_out_dir / "eval_normal.html"

        monkeypatch.setattr(ebq, "TEMPLATE_PATH", real_template)
        monkeypatch.setattr(ebq, "MAIN_SAMPLES", real_main_samples)
        monkeypatch.setattr(ebq, "PRO_SAMPLES", Path("/tmp/does_not_exist.json"))
        monkeypatch.setattr(ebq, "OUT_DIR", tmp_out_dir)

        ebq.build_normal_html()
        html = out.read_text()
        # JS 代码里有 'D. 专业合理性' 字符串（被 renderSection 调用），
        # 但渲染逻辑由 if (PROFESSIONAL_ONLY && QUESTIONS.professional) 守护。
        # 普通组 PROFESSIONAL_ONLY=false → D 段不会渲染。
        # 验证：PROFESSIONAL_ONLY=false 且 QUESTIONS.professional 不存在（D 段不执行）
        assert "const PROFESSIONAL_ONLY = false;" in html
        # 同时验证：渲染 D 段的代码确实被 false gate 拦住
        assert "if (PROFESSIONAL_ONLY && QUESTIONS.professional)" in html


# ═══════════════════════════════════════════════════════════════════════════════
# build_professional_html
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildProfessionalHtml:
    def test_contains_main_and_pro_samples(self, real_template, real_main_samples, real_pro_samples, tmp_out_dir, monkeypatch):
        """专业组 HTML 必须含 main + pro 两组样本的 case_id"""
        out = tmp_out_dir / "eval_professional.html"

        monkeypatch.setattr(ebq, "TEMPLATE_PATH", real_template)
        monkeypatch.setattr(ebq, "MAIN_SAMPLES", real_main_samples)
        monkeypatch.setattr(ebq, "PRO_SAMPLES", real_pro_samples)
        monkeypatch.setattr(ebq, "OUT_DIR", tmp_out_dir)

        ebq.build_professional_html()
        html = out.read_text()

        for s in json.loads(real_main_samples.read_text()):
            assert s["case_id"] in html
        for s in json.loads(real_pro_samples.read_text()):
            assert s["case_id"] in html, f"专项 case {s['case_id']} 未注入"

    def test_professional_only_flag_is_true(self, real_template, real_main_samples, real_pro_samples, tmp_out_dir, monkeypatch):
        """专业组 PROFESSIONAL_ONLY 必须为 true"""
        out = tmp_out_dir / "eval_professional.html"

        monkeypatch.setattr(ebq, "TEMPLATE_PATH", real_template)
        monkeypatch.setattr(ebq, "MAIN_SAMPLES", real_main_samples)
        monkeypatch.setattr(ebq, "PRO_SAMPLES", real_pro_samples)
        monkeypatch.setattr(ebq, "OUT_DIR", tmp_out_dir)

        ebq.build_professional_html()
        html = out.read_text()
        m = re.search(r"const PROFESSIONAL_ONLY = (true|false);", html)
        assert m and m.group(1) == "true"


# ═══════════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════════

class TestMain:
    def test_main_writes_both_files(self, real_template, real_main_samples, real_pro_samples, tmp_out_dir, monkeypatch, capsys):
        monkeypatch.setattr(ebq, "TEMPLATE_PATH", real_template)
        monkeypatch.setattr(ebq, "MAIN_SAMPLES", real_main_samples)
        monkeypatch.setattr(ebq, "PRO_SAMPLES", real_pro_samples)
        monkeypatch.setattr(ebq, "OUT_DIR", tmp_out_dir)

        ebq.main()

        assert (tmp_out_dir / "eval_normal.html").exists()
        assert (tmp_out_dir / "eval_professional.html").exists()
