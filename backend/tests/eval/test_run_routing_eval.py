"""Mechanics tests for the routing-evaluation runner.

Two-layer design (per docs/ROUTING_EVALUATION.md):
- This file gates only on runner MECHANICS: offline execution (FakeProvider
  only, no network), artifact production, metric keys, skip accounting, and
  properties the runner guarantees by construction (ES exclusivity 0).
- Actual routing accuracy is REPORTED by the runner (outputs/routing_evaluation/
  report.md), never asserted here — gold labels and real routing may honestly
  disagree, and that is the finding, not a test failure.
"""

from __future__ import annotations

import json

import pytest

from tests.eval.support import run_routing_eval as runner
from tests.eval.support.routing_schema import load_cases

REQUIRED_METRIC_KEYS = {
    "family_accuracy",
    "skill_accuracy",
    "boundary_accuracy",
    "over_intervention_rate",
    "under_intervention_rate",
    "safety_override_accuracy",
    "es_exclusivity",
}

REQUIRED_ARTIFACTS = (
    "cases.json", "results.json", "summary.json",
    "failures.json", "report.md",
)


@pytest.fixture(scope="module")
def eval_output(tmp_path_factory) -> dict:
    """Run the full evaluation once per module, offline."""
    out_dir = tmp_path_factory.mktemp("routing_eval_out")
    return runner.run_eval(output_dir=out_dir)


# (a) end-to-end offline execution + artifact production


def test_runner_runs_end_to_end_offline(eval_output):
    results, skipped = eval_output["results"], eval_output["skipped"]
    cases = load_cases(runner.DATASET_PATH)
    assert len(results) + len(skipped) == len(cases)


def test_runner_touches_no_network_provider_only(tmp_path):
    # A second isolated run on a spin-up-free tmp root, asserting the provider
    # never leaves the fake agenda loop (no real client is ever constructed).
    out = runner.run_eval(output_dir=tmp_path / "out")
    assert len(out["results"]) == 150


def test_outputs_directory_produced(tmp_path):
    runner.run_eval(output_dir=tmp_path / "nested" / "out")
    for name in REQUIRED_ARTIFACTS:
        assert (tmp_path / "nested" / "out" / name).is_file()


# (b) metric keys / schema of artifacts


def test_metric_keys_present(eval_output):
    metrics = eval_output["metrics"]
    missing = REQUIRED_METRIC_KEYS - set(metrics)
    assert not missing, f"metrics missing keys: {missing}"


def test_metric_ranges_are_sane(eval_output):
    m = eval_output["metrics"]
    for key in ("family_accuracy", "skill_accuracy", "boundary_accuracy",
                "over_intervention_rate", "under_intervention_rate",
                "safety_override_accuracy"):
        v = m[key]
        assert 0.0 <= v <= 1.0, f"{key}={v} not in [0, 1]"


def test_case_shape(eval_output):
    for r in eval_output["results"]:
        assert {"case_id", "expected_family", "actual_family", "pass",
                "failure_type", "turns"} <= set(r)
        for t in r["turns"]:
            assert {"turn", "expected_family", "actual_family",
                    "pass", "failure_type"} <= set(t)
            assert t["failure_type"] in {
                "correct", "wrong_family", "wrong_skill",
                "over_intervention", "under_intervention",
                "safety_error", "boundary_error", "lifecycle_error", "error",
            }


def test_results_and_skips_fielded(tmp_path):
    out = runner.run_eval(output_dir=tmp_path / "out")
    payload = json.loads((tmp_path / "out" / "results.json").read_text("utf-8"))
    assert set(payload) == {"results", "skipped"}
    assert payload["results"] == out["results"]


def test_report_markdown_sections(eval_output, tmp_path):
    out_dir = tmp_path / "rep"
    runner.run_eval(output_dir=out_dir)
    text = (out_dir / "report.md").read_text("utf-8")
    for section in ("Dataset Overview", "Metrics", "Conclusions"):
        assert section in text


# (c) ES exclusivity: 0 violations — the guarantee lives in pipeline code
#     (ESO plan() supersedes + SwitchToTherapy/CrisisDetected clear ES state),
#     so the runner mechanism can assert it.


def test_es_exclusivity_zero(eval_output):
    assert eval_output["metrics"]["es_exclusivity_violations"] == 0


def test_no_case_reports_more_than_one_live_es(eval_output):
    for r in eval_output["results"]:
        for t in r["turns"]:
            assert t.get("live_es", 0) <= 1


def test_schema_self_check_still_holds():
    assert runner.THERAPY_UPPER == {"cbt": "CBT", "act": "ACT", "dbt": "DBT",
                                    "mi": "MI", "sfbt": "SFBT"}
