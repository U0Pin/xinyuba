"""Phase 2 黄金快照测试：重跑捕获并逐字段 diff。"""

import json
import os

from tests.support.golden_v2_capture import capture


def test_golden_v2_snapshots_unchanged():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "snapshots.json"), encoding="utf-8") as f:
        expected = json.load(f)
    actual = capture()
    assert set(actual) == set(expected), "snapshot key set changed"
    for key in expected:
        assert actual[key] == expected[key], f"prompt drifted: {key}"
