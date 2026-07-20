"""Trace / Span 数据结构与 JSONL 序列化。

Trace 是一个 Trial（Case × 一次独立运行）的完整执行记录：
消息流、工具调用、耗时、Token/成本、各评分器 Verdict。
存储格式：runs/<run_id>/trials.jsonl，每行一个 Trace dict。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ToolCallRecord:
    """一次工具调用。blocked=True 表示被沙箱 authorizer 拦截的危险企图。"""
    name: str
    arguments: dict = field(default_factory=dict)
    result: str | None = None
    error: str | None = None
    duration_ms: float = 0.0
    blocked: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Trace:
    case_id: str
    split: str
    trial_index: int
    target: str
    model: str | None = None
    prompt_version: str | None = None
    started_at: str = ""
    duration_ms: float = 0.0
    steps: int = 0                       # Agent 循环步数（LLM 轮次或 Mock 动作数）
    messages: list[dict] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    final_answer: str = ""
    usage: dict = field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0})
    env_state: dict = field(default_factory=dict)
    infra_error: str | None = None       # 非 None 表示基础设施错误（E15）
    verdicts: dict[str, dict] = field(default_factory=dict)  # grader -> Verdict dict
    passed: bool = False

    @property
    def tool_errors(self) -> int:
        return sum(1 for tc in self.tool_calls if not tc.ok)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tool_errors"] = self.tool_errors
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Trace":
        d = dict(d)
        d.pop("tool_errors", None)
        d["tool_calls"] = [ToolCallRecord(**tc) for tc in d.get("tool_calls", [])]
        return cls(**d)


def append_trace_jsonl(path: str | Path, trace: Trace) -> None:
    """增量追加一条 Trace 到 trials.jsonl（运行中途失败也能保留现场）。"""
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")


def load_traces_jsonl(path: str | Path) -> list[Trace]:
    traces = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                traces.append(Trace.from_dict(json.loads(line)))
    return traces
