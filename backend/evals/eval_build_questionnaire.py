"""构建互动 HTML 问卷 — 注入样本数据。

生成两份：
  - 普通用户组：outputs/eval_questionnaire/eval_normal.html （10 段主样本，PROFESSIONAL_ONLY=false）
  - 专业用户组：outputs/eval_questionnaire/eval_professional.html （含 2 段专项 + 10 主样本，PROFESSIONAL_ONLY=true）

不修改模板文件（template.html），仅做字符串替换。
"""
import json
import sys
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = _PROJECT_DIR / "outputs" / "eval_questionnaire" / "template.html"
MAIN_SAMPLES = _PROJECT_DIR / "outputs" / "eval_samples" / "main_samples.json"
PRO_SAMPLES = _PROJECT_DIR / "outputs" / "eval_samples" / "professional_only_samples.json"
OUT_DIR = _PROJECT_DIR / "outputs" / "eval_questionnaire"


def _minify_samples(samples: list[dict]) -> str:
    """转成嵌入 HTML 的 JS 字符串。"""
    # 提取受测者阅读需要的字段
    minimal = []
    for s in samples:
        minimal.append({
            "case_id": s["case_id"],
            "target": s["target"],
            "actual": s["actual"],
            "quality": s["quality"],
            "scenario_label": s.get("scenario_label", ""),
            "scenario_description": s.get("scenario_description", ""),
            "decision_events": s.get("decision_events", []),
        })
    return json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))


def build_normal_html() -> Path:
    """普通用户组：仅 main samples。"""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    samples = json.loads(MAIN_SAMPLES.read_text(encoding="utf-8"))
    html = (
        template.replace("__SAMPLES_PLACEHOLDER__", _minify_samples(samples))
        .replace("const PROFESSIONAL_ONLY = false;", "const PROFESSIONAL_ONLY = false;")
    )
    out = OUT_DIR / "eval_normal.html"
    out.write_text(html, encoding="utf-8")
    return out


def build_professional_html() -> Path:
    """专业用户组：main + professional 合并，专业组可看全部 12 段。"""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    main_samples = json.loads(MAIN_SAMPLES.read_text(encoding="utf-8"))
    pro_samples = json.loads(PRO_SAMPLES.read_text(encoding="utf-8"))
    # 专业组看全部样本（含 2 段 HIGH_RISK 专项），共 12 段
    all_samples = main_samples + pro_samples
    html = (
        template.replace("__SAMPLES_PLACEHOLDER__", _minify_samples(all_samples))
        .replace("const PROFESSIONAL_ONLY = false;", "const PROFESSIONAL_ONLY = true;")
    )
    out = OUT_DIR / "eval_professional.html"
    out.write_text(html, encoding="utf-8")
    return out


def main():
    normal_out = build_normal_html()
    pro_out = build_professional_html()
    print(f"[build_questionnaire] → {normal_out}", file=sys.stderr)
    print(f"[build_questionnaire] → {pro_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
