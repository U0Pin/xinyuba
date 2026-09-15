"""Phase 1 测试：core/logging_utils.py。"""

import json

from src.core.logging_utils import LLMCallSink, app_log, new_trace_id
from src.utils.config import config


def test_new_trace_id_unique():
    ids = {new_trace_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(len(i) == 16 for i in ids)


def test_app_log_writes_structured_line(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    app_log("info", "test_module", "test_event", trace_id="trace-1", user_text="你好", n=1)
    path = tmp_path / "app.jsonl"
    assert path.exists()
    line = json.loads(path.read_text(encoding="utf-8").strip())
    assert line["level"] == "info"
    assert line["module"] == "test_module"
    assert line["event"] == "test_event"
    assert line["trace_id"] == "trace-1"
    assert line["user_text"] == "你好"
    assert line["n"] == 1
    assert "ts" in line


def test_app_log_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    app_log("info", "m", "e1")
    app_log("error", "m", "e2")
    lines = (tmp_path / "app.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[1])["event"] == "e2"


def test_llm_call_sink(tmp_path):
    sink = LLMCallSink(path=str(tmp_path / "llm_calls.jsonl"))
    sink.record({"request_id": "r1", "model": "deepseek-chat"})
    sink.record({"request_id": "r2"})
    lines = (tmp_path / "llm_calls.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert [json.loads(ln)["request_id"] for ln in lines] == ["r1", "r2"]


def test_now_iso_is_beijing_time():
    from src.core.logging_utils import now_iso
    ts = now_iso()
    assert ts.endswith("+08:00"), f"应为北京时间（UTC+8），实际：{ts}"
