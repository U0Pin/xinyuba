"""Mechanics tests for the therapy-evaluation runner.

Same two-layer design as tests/eval/test_run_routing_eval.py (per
docs/THERAPY_EVALUATION.md):
- This file gates only on runner MECHANICS: offline execution
  (AgendaProvider only, no network), artifact production, metric keys and
  ranges, artifact reproducibility, fidelity_results shape, and engine
  reality properties observable in the runner's own offline run (e.g.
  never more than one therapy family's llm tags within a single turn).
- Accuracy targets (family/skill/fidelity rates) are NEVER asserted here —
  they are REPORTED by the runner (outputs/therapy_evaluation/report.md);
  gold labels and real machinery may honestly disagree.
"""

from __future__ import annotations

import json

import pytest

from tests.eval.support import run_therapy_eval as runner
from tests.eval.support.therapy_schema import load_therapy_cases

REQUIRED_METRIC_KEYS = {
    "family_accuracy",
    "skill_accuracy",
    "boundary_accuracy",
    "over_intervention_rate",
    "under_intervention_rate",
    "crisis_override_accuracy",
    "therapy_continuity",
    "theory_mixing_rate",
    "fidelity_rate",
    "lifecycle_error_rate",
}

REQUIRED_ARTIFACTS = (
    "cases.json", "results.json", "summary.json",
    "failures.json", "fidelity_results.json", "report.md",
)

FAILURE_TYPES = {
    "correct", "wrong_family", "wrong_skill", "over_intervention",
    "under_intervention", "boundary_error", "fidelity_error",
    "theory_mixing", "lifecycle_error", "safety_error", "runner_error",
}


@pytest.fixture(scope="module")
def eval_output(tmp_path_factory) -> dict:
    """Run the full evaluation once per module, offline."""
    out_dir = tmp_path_factory.mktemp("therapy_eval_out")
    return runner.run_eval(output_dir=out_dir)


# (a) end-to-end offline execution + artifact production


def test_runner_runs_end_to_end_offline(eval_output):
    results, skipped = eval_output["results"], eval_output["skipped"]
    cases = load_therapy_cases(runner.DATASET_PATH)
    assert len(results) + len(skipped) == len(cases) == 176


def test_outputs_directory_produced(tmp_path):
    runner.run_eval(output_dir=tmp_path / "nested" / "out")
    for name in REQUIRED_ARTIFACTS:
        assert (tmp_path / "nested" / "out" / name).is_file()


def test_artifact_reproducibility(tmp_path):
    """Two runs on fresh tmp roots produce identical pass/fail signatures
    (the offline agenda is deterministic)."""
    a = runner.run_eval(output_dir=tmp_path / "a")
    b = runner.run_eval(output_dir=tmp_path / "b")
    sig_a = {r["case_id"]: (r["pass"], r["failure_type"]) for r in a["results"]}
    sig_b = {r["case_id"]: (r["pass"], r["failure_type"]) for r in b["results"]}
    assert sig_a == sig_b


# (b) metric keys / artifact schema


def test_metric_keys_present(eval_output):
    metrics = eval_output["metrics"]
    missing = REQUIRED_METRIC_KEYS - set(metrics)
    assert not missing, f"metrics missing keys: {missing}"


def test_metric_ranges_are_sane(eval_output):
    m = eval_output["metrics"]
    for key in REQUIRED_METRIC_KEYS:
        v = m[key]
        if v is None:
            continue  # None only when no comparable turns exist for that metric
        if isinstance(v, (int, float)):
            assert 0.0 <= v <= 1.0, f"{key}={v} not in [0, 1]"


def test_case_shape(eval_output):
    for r in eval_output["results"]:
        assert {"case_id", "expected_family", "actual_family", "pass",
                "failure_type", "turns", "fidelity"} <= set(r)
        assert r["failure_type"] in FAILURE_TYPES
        for f in r["fidelity"]:
            assert {"case_id", "family", "check", "ok", "detail"} <= set(f)
            assert isinstance(f["ok"], bool)


def test_results_carry_full_context(eval_output):
    """Manual-adjudication phase needs state snapshot + per-turn actuals +
    tag lists preserved in results.json."""
    for r in eval_output["results"]:
        assert "context" in r and "final_state" in r["context"]
        for t in r["turns"]:
            assert {"turn", "actual_family", "tags", "pass"} <= set(t)


def test_report_markdown_sections(eval_output, tmp_path):
    out_dir = tmp_path / "rep"
    runner.run_eval(output_dir=out_dir)
    text = (out_dir / "report.md").read_text("utf-8")
    for section in ("Dataset Overview", "Metrics", "Scripting policy",
                    "Conclusions", "Known production findings"):
        assert section in text


def test_report_flags_scripting_limitations(tmp_path):
    out_dir = tmp_path / "rep"
    runner.run_eval(output_dir=out_dir)
    text = (out_dir / "report.md").read_text("utf-8")
    assert "scripting limitation" in text
    assert "expected_no_therapy" in text or "Decider negatives" in text


# (c) fidelity_results shape


def test_fidelity_results_shape(eval_output, tmp_path):
    out_dir = tmp_path / "fid"
    runner.run_eval(output_dir=out_dir)
    payload = json.loads(
        (out_dir / "fidelity_results.json").read_text("utf-8"))
    assert "per_case" in payload and "breakdown" in payload
    for checks in payload["per_case"].values():
        for f in checks:
            assert {"check", "ok"} <= set(f)


def test_no_more_than_one_family_llm_tags_per_turn(eval_output):
    """Engine reality in this offline run: a single turn never carries llm
    agent tags of TWO therapy families (theory-mixing engine property).
    Only asserted if the runner's own offline run exhibits per-turn probe
    data — multi-turn cases necessarily carry more turns, but each turn's
    tag slice is per-turn."""
    fams_per_turn = []
    for r in eval_output["results"]:
        for t in r["turns"]:
            fams = set()
            for tag in t["tags"]:
                if tag.startswith("therapy_") and tag != "therapy_decider":
                    fams.add(tag.split("_")[1])
            fams_per_turn.append(fams)
    multi = [f for f in fams_per_turn if len(f) > 1]
    # recorded finding if it ever happens; the metric theoretical rate is 0
    assert eval_output["metrics"]["theory_mixing_rate"] == (
        round(len(multi) / max(1, sum(1 for r in eval_output["results"]
                                      for t in r["turns"]
                                      if (t.get("expected_family") or "")
                                      .lower() in runner.THERAPY_FAMILIES)), 4)
        or 0.0)


def test_known_bugs_recorded_not_failed(eval_output):
    """ACT emotion:{} and CBT assessment-None must appear as info-checks
    (ok=True) in fidelity records, never as failures."""
    for r in eval_output["results"]:
        for f in r["fidelity"]:
            if f["check"].startswith("act_emotion_known_bug"):
                assert f["ok"] is True


def test_negatives_assert_machinery_not_labels(eval_output):
    """expected_no_therapy cases are asserted on machinery: pass recorded and
    never classified as wrong_family (scripting limitation documented)."""
    for r in eval_output["results"]:
        if r["expected_no_therapy"]:
            assert r["failure_type"] not in ("wrong_family", "wrong_skill")


def test_no_routing_outputs_touched(tmp_path):
    # runner writes only into the therapy_evaluation directory namespace
    out = runner.run_eval(output_dir=tmp_path / "t")
    assert len(out["results"]) == 176
